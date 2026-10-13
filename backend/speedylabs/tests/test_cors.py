"""mvp.md step 1.5: CORS is an exact origin list, never a wildcard with credentials.

Written by the tester - no existing test exercised the actual CORS middleware
behaviour (test_config.py only covers the boot-time wildcard refusal). Covers the
step's two Accept lines about live preflight behaviour: a disallowed origin gets no
`Access-Control-Allow-Origin`, and an allowed origin gets one with no
`Access-Control-Allow-Credentials` (none are used).

`conftest.py` sets `CORS_ALLOWED_ORIGINS` indirectly via `.env`/`os.environ`; the
app's default is `http://localhost:3000` (src/config.py), which is what the running
dev stack and `docker-compose.yml` actually use, so it is used here rather than
re-deriving it from `Settings` at import time (which would re-trigger the lru_cache
before this module sets anything).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.main import app

ALLOWED_ORIGIN = "http://localhost:3000"
DISALLOWED_ORIGIN = "https://evil.example"


def test_preflight_from_a_disallowed_origin_gets_no_allow_origin_header() -> None:
    client = TestClient(app)
    response = client.options(
        "/readyz",
        headers={
            "Origin": DISALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers.get("access-control-allow-origin") is None


def test_preflight_from_the_allowed_origin_is_allowed_with_no_credentials_header() -> None:
    client = TestClient(app)
    response = client.options(
        "/readyz",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert response.headers.get("access-control-allow-credentials") is None


def test_actual_response_from_a_disallowed_origin_gets_no_allow_origin_header() -> None:
    """The preflight is only half the story - the browser also checks the real
    response's own CORS headers, which CORSMiddleware sets independently."""
    client = TestClient(app)
    response = client.get("/healthz", headers={"Origin": DISALLOWED_ORIGIN})

    assert response.status_code == 200  # the server still answers; the browser blocks it
    assert response.headers.get("access-control-allow-origin") is None
