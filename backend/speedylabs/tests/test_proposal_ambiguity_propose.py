"""The look-alike rule and the stored model ambiguities, through the propose service and route.

docs/plans/proposal-ambiguity-guard.md, steps 1 and 2. The rule itself is covered as a pure
function in `test_proposal_ambiguity_guard.py`; this file proves it is applied on every path -
fresh, cache hit, idempotent replay - and that the model's own ambiguity lines survive the cache.

Driven in-process against the real database with `llm.parse` stubbed, on a freshly uploaded
500-row `month1_invoices.csv` (the upload goes through the Compose stack, which parses and
profiles it; no model is involved). Each test names a model of its own (`stub_model`), so cache
keys never collide across tests, and nothing here can reach a provider.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from src.features.layouts import repo as layouts_repo
from src.features.mapping import prompt as prompt_builder
from src.features.mapping import propose
from src.features.mapping import repo as mapping_repo
from src.lib import db, llm
from tests import live, proposal_support
from tests.live import ADMIN_DB_URL, reset_rate_limits
from tests.live import db as admin_db
from tests.proposal_support import (
    GUARD_LINE,
    REASON_SENTENCE,
    in_process,
    profiled_file_id,
    proposal_from_cassette,
    run_with_database,
    stub_parse,
)

live_api = live.live_api
customer = live.customer
stub_model = proposal_support.stub_model

MODEL_LINE = "uom: UMCODE vs RSRVD1"


@pytest.fixture
def file_id(customer: str) -> UUID:
    reset_rate_limits()
    return profiled_file_id(customer)


async def _propose(
    file_id: UUID, *, key: str | None = None, ip: str = "ip-ambiguity"
) -> dict[str, Any]:
    async with db.session() as session:
        result = await propose.propose(
            session,
            file_id,
            upload_dir=Path("."),
            client_ip_hash=f"{ip}-{uuid4().hex[:6]}",
            idempotency_key=key,
        )
        # `propose()` returns with its last writes (the stored proposal, the draft merge, the
        # key's fulfilment) uncommitted, and the route does not commit either - see
        # `test_a_successful_proposal_is_stored_once_the_request_has_ended` below, which fails
        # because of exactly that. This helper commits so that the tests of the *rule* measure
        # the rule on a database that holds what the service meant to store.
        await session.commit()
        return result


def _admin_sql(statement: str, file_id: UUID) -> None:
    """Edit a stored proposal as the schema owner; the app role cannot (and should not)."""
    with admin_db(ADMIN_DB_URL) as conn:
        conn.execute(statement, (file_id,))  # pyright: ignore[reportArgumentType]


def _customer_id(response: dict[str, Any]) -> dict[str, Any]:
    entries: dict[str, Any] = response["entries"]
    return dict(entries["customer_id"])


async def _cache_key(session: db.AsyncSession, file_id: UUID, model: str) -> tuple[str, int]:
    layout = await layouts_repo.current_layout(session, file_id)
    assert layout is not None
    version = int(layout["version"])
    digest = (
        await session.execute(
            text("select sha256 from source_files where id = :id"), {"id": file_id}
        )
    ).scalar_one()
    key = prompt_builder.proposal_cache_key(
        file_digest=str(digest), layout_version=version, model=model
    )
    return key, version


async def _store_pre_change_row(
    session: db.AsyncSession, file_id: UUID, model: str, proposal: dict[str, Any]
) -> None:
    """A `mapping_proposals` row as migration 0032's predecessor would have written it: no
    `ambiguities` value, so the column's default (`[]`) is what reads back."""
    key, _ = await _cache_key(session, file_id, model)
    await session.execute(
        text(
            """
            insert into mapping_proposals (
                id, source_file_id, cache_key, canonical_schema_version, origin, model, effort,
                input_tokens, output_tokens, cached_input_tokens, cache_write_tokens, cost_usd,
                latency_ms, finish_reason, proposal, dropped_fields
            ) values (
                :id, :file_id, :key, '1.0.0', 'llm', :model, 'low',
                12000, 800, 0, 0, 0.038, 1234, 'ok', cast(:proposal as jsonb), '[]'::jsonb
            )
            """
        ),
        {
            "id": uuid4(),
            "file_id": file_id,
            "key": key,
            "model": model,
            "proposal": json.dumps(proposal),
        },
    )
    await session.commit()


