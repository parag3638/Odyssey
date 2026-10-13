"""Security headers on every response.

`BACKEND.md` §4: "Any HTML or docs routes send security headers (HSTS, `nosniff`,
frame deny, CSP)." They are applied to every response rather than only the HTML ones,
because deciding per-route which responses deserve protection is how a route gets
missed.

The CSP is `default-src 'none'`, which is the right default for an API that returns
JSON and nothing else: it cannot load a script, a style or an image, so it should not
claim it may. FastAPI's interactive docs *do* need a CDN, so those two paths get their
own relaxed policy - and only outside production, where an unauthenticated public API
should not be publishing a live request console at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import Request, Response

#: Paths that render HTML and legitimately need external scripts and styles.
DOCS_PATHS: Final = ("/docs", "/redoc", "/docs/oauth2-redirect")

#: An API that serves only JSON needs nothing at all. `frame-ancestors 'none'` is the
#: modern equivalent of X-Frame-Options, which is kept alongside it for older browsers.
API_CSP: Final = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

#: Swagger UI pulls its bundle and stylesheet from jsDelivr.
DOCS_CSP: Final = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' https://fastapi.tiangolo.com data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)

#: Two years, with subdomains. Only meaningful over HTTPS; harmless over plain HTTP,
#: and setting it unconditionally means production cannot forget it.
HSTS: Final = "max-age=63072000; includeSubDomains"


def security_headers(path: str) -> dict[str, str]:
    """The headers for one response, chosen by path."""
    return {
        "Strict-Transport-Security": HSTS,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        # The API needs none of these, and saying so removes a class of attack surface
        # rather than leaving the browser's defaults in place.
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": DOCS_CSP if path.startswith(DOCS_PATHS) else API_CSP,
    }


async def add_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    for name, value in security_headers(request.url.path).items():
        response.headers.setdefault(name, value)
    return response
