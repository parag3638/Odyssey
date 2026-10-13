"""mvp.md step 1.5: security headers on every response.

Written by the tester. The builder's handover said these were verified manually
(curl, by hand) but no automated test exercises them, so a regression here would ship
silently. Covers the step's four Accept lines:

1. HSTS, nosniff, frame-deny and a CSP are present on any response.
2. `main.py` applies the header middleware first, so it should also appear on a 404
   (no route matched) and on a 503 (`/readyz` with the database unreachable, exercised
   via `conftest.py`'s dead `DATABASE_URL`) - not just on a plain 200.
3. The API's CSP is `default-src 'none'` per `src/lib/http_headers.py`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.main import app

REQUIRED_HEADERS = {
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
}


def test_security_headers_present_on_a_200() -> None:
    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    for name, value in REQUIRED_HEADERS.items():
        assert response.headers.get(name) == value, name
    assert response.headers.get("content-security-policy") is not None


def test_security_headers_present_on_a_404() -> None:
    client = TestClient(app)
    response = client.get("/this-route-does-not-exist")

    assert response.status_code == 404
    for name, value in REQUIRED_HEADERS.items():
        assert response.headers.get(name) == value, name
    assert response.headers.get("content-security-policy") is not None


def test_security_headers_present_on_a_503() -> None:
    # conftest.py's session-wide DATABASE_URL points nowhere, so /readyz always 503s
    # here unless a real Postgres happens to be reachable on 127.0.0.1:5432 too - use
    # the TestClient lifespan form like test_readyz.py does, so db.init() runs and the
    # route actually attempts (and fails) a connection rather than erroring earlier.
    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    for name, value in REQUIRED_HEADERS.items():
        assert response.headers.get(name) == value, name
    assert response.headers.get("content-security-policy") is not None


def test_api_csp_is_default_src_none() -> None:
    client = TestClient(app)
    response = client.get("/healthz")

    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
