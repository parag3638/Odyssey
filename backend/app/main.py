from contextlib import asynccontextmanager
from time import perf_counter
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import health, accounts, orders, positions, bots, signals, activity, stocks, ai, dashboard, research
from app.timing import current_request_metrics, reset_request_metrics, start_request_metrics


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    if os.environ.get("ODYSSEY_DISABLE_SCHEDULER") != "1":
        from app.services.scheduler import start_scheduler
        start_scheduler()
    yield


app = FastAPI(title="Odyssey", lifespan=lifespan)


@app.middleware("http")
async def performance_headers(request, call_next):
    started = perf_counter()
    token = start_request_metrics()
    try:
        response = await call_next(request)
        metrics = current_request_metrics()
        total_ms = (perf_counter() - started) * 1000
        response.headers["Server-Timing"] = (
            f'app;dur={total_ms:.1f}, db;dur={metrics["db_ms"]:.1f};desc="{metrics["db_queries"]} queries"'
        )
        if request.method == "GET" and "cache-control" not in response.headers:
            if request.url.path.startswith("/stocks") or request.url.path == "/signals":
                response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300"
            else:
                response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        reset_request_metrics(token)


app.add_middleware(
    CORSMiddleware,
    # Any localhost/127.0.0.1 port — Next dev may land on 3000, 3001, … so we
    # can't hardcode a single port or the whole app shows "Failed to fetch".
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    # Deployed frontends: custom domain + Vercel production alias.
    allow_origins=[
        "https://odyssey.paragsingh.in",
        "https://odyssey-two-bay.vercel.app",
    ],
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(orders.router)
app.include_router(positions.router)
app.include_router(bots.router)
app.include_router(signals.router)
app.include_router(activity.router)
app.include_router(stocks.router)
app.include_router(ai.router)
app.include_router(dashboard.router)
app.include_router(research.router)
