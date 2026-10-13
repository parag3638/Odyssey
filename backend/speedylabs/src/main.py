"""Application entry point: middleware, error handling, health routes.

Phase 0 mounts no features. The routes here are the two BACKEND.md §7 requires:
`/healthz` says the process is alive and touches nothing; `/readyz` says the database
and critical dependencies are reachable. Neither needs auth and neither returns secrets.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src import config
from src.features.attribution.routes import router as attribution_router
from src.features.canonical.routes import router as canonical_router
from src.features.customers.routes import router as customers_router
from src.features.dashboard.routes import router as dashboard_router
from src.features.demo.routes import router as demo_router
from src.features.drift.routes import router as drift_router
from src.features.layouts.routes import router as layouts_router
from src.features.mapping.routes import router as mapping_router
from src.features.profiling.routes import router as profiling_router
from src.features.readiness.routes import router as readiness_router
from src.features.rebates.routes import router as rebates_router
from src.features.runs.routes import router as runs_router
from src.features.specs.routes import router as specs_router
from src.features.uploads.routes import router as uploads_router
from src.features.variance.routes import router as variance_router
from src.jobs.tasks import close_queue
from src.lib import db, logger
from src.lib.errors import AppError, TooLarge
from src.lib.http_headers import add_security_headers
from src.lib.ids import uuid7

REQUEST_ID_HEADER = "X-Request-Id"


class HealthStatus(BaseModel):
    """The payload inside the §3 single-item envelope."""

    status: str


class HealthEnvelope(BaseModel):
    """`{"data": {"status": "ok"}}` - BACKEND.md §3's one-item shape.

    Declared as a model rather than returned as a bare dict so the OpenAPI document
    describes the response properly. A `dict[str, Any]` return annotation produces an
    empty-object schema, which generates a `{}` TypeScript type - the frontend then
    cannot read `data.status` and the generated types stop being worth generating. §2
    calls schemas "one source for validation, docs and frontend types"; this is what
    that costs in practice.
    """

    data: HealthStatus


log = logger.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Start and stop the one connection pool (BACKEND.md §6, §7)."""
    settings = config.load()
    logger.configure(settings.log_level)
    db.init(settings.database_url)

    # The queue's pool is opened lazily, on the first deferral - not here.
    #
    # §7 puts dependency reachability in `/readyz`, not in startup: an API that refuses
    # to boot because Postgres blipped cannot serve the endpoint that would explain why.
    # Opening it here eagerly also made every request-less test pay a connection attempt
    # against whatever URL was configured, which took the suite from 53s to 172s against
    # a 90s budget. Lazy opening costs nothing until a route actually needs the queue,
    # and `tasks.defer_parse` turns an unreachable one into a 503.
    log.info("startup", app_env=settings.app_env, llm_enabled=settings.llm_enabled)
    try:
        yield
    finally:
        await close_queue()
        await db.dispose()
        log.info("shutdown")


def error_body(
    code: str, message: str, request_id: str, fields: dict[str, str] | None
) -> dict[str, Any]:
    """The single error shape from BACKEND.md §3. Nothing else may invent one."""
    error: dict[str, Any] = {"code": code, "message": message, "requestId": request_id}
    if fields:
        error["fields"] = fields
    return {"error": error}