def _stored(customer_id_confidence: str = "0.9", column: str = "BILTNO") -> dict[str, Any]:
    return {
        "customer_id": {
            "sourceColumn": column,
            "transform": {"kind": "trim"},
            "origin": "llm",
            "confidence": customer_id_confidence,
            "reason": "Six-digit account numbers.",
        }
    }


# --------------------------------------------------------------------------------------
# Fresh path
# --------------------------------------------------------------------------------------


def test_a_confident_biltno_comes_back_capped_explained_named_and_in_the_draft(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), calls))

    async def scenario() -> dict[str, Any]:
        response = await _propose(file_id)
        async with db.session() as session:
            layout = await layouts_repo.current_layout(session, file_id)
            assert layout is not None
            draft = await mapping_repo.draft(session, file_id, int(layout["version"]))
            assert draft is not None
            stored = (
                (
                    await session.execute(
                        text(
                            "select proposal, ambiguities from mapping_proposals "
                            "where source_file_id = :f"
                        ),
                        {"f": file_id},
                    )
                )
                .mappings()
                .one()
            )
        return {"response": response, "draft": dict(draft["mapping"]), "stored": dict(stored)}

    out = run_with_database(scenario)
    entry = _customer_id(out["response"])

    assert calls == [1]
    assert out["response"]["origin"] == "llm"
    assert entry["sourceColumn"] == "BILTNO"
    assert entry["confidence"] == "0.50"
    assert entry["reason"].endswith(REASON_SENTENCE)
    assert out["response"]["ambiguities"] == [GUARD_LINE]
    assert out["draft"]["customer_id"]["confidence"] == "0.50", "the draft carries the capped value"
    # The stored row is the model's output as the guard validated it - before the rule.
    assert out["stored"]["proposal"]["customer_id"]["confidence"] == "0.9"
    assert out["stored"]["ambiguities"] == []


def test_a_model_confidence_under_the_cap_stays_but_the_ambiguity_is_still_added(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        llm, "parse", stub_parse(proposal_from_cassette(customer_id_confidence=0.30), [])
    )

    response = run_with_database(lambda: _propose(file_id))

    assert _customer_id(response)["confidence"] == "0.3"
    assert response["ambiguities"] == [GUARD_LINE]


