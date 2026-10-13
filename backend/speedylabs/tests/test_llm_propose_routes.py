"""mvp.md 6.5 - the propose route's actual HTTP contract.

`test_llm_propose.py`'s own module docstring says the denial paths "are also checked
through the live route in `test_llm_propose_routes.py`". Everything about `propose.propose()`
had only ever been exercised in-process, never through FastAPI's routing, error-handler mapping,
or a real HTTP response: the `503` error envelope's exact shape, and the fact that the route does
not 500 under concurrent load, were unverified claims about this specific route.

**These tests run the real app in-process, with the model forced unconfigured.** They used to
POST to the Compose stack on :8000 and assume it had no `OPENAI_API_KEY`. The key is now in
`.env`, the container reads it, and those requests became live paid calls (about $0.16 in one
`make check`; responses came back `origin: "llm"`). A test cannot change the configuration of a
process it only talks to, so the three tests that mean "unconfigured" now drive
`src.main.app` through `httpx.ASGITransport` - the same routing, middleware, exception handlers
and database, a real Postgres - under the `unconfigured_llm` fixture (`conftest.py`). Uploads
still go through the live stack (no model is involved in parsing and profiling), and the 404 test
still talks to it, because a file that does not exist is refused before any model is reached.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest

from tests import live
from tests.live import API_BASE, SAMPLE_CSV, reset_rate_limits, upload_and_settle
from tests.proposal_support import in_process

live_api = live.live_api
customer = live.customer


def _propose(
    file_id: str, *, idempotency_key: str | None = None, timeout: float = 35
) -> httpx.Response:
    """Against the Compose stack. Only for requests that cannot reach a model."""
    headers = {} if idempotency_key is None else {"Idempotency-Key": idempotency_key}
    return httpx.post(
        f"{API_BASE}/v1/source-files/{file_id}/mapping/propose", headers=headers, timeout=timeout
    )


def _propose_here(file_id: str, *, idempotency_key: str | None = None) -> httpx.Response:
    headers = {} if idempotency_key is None else {"Idempotency-Key": idempotency_key}

    async def work(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(f"/v1/source-files/{file_id}/mapping/propose", headers=headers)

    return in_process(work)


@pytest.fixture
def no_model(customer: str, unconfigured_llm: None) -> str:
    """A customer, with the model forced off and a full rate-limit budget.

    The in-process app counts against `rate_limits` in the shared dev database (10 proposals
    an hour per source address), so the budget is reset the way `test_llm_propose.py` does.
    """
    del unconfigured_llm
    reset_rate_limits()
    return customer


def _profiled_file(customer_id: str) -> str:
    run = upload_and_settle(SAMPLE_CSV, customer_id=customer_id)
    return str(run["sourceFileId"])


# --------------------------------------------------------------------------------------
# The basic contract: 404 for a file that is not yours/does not exist, and the real
# error envelope shape from BACKEND.md §3 on every response.
# --------------------------------------------------------------------------------------


def test_a_nonexistent_file_is_404_with_the_standard_error_envelope(customer: str) -> None:
    del customer
    response = _propose("00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404, response.text
    body = response.json()
    assert set(body.keys()) == {"error"}
    error = body["error"]
    assert error["code"] == "not_found"
    assert error.get("requestId")
    # §3: never a stack trace, SQL, file path or internal name in the body.
    text = response.text
    for forbidden in ("Traceback", "site-packages", "/app/", "sqlalchemy", "psycopg"):
        assert forbidden not in text, f"{forbidden!r} leaked into the error response"


def test_proposing_with_no_model_configured_is_a_clean_503_not_a_500(no_model: str) -> None:
    """No `OPENAI_API_KEY` (forced, whatever `.env` holds). The route must turn
    `DependencyUnavailable` into exactly the `BACKEND.md` §3 shape through FastAPI's real
    exception handling - unverified before this test, since every prior check of this branch
    called `propose.propose()` directly.

    The code and message are also what `frontend/e2e/mapping-propose-degraded.spec.ts` serves
    from its intercepted route; if either changes here, change the constants there."""
    file_id = _profiled_file(no_model)
    response = _propose_here(file_id)
    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "dependency_unavailable"
    assert error["message"].startswith("Mapping proposals are not configured")
    assert "hand" in error["message"], "every degraded message points at manual mapping"
    assert error.get("requestId")
    assert "Traceback" not in response.text


def test_the_503_response_carries_no_api_key_or_internal_detail(no_model: str) -> None:
    file_id = _profiled_file(no_model)
    response = _propose_here(file_id)
    assert response.status_code == 503, response.text
    body = response.text
    for forbidden in ("sk-", "OPENAI_API_KEY", "Bearer ", "openai.com"):
        assert forbidden not in body, f"{forbidden!r} leaked into a degraded response"


# --------------------------------------------------------------------------------------
# Concurrency at the route level: must not 500, must not deadlock.
# --------------------------------------------------------------------------------------


def test_two_concurrent_proposes_for_the_same_file_both_degrade_cleanly(no_model: str) -> None:
    """Both requests take the same cache-key advisory lock (`propose.py`'s
    `_LOCK_CACHE_KEY`), so this also proves that lock does not deadlock the route under
    concurrent requests through the routing layer, each on its own pooled connection."""
    file_id = _profiled_file(no_model)

    async def race(client: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response]:
        url = f"/v1/source-files/{file_id}/mapping/propose"
        return await asyncio.gather(client.post(url), client.post(url))

    first, second = in_process(race)
    assert first.status_code == 503, first.text
    assert second.status_code == 503, second.text


# --------------------------------------------------------------------------------------
# The `Idempotency-Key` header. These two tests were written to **document a gap** - the
# header was accepted by CORS and never read - and they said so explicitly: "if this now
# fails, `propose.py` has started passing an idempotency key through to the ledger -
# update this test (and the finding it documents) accordingly". It has, so they now
# assert the contract that exists: `lib/idempotency.py` plus migration 0012 bind a key to
# the request it was used for, and reusing one key for a *different* request is refused
# rather than silently answered with the first request's result.
# --------------------------------------------------------------------------------------


def test_one_key_reused_for_a_different_file_is_refused(no_model: str) -> None:
    """Reusing a key across two different files is a client bug, and must not be answered
    with the first file's proposal - which is exactly what a key that ignored its request
    would do. The second request is refused with `409 conflict` naming the reuse.

    Observable with no model configured: the key is claimed *before* the work, so the
    conflict is decided before the route reaches the 503 it would otherwise return.
    """
    file_a = _profiled_file(no_model)
    file_b = _profiled_file(no_model)
    # Fresh every run: idempotency rows outlive a test, and a fixed string would collide with
    # its own previous run against a different file - which is the very conflict being tested,
    # arriving a request too early.
    same_key = f"idem-key-reused-{uuid4()}"

    response_a = _propose_here(file_a, idempotency_key=same_key)
    response_b = _propose_here(file_b, idempotency_key=same_key)

    # File A's own request gets its own answer - here a 503, because no model is configured.
    assert response_a.status_code == 503, response_a.text
    assert response_b.status_code == 409, response_b.text
    error = response_b.json()["error"]
    assert error["code"] == "conflict"
    assert "Idempotency-Key" in error["message"]
    # And the same key repeated on its *own* file is not refused: it is the same request.
    again = _propose_here(file_a, idempotency_key=same_key)
    assert again.status_code == 503, again.text


def test_the_key_reaches_the_spend_ledger(customer: str) -> None:
    """The structural half of the fix, kept structural for the same reason it was written
    that way: with no model configured no reservation is ever taken, so there is no row to
    inspect. What can be asserted is that the ledger call now carries the key - the column
    `llm_spend_reservations.idempotency_key` has existed since migration 0008 and was never
    populated, which is how a spend entry became impossible to trace to the request that
    caused it.
    """
    del customer
    import inspect

    from src.features.mapping import propose as propose_module

    source = inspect.getsource(
        propose_module._call_with_one_truncation_retry  # pyright: ignore[reportPrivateUsage]
    )
    assert "idempotency_key=idempotency_key" in source, (
        "`propose.py` must pass the client's idempotency key through to `spend.reserve`, so a "
        "ledger row can be traced back to the request that caused it"
    )
