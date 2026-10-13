"""GET /readyz (BACKEND.md §7): DB and critical dependencies reachable.

Acceptance criterion (mvp.md step 0.1, #4): Postgres stopped -> 503 with the exact
§3 error shape, and no connection string in the body. `conftest.py` points
DATABASE_URL at an address nothing is listening on, which produces the same failure
(a connection that never succeeds) as a stopped server does, without depending on
Compose being down for this particular test.

The healthy `200 {"data":{"status":"ready"}}` path (step 0.3 closed this gap: Docker
and a real Postgres now exist) pre-empts the app lifespan's own `db.init()` by calling
it directly with the real local URL before entering `TestClient`'s `with` block -
`db.init()` is a no-op once an engine already exists, so the route still runs through
the real app, just against a real database instead of the cached `Settings`
singleton's (dead) one.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.lib import db
from src.main import app
from tests.conftest import LIVE_DATABASE_URL

# Everything the real DATABASE_URL in conftest.py is built from. None of it may reach
# a caller per BACKEND.md §3/§4 ("never return ... internal names", "no connection
# string in the body").
LEAK_STRINGS = ["nobody", "59999", "127.0.0.1", "psycopg", "nothing", "Traceback", "password"]


def test_returns_503_with_the_dependency_unavailable_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "dependency_unavailable"
    assert body["error"]["message"] == "The database is not reachable. Try again in a moment."
    assert body["error"]["requestId"].startswith("req_")
    assert "fields" not in body["error"]
    assert set(body["error"]) == {"code", "message", "requestId"}


def test_error_body_never_leaks_connection_details() -> None:
    with TestClient(app) as client:
        response = client.get("/readyz")

    raw = json.dumps(response.json())
    for needle in LEAK_STRINGS:
        assert needle not in raw, f"{needle!r} leaked into the /readyz error body"


def test_response_header_request_id_matches_the_body() -> None:
    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.headers.get("x-request-id") == response.json()["error"]["requestId"]


def test_returns_200_when_the_database_is_reachable(require_live_db: None) -> None:
    del require_live_db
    db.init(LIVE_DATABASE_URL)

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ready"}}