def test_fields_outside_the_party_fields_are_not_touched_by_the_rule(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = proposal_from_cassette()
    monkeypatch.setattr(llm, "parse", stub_parse(proposal, []))

    response = run_with_database(lambda: _propose(file_id))

    returned = {name: dict(entry) for name, entry in response["entries"].items()}
    for field in proposal.fields:
        if field.canonical_field in ("customer_id", "invoice_vendor"):
            continue
        assert returned[field.canonical_field]["confidence"] == f"{field.confidence:f}"
        assert "Lowered because" not in (returned[field.canonical_field]["reason"] or "")


# --------------------------------------------------------------------------------------
# Cache hit
# --------------------------------------------------------------------------------------


def test_a_row_stored_before_the_rule_existed_is_served_capped_and_named_at_zero_cost(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-0032 shape: customer_id at 0.9 and `ambiguities` reading back as `[]`. The rule
    runs on read, so the next request corrects it with no data migration and no model call."""
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), calls))

    async def scenario() -> dict[str, Any]:
        async with db.session() as session:
            await _store_pre_change_row(session, file_id, stub_model, _stored())
            stored_ambiguities = (
                await session.execute(
                    text("select ambiguities from mapping_proposals where source_file_id = :f"),
                    {"f": file_id},
                )
            ).scalar_one()
        assert stored_ambiguities == [], "the column default is what a pre-0032 row reads as"
        return await _propose(file_id)

    response = run_with_database(scenario)
    entry = _customer_id(response)

    assert calls == [], "served from the cache - the model was not asked"
    assert response["origin"] == "cache"
    assert response["spend"]["callCostUsd"] == "0"
    assert entry["confidence"] == "0.50"
    assert entry["reason"] == f"Six-digit account numbers. {REASON_SENTENCE}"
    assert response["ambiguities"] == [GUARD_LINE]


def test_a_cached_entry_naming_a_column_the_profile_no_longer_has_is_returned_as_stored(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), []))

    async def scenario() -> dict[str, Any]:
        async with db.session() as session:
            await _store_pre_change_row(session, file_id, stub_model, _stored(column="GONE"))
        return await _propose(file_id)

    response = run_with_database(scenario)

    assert response["origin"] == "cache"
    assert _customer_id(response)["confidence"] == "0.9"
    assert _customer_id(response)["reason"] == "Six-digit account numbers."
    assert response["ambiguities"] == []


# --------------------------------------------------------------------------------------
# The model's own ambiguities survive both cache paths
# --------------------------------------------------------------------------------------


def test_a_model_ambiguity_survives_the_cache_hit_and_the_idempotent_replay_in_order(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        llm,
        "parse",
        stub_parse(
            proposal_from_cassette(ambiguities=[MODEL_LINE, f"  {MODEL_LINE} ", " "]), calls
        ),
    )
    key = f"ambiguity-{uuid4()}"

    async def scenario() -> dict[str, Any]:
        first = await _propose(file_id, key=key)
        replay = await _propose(file_id, key=key)
        cache_hit = await _propose(file_id)
        async with db.session() as session:
            stored = (
                await session.execute(
                    text("select ambiguities from mapping_proposals where source_file_id = :f"),
                    {"f": file_id},
                )
            ).scalar_one()
        return {"first": first, "replay": replay, "cache_hit": cache_hit, "stored": stored}

    out = run_with_database(scenario)

    assert calls == [1], "one paid call; the replay and the cache hit cost nothing"
    assert out["first"]["origin"] == "llm"
    assert out["replay"]["origin"] == "cache"
    assert out["cache_hit"]["origin"] == "cache"
    assert out["first"]["ambiguities"] == [MODEL_LINE, GUARD_LINE]
    assert out["replay"]["ambiguities"] == [MODEL_LINE, GUARD_LINE]
    assert out["cache_hit"]["ambiguities"] == [MODEL_LINE, GUARD_LINE]
    # Only the model's own cleaned wording is stored - the guard's line is recomputed on read.
    assert out["stored"] == [MODEL_LINE]
    assert out["replay"]["spend"]["callCostUsd"] == "0"
    assert out["cache_hit"]["spend"]["callCostUsd"] == "0"


def test_an_idempotent_replay_returns_the_same_entries_as_the_first_response(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        llm, "parse", stub_parse(proposal_from_cassette(ambiguities=[MODEL_LINE]), [])
    )
    key = f"replay-{uuid4()}"

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        return await _propose(file_id, key=key), await _propose(file_id, key=key)

    first, replay = run_with_database(scenario)

    assert replay["entries"] == first["entries"]
    assert replay["ambiguities"] == first["ambiguities"]
    assert replay["dropped"] == first["dropped"]


def test_a_replayed_entry_naming_a_column_the_profile_no_longer_has_is_returned_as_stored(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replay branch loads the profile too; a stored entry that no longer matches it
    comes back untouched rather than raising."""
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), []))
    key = f"gone-{uuid4()}"

    async def scenario() -> dict[str, Any]:
        await _propose(file_id, key=key)
        _admin_sql(
            "update mapping_proposals set proposal = jsonb_set("
            "proposal, '{customer_id,sourceColumn}', '\"GONE\"') where source_file_id = %s",
            file_id,
        )
        return await _propose(file_id, key=key)

    replay = run_with_database(scenario)

    assert replay["origin"] == "cache"
    assert _customer_id(replay)["sourceColumn"] == "GONE"
    assert _customer_id(replay)["confidence"] == "0.9"