def create_app() -> FastAPI:
    settings = config.load()
    app = FastAPI(
        title="Speedy Onboard",
        version="0.1.0",
        lifespan=lifespan,
        # An unauthenticated public API should not publish a live request console.
        # Available locally and in CI, where it is genuinely useful.
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None if settings.app_env == "production" else "/redoc",
    )

    # Added first so it wraps everything, including CORS preflight responses and any
    # error the handler below produces - a 503 needs these headers as much as a 200.
    app.middleware("http")(add_security_headers)

    # §6: compress responses over 1 KB. A layout document with 200 fields, a 186-entry
    # blank histogram and three sample records is tens of kilobytes of highly repetitive
    # JSON, and it is re-read on every visit to the field editor.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER, "Idempotency-Key"],
    )

    @app.middleware("http")
    async def body_size_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        """§4: cap request body size. Uploads are excluded; they cap themselves.

        This existed only inside `POST /v1/uploads` until P4's `PATCH .../layout` added a
        second body-accepting route and did not inherit it - a 1.65 MB field list was
        accepted because every individual field in it was valid. A cap belongs in one
        place that every present and future route passes through, which is here.

        `Content-Length` only: a chunked request without it is not rejected here, because
        reading the stream to measure it is the resource exhaustion the cap exists to
        prevent. Starlette spools a large body to a temporary file before a handler sees
        it, so the real defence for that case is an ingress limit, which P17 owns.
        """
        if request.url.path.endswith("/v1/uploads"):
            # Uploads own their cap and their message, which speaks in megabytes and
            # tells the user to split the file by month. Measuring them here too would
            # pre-empt that with a worse message for no gain.
            return await call_next(request)

        declared = request.headers.get("content-length")
        limit = settings.max_body_bytes
        if declared and declared.isdigit() and int(declared) > limit:
            # Returned, not raised. Starlette's `ExceptionMiddleware` - which is what
            # runs `@app.exception_handler` - sits *inside* the user middleware stack, so
            # an `AppError` raised here would escape it and surface as a 500 with no
            # envelope. The §3 body is therefore built here, from the same helper the
            # handler uses, so the shape cannot drift.
            kilobytes = limit // 1024
            error = TooLarge(
                f"That request is larger than {kilobytes:,} KB. Send fewer fields, or "
                "upload the file instead."
            )
            request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid7().hex}"
            log.warning(
                "app_error",
                code=error.code,
                status=error.status,
                route=request.url.path,
                request_id=request_id,
                declared_bytes=int(declared),
            )
            return JSONResponse(
                status_code=error.status,
                content=error_body(error.code, error.message, request_id, None),
                headers={REQUEST_ID_HEADER: request_id},
            )
        return await call_next(request)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        """Every request carries a requestId, echoed back (BACKEND.md §8).

        Taken from the incoming header when present so a trace survives across hops,
        generated otherwise.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid7().hex}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """One handler turns every typed error into the §3 shape. Logged once, here."""
        request_id = getattr(request.state, "request_id", "req_unknown")
        log.warning(
            "app_error",
            code=exc.code,
            status=exc.status,
            route=request.url.path,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=exc.status,
            content=error_body(exc.code, exc.message, request_id, exc.fields),
            headers={REQUEST_ID_HEADER: request_id, **exc.headers},
        )

    app.include_router(uploads_router)
    app.include_router(runs_router)
    app.include_router(customers_router)
    app.include_router(profiling_router)
    app.include_router(layouts_router)
    app.include_router(canonical_router)
    app.include_router(mapping_router)
    app.include_router(readiness_router)
    app.include_router(specs_router)
    app.include_router(drift_router)
    app.include_router(rebates_router)
    app.include_router(variance_router)
    app.include_router(attribution_router)
    app.include_router(dashboard_router)
    app.include_router(demo_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """FastAPI's own validation failures, in §3's envelope.

        Without this they return FastAPI's `{"detail": [...]}`, which is a second error
        shape - and §3 says the envelope is always `{"error": {...}}`. A frontend that
        parses one shape would silently fail to show the other, which is how a user ends
        up staring at a form that refuses to submit and says nothing.

        Field names are mapped into `fields` so a form can mark the offending control,
        and the raw input is deliberately **not** echoed: it is attacker-controlled and
        has no business being reflected back into a page.
        """
        request_id = getattr(request.state, "request_id", "req_unknown")
        fields: dict[str, str] = {}
        for error in exc.errors():
            location = [str(part) for part in error.get("loc", ()) if part != "body"]
            fields[".".join(location) or "request"] = str(error.get("msg", "Invalid value."))
        log.warning(
            "validation_error", route=request.url.path, request_id=request_id, fields=list(fields)
        )
        return JSONResponse(
            status_code=400,
            content=error_body(
                "malformed_request",
                "Some values in that request are not valid. Check the highlighted fields.",
                request_id,
                fields,
            ),
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.get("/healthz")
    async def healthz() -> HealthEnvelope:
        """Process is alive. Touches no dependency, so it never fails for someone else."""
        return HealthEnvelope(data=HealthStatus(status="ok"))

    @app.get("/readyz")
    async def readyz() -> HealthEnvelope:
        """Database reachable. Raises `DependencyUnavailable` -> 503 via the handler."""
        await db.ping()
        return HealthEnvelope(data=HealthStatus(status="ready"))

    return app


app = create_app()
