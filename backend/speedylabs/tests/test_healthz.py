"""GET /healthz (BACKEND.md §7): process is alive, touches no dependency.

Acceptance criterion (mvp.md step 0.1, #1): `GET /healthz` returns
`200 {"data":{"status":"ok"}}` with no DB touched. "No DB touched" is proven here in
the strict sense - not merely tolerating an unreachable database, but working when the
engine was *never initialised at all*. `TestClient(app)` used without the `with` form
never runs the app's lifespan, so `db.init()` is never called; the test asserts the
engine singleton is still unset both before and after the request.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.lib import db
from src.main import app


def test_returns_ok_without_ever_initialising_a_db_engine() -> None:
    assert db._engine is None  # pyright: ignore[reportPrivateUsage]

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok"}}
    engine_after = db._engine  # pyright: ignore[reportPrivateUsage]
    assert engine_after is None, "a 200 from /healthz must not require a DB engine"


def test_response_has_exactly_the_documented_shape() -> None:
    client = TestClient(app)
    response = client.get("/healthz")

    body = response.json()
    assert set(body) == {"data"}
    assert set(body["data"]) == {"status"}


def test_generates_a_req_prefixed_request_id_when_caller_sends_none() -> None:
    client = TestClient(app)
    response = client.get("/healthz")

    request_id = response.headers.get("x-request-id")
    assert request_id is not None
    assert request_id.startswith("req_")


def test_echoes_back_a_caller_supplied_request_id_unchanged() -> None:
    client = TestClient(app)
    response = client.get("/healthz", headers={"X-Request-Id": "req_from_caller_123"})

    assert response.headers.get("x-request-id") == "req_from_caller_123"