def test_a_row_whose_ambiguities_are_empty_is_served_with_the_guard_line_only(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-0032 rows read `[]`: no error, and the one line the file itself supports."""
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), []))
    key = f"empty-{uuid4()}"

    async def scenario() -> dict[str, Any]:
        await _propose(file_id, key=key)
        _admin_sql(
            "update mapping_proposals set ambiguities = '[]'::jsonb where source_file_id = %s",
            file_id,
        )
        return await _propose(file_id, key=key)

    assert run_with_database(scenario)["ambiguities"] == [GUARD_LINE]


# --------------------------------------------------------------------------------------
# Denial and conflict cases, unchanged (through the real route)
# --------------------------------------------------------------------------------------


def _post(path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async def work(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(path, headers=headers or {})

    return in_process(work)


def test_an_unknown_file_is_404_not_found_and_the_model_is_not_asked(
    customer: str, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del customer
    reset_rate_limits()
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), calls))

    response = _post(f"/v1/source-files/{uuid4()}/mapping/propose")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert calls == []


def test_one_key_reused_for_another_file_is_409_conflict_and_costs_one_call(
    customer: str, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_rate_limits()
    file_a = profiled_file_id(customer)
    file_b = profiled_file_id(customer)
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), calls))
    headers = {"Idempotency-Key": f"reuse-{uuid4()}"}

    first = _post(f"/v1/source-files/{file_a}/mapping/propose", headers)
    second = _post(f"/v1/source-files/{file_b}/mapping/propose", headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "conflict"
    assert calls == [1]


def test_a_file_with_a_materialised_run_is_409_mapping_locked_and_the_model_is_not_asked(
    customer: str, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_rate_limits()
    from tests.live import SAMPLE_CSV, upload_and_settle

    run = upload_and_settle(SAMPLE_CSV, customer_id=customer)
    with admin_db(ADMIN_DB_URL) as conn:
        conn.execute("update ingest_runs set materialized_at = now() where id = %s", (run["id"],))
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), calls))

    response = _post(f"/v1/source-files/{run['sourceFileId']}/mapping/propose")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "mapping_locked"
    assert calls == []


def test_the_route_returns_the_capped_entry_and_the_guard_line(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wire shape: `confidence` and `reason` per entry, `ambiguities` a list of strings."""
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), []))

    response = _post(f"/v1/source-files/{file_id}/mapping/propose")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    entry = next(item for item in data["proposals"] if item["canonicalField"] == "customer_id")
    assert entry["confidence"] == "0.50"
    assert entry["reason"].endswith(REASON_SENTENCE)
    assert data["ambiguities"] == [GUARD_LINE]
    assert data["origin"] == "llm"


# --------------------------------------------------------------------------------------
# Persistence through the route. These fail today: see the first docstring.
# --------------------------------------------------------------------------------------


def _count(sql: str, file_id: UUID) -> int:
    with admin_db(ADMIN_DB_URL) as conn:
        row = conn.execute(sql, (file_id,)).fetchone()  # pyright: ignore[reportArgumentType]
    assert row is not None
    return int(row[0])


def test_a_successful_proposal_is_stored_once_the_request_has_ended(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`propose()` ends with `insert_proposal`, `_apply_to_draft` and `idempotency.fulfil`, none
    of which commit, and `db.session()`'s contract is "callers own the transaction boundary" -
    the route does not commit, so the session's close rolls all three back. The response is a
    200 describing rows that do not exist. Same class of defect as the 2026-10-07 P11.5 entry in
    docs/decisions.md (an idempotency claim that was never committed).

    Asserted from a second connection, as the schema owner, after the request has returned."""
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), []))

    response = _post(f"/v1/source-files/{file_id}/mapping/propose")

    assert response.status_code == 200, response.text
    assert _count("select count(*) from mapping_proposals where source_file_id = %s", file_id) == 1
    assert _count("select count(*) from mapping_drafts where source_file_id = %s", file_id) == 1


def test_asking_twice_through_the_route_is_one_paid_call(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo property (propose.py's docstring, point 1): a repeat proposal costs nothing."""
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), calls))
    url = f"/v1/source-files/{file_id}/mapping/propose"

    first = _post(url)
    second = _post(url)

    assert first.status_code == second.status_code == 200
    assert second.json()["data"]["origin"] == "cache"
    assert calls == [1], "the second request paid for another model call"


def test_repeating_an_idempotency_key_through_the_route_does_not_pay_again(
    file_id: UUID, stub_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", stub_parse(proposal_from_cassette(), calls))
    url = f"/v1/source-files/{file_id}/mapping/propose"
    headers = {"Idempotency-Key": f"route-replay-{uuid4()}"}

    first = _post(url, headers)
    second = _post(url, headers)

    assert first.status_code == second.status_code == 200
    assert calls == [1], (
        "BACKEND.md section 3: the same key must give the same result, not a new call"
    )
