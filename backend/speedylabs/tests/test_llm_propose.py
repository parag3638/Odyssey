"""mvp.md 6.5 - the propose service: our cache, the cap, and the four failure branches.

Driven in-process with `llm.parse` stubbed, because the interesting paths are the ones a
real model cannot be made to take on demand - a refusal, a truncation, a content filter -
and because a test that needed a paid call would not run in the gate at all. The *denial*
paths that do not need a model (no key, cap exhausted, locked mapping) are also checked
through the live route in `test_llm_propose_routes.py`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from src import config
from src.features.mapping import propose, spend
from src.features.mapping.pricing import TokenUsage
from src.lib import db, llm
from src.lib.errors import DependencyUnavailable, LlmProposalInvalid, SpendCapReached
from tests.conftest import LIVE_DATABASE_URL

CASSETTE = Path(__file__).resolve().parent / "cassettes" / "mapping_proposal_csv.json"
CAP = Decimal("5.00")


def _parsed_cassette() -> Any:
    from src.features.mapping.proposal_schema import MappingProposal

    return MappingProposal.model_validate(json.loads(CASSETTE.read_text()))


def _result(
    outcome: str = "ok",
    *,
    parsed: Any = None,
    input_tokens: int = 12_000,
    output_tokens: int = 800,
) -> llm.LlmResult:
    return llm.LlmResult(
        outcome=outcome,  # type: ignore[arg-type]
        parsed=parsed,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        latency_ms=1_234,
        model="stub-model",
        effort="low",
    )


def _stub_parse(
    result: llm.LlmResult, *, record: list[int] | None = None
) -> Callable[..., llm.LlmResult]:
    """`llm.parse` replaced by something whose type pyright can see.

    A `lambda **_:` cannot carry an annotation, so under strict mode every one of these
    monkeypatches was an unknown-typed argument. An inner `def` with `**_: Any` is the same
    stub with a signature.
    """

    def parse(**_: Any) -> llm.LlmResult:
        if record is not None:
            record.append(1)
        return result

    return parse


@pytest.fixture
def live(require_live_db: None) -> None:
    del require_live_db


async def _profiled_file(session: db.AsyncSession) -> UUID:
    """A file that has a layout and a profile **and the columns these tests map from**.

    `CUSTNO` alone is not enough, and `limit 1` with no ordering is worse: both month-2 fixtures
    also
    carry `CUSTNO`, the drifted one has `SHPQTY` where month 1 has `SHPUNT`, and an unordered pick
    therefore depends on whatever else happens to be in the database. That cost two gate runs - the
    tests failed on an arbitrarily chosen file rather than on anything in `propose`.

    So: require `SHPUNT`, which is the column `tests/mappings.CSV_MAPPING` cites for `qty` and
    the one
    that makes a file usable here, and order by the file's id so the choice is the same every run.
    """
    row = (
        await session.execute(
            text(
                "select p.source_file_id from column_profiles p "
                "join file_layouts l on l.source_file_id = p.source_file_id "
                "where p.column_name = 'SHPUNT' "
                "order by p.source_file_id limit 1"
            )
        )
    ).first()
    if row is None:
        pytest.skip("no profiled CSV in the database - run `make demo` in backend/ first")
    return UUID(str(row[0]))


async def _reset(session: db.AsyncSession, file_id: UUID) -> None:
    await session.execute(
        text("delete from mapping_proposals where source_file_id = :f"), {"f": file_id}
    )
    await session.execute(
        text("delete from mapping_drafts where source_file_id = :f"), {"f": file_id}
    )
    await session.execute(text("delete from llm_spend_reservations"))
    await session.execute(text("delete from llm_spend"))
    await session.execute(text("delete from rate_limits"))
    await session.commit()


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Point config at a model with real prices, so the ledger is exercised."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", "stub-model")
    monkeypatch.setenv("OPENAI_PRICE_INPUT_PER_MTOK", "2.50")
    monkeypatch.setenv("OPENAI_PRICE_CACHED_INPUT_PER_MTOK", "0.25")
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_MTOK", "10.00")
    monkeypatch.setenv("LLM_DAILY_SPEND_CAP_USD", "5.00")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    config.load.cache_clear()


def _run(scenario: Any) -> Any:
    return asyncio.run(scenario())


# --------------------------------------------------------------------------------------
# The happy path, and the cache that makes a repeat free
# --------------------------------------------------------------------------------------


def test_a_proposal_is_guarded_stored_and_merged_into_the_draft(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del live
    _configure(monkeypatch)
    calls: list[int] = []

    def stub(**kwargs: Any) -> llm.LlmResult:
        calls.append(1)
        return _result(parsed=_parsed_cassette())

    monkeypatch.setattr(llm, "parse", stub)

    async def scenario() -> dict[str, Any]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                first = await propose.propose(
                    session, file_id, upload_dir=Path("."), client_ip_hash="ip-a"
                )
                budget = await spend.budget(session, CAP)
                return {
                    "origin": first["origin"],
                    "entries": len(first["entries"]),
                    "dropped": len(first["dropped"]),
                    "ambiguities": len(first["ambiguities"]),
                    "settled": budget.settled_usd,
                    "held": budget.held_usd,
                    "spend": first["spend"],
                }
        finally:
            await db.dispose()

    outcome = _run(scenario)
    assert calls == [1]
    assert outcome["origin"] == "llm"
    assert outcome["entries"] == 16
    assert outcome["ambiguities"] == 2
    # 12,000 input at 2.50 + 800 output at 10.00 = 0.038 exactly.
    assert outcome["settled"] == Decimal("0.038000")
    assert outcome["held"] == Decimal("0"), "the reservation was settled, not left holding"
    assert outcome["spend"]["callCostUsd"] == "0.038000"
    assert outcome["spend"]["capUsd"] == "5.00"


def test_a_repeat_proposal_is_served_from_our_cache_at_zero_cost(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo property: asking twice for the same file costs exactly nothing."""
    del live
    _configure(monkeypatch)
    calls: list[int] = []

    def stub(**kwargs: Any) -> llm.LlmResult:
        calls.append(1)
        return _result(parsed=_parsed_cassette())

    monkeypatch.setattr(llm, "parse", stub)

    async def scenario() -> dict[str, Any]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                await propose.propose(session, file_id, upload_dir=Path("."), client_ip_hash="ip-b")
                after_first = await spend.budget(session, CAP)
                second = await propose.propose(
                    session, file_id, upload_dir=Path("."), client_ip_hash="ip-b"
                )
                after_second = await spend.budget(session, CAP)
                return {
                    "origin": second["origin"],
                    "entries": len(second["entries"]),
                    "cost": second["spend"]["callCostUsd"],
                    "settled_before": after_first.settled_usd,
                    "settled_after": after_second.settled_usd,
                }
        finally:
            await db.dispose()

    outcome = _run(scenario)
    assert calls == [1], "the model was called once, not twice"
    assert outcome["origin"] == "cache"
    assert outcome["entries"] == 16
    assert outcome["cost"] == "0"
    assert outcome["settled_after"] == outcome["settled_before"], "no additional spend"


def test_a_manual_choice_is_not_clobbered_by_a_later_proposal(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal is a suggestion. Asking for one after correcting two fields by hand must
    not silently undo the correction."""
    del live
    _configure(monkeypatch)
    monkeypatch.setattr(llm, "parse", _stub_parse(_result(parsed=_parsed_cassette())))

    async def scenario() -> dict[str, Any]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)

                from src.features.mapping import repo
                from src.lib.ids import uuid7

                await repo.merge_draft(
                    session,
                    file_id,
                    1,
                    draft_id=uuid7(),
                    canonical_schema_version="1.0.0",
                    patch={
                        "customer_id": {
                            "sourceColumn": "CUSTNO",
                            "transform": {"kind": "trim"},
                            "origin": "manual",
                            "confidence": None,
                            "reason": None,
                        }
                    },
                )
                await propose.propose(session, file_id, upload_dir=Path("."), client_ip_hash="ip-c")
                draft = await repo.draft(session, file_id, 1)
                assert draft is not None
                mapping: dict[str, Any] = dict(draft["mapping"])
                return {
                    "customer_id": mapping["customer_id"],
                    "invoice_no_origin": mapping["invoice_no"]["origin"],
                }
        finally:
            await db.dispose()

    outcome = _run(scenario)
    assert outcome["customer_id"]["sourceColumn"] == "CUSTNO", "the human's choice survived"
    assert outcome["customer_id"]["origin"] == "manual"
    assert outcome["invoice_no_origin"] == "llm", "the rest was filled in"


# --------------------------------------------------------------------------------------
# The four failure branches
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "expected_error", "expected_in_message"),
    [
        ("refused", DependencyUnavailable, "declined"),
        ("content_filtered", DependencyUnavailable, "sample values"),
    ],
)
def test_a_refusal_and_a_content_filter_each_say_what_happened(
    live: None,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    expected_error: type[Exception],
    expected_in_message: str,
) -> None:
    """Both are "no proposal", but only one is worth retrying, so the messages differ."""
    del live
    _configure(monkeypatch)
    monkeypatch.setattr(llm, "parse", _stub_parse(_result(outcome)))

    async def scenario() -> tuple[str, Decimal, Decimal]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                try:
                    await propose.propose(
                        session, file_id, upload_dir=Path("."), client_ip_hash="ip-d"
                    )
                except Exception as error:  # the branch under test
                    budget = await spend.budget(session, CAP)
                    return (
                        type(error).__name__ + ":" + str(error),
                        budget.settled_usd,
                        budget.held_usd,
                    )
                raise AssertionError("expected a failure")
        finally:
            await db.dispose()

    message, settled, held = _run(scenario)
    assert expected_error.__name__ in message
    assert expected_in_message in message
    assert "hand" in message, "every refusal points at the manual path"
    # We paid for the call even though its output was unusable: the ledger is honest.
    assert settled == Decimal("0.038000")
    assert held == Decimal("0"), "nothing left holding"


def test_a_truncation_raises_the_ceiling_once_and_then_gives_up(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blind retrying is wrong: the same prompt truncates the same way. So the one retry
    changes something, and a second truncation is reported as a fact about the file."""
    del live
    _configure(monkeypatch)
    ceilings: list[int] = []

    def stub(**kwargs: Any) -> llm.LlmResult:
        ceilings.append(int(kwargs["max_output_tokens"]))
        return _result("truncated")

    monkeypatch.setattr(llm, "parse", stub)

    async def scenario() -> str:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                try:
                    await propose.propose(
                        session, file_id, upload_dir=Path("."), client_ip_hash="ip-e"
                    )
                except LlmProposalInvalid as error:
                    return error.message
                raise AssertionError("expected LlmProposalInvalid")
        finally:
            await db.dispose()

    message = _run(scenario)
    assert ceilings == [propose.MAX_OUTPUT_TOKENS, propose.MAX_OUTPUT_TOKENS * 2], (
        "exactly one retry, at a raised ceiling"
    )
    assert "too many columns" in message
    assert "hand" in message


def test_a_truncation_that_succeeds_on_the_retry_is_a_success(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del live
    _configure(monkeypatch)
    attempts: list[int] = []

    def stub(**kwargs: Any) -> llm.LlmResult:
        attempts.append(int(kwargs["max_output_tokens"]))
        if len(attempts) == 1:
            return _result("truncated")
        return _result(parsed=_parsed_cassette())

    monkeypatch.setattr(llm, "parse", stub)

    async def scenario() -> tuple[str, Decimal]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                result = await propose.propose(
                    session, file_id, upload_dir=Path("."), client_ip_hash="ip-f"
                )
                budget = await spend.budget(session, CAP)
                return str(result["origin"]), budget.settled_usd
        finally:
            await db.dispose()

    origin, settled = _run(scenario)
    assert origin == "llm"
    assert len(attempts) == 2
    # Both attempts are paid for: 0.038 twice.
    assert settled == Decimal("0.076000")


def test_an_unreachable_provider_releases_the_reservation_untouched(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call that never happened must not burn budget."""
    del live
    _configure(monkeypatch)

    def stub(**kwargs: Any) -> llm.LlmResult:
        raise DependencyUnavailable("the provider is down")

    monkeypatch.setattr(llm, "parse", stub)

    async def scenario() -> tuple[Decimal, Decimal, int]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                with pytest.raises(DependencyUnavailable):
                    await propose.propose(
                        session, file_id, upload_dir=Path("."), client_ip_hash="ip-g"
                    )
                budget = await spend.budget(session, CAP)
                released = (
                    await session.execute(
                        text(
                            "select count(*) from llm_spend_reservations where status = 'released'"
                        )
                    )
                ).scalar_one()
                return budget.settled_usd, budget.available_usd, int(released)
        finally:
            await db.dispose()

    settled, available, released = _run(scenario)
    assert settled == Decimal("0")
    assert available == CAP, "the budget is fully restored"
    assert released == 1


def test_an_exhausted_budget_refuses_before_the_model_is_called(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del live
    _configure(monkeypatch, LLM_DAILY_SPEND_CAP_USD="0.000002")
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", _stub_parse(_result(parsed=_parsed_cassette()), record=calls))

    async def scenario() -> str:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                await session.execute(
                    text(
                        "insert into llm_spend (day, settled_usd, call_count, updated_at) "
                        "values (current_date, 0.000002, 1, now())"
                    )
                )
                await session.commit()
                try:
                    await propose.propose(
                        session, file_id, upload_dir=Path("."), client_ip_hash="ip-h"
                    )
                except SpendCapReached as error:
                    return error.headers.get("Retry-After", "")
                raise AssertionError("expected SpendCapReached")
        finally:
            await db.dispose()

    retry_after = _run(scenario)
    assert calls == [], "no API call is made when the budget is gone"
    assert int(retry_after) > 0, "Retry-After names when the budget resets"


def test_no_key_configured_degrades_rather_than_erroring(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole manual path works without the model, so this is a 503 with advice."""
    del live
    _configure(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config.load.cache_clear()

    async def scenario() -> str:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                try:
                    await propose.propose(
                        session, file_id, upload_dir=Path("."), client_ip_hash="ip-i"
                    )
                except DependencyUnavailable as error:
                    return error.message
                raise AssertionError("expected DependencyUnavailable")
        finally:
            await db.dispose()

    message = _run(scenario)
    assert "not configured" in message
    assert "by hand" in message


def test_the_rate_limit_refuses_the_eleventh_call_in_an_hour(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lower than the upload limit, and its message names proposals rather than uploads."""
    del live
    _configure(monkeypatch, RATE_LIMIT_LLM_PER_HOUR="3")
    monkeypatch.setattr(llm, "parse", _stub_parse(_result(parsed=_parsed_cassette())))

    async def scenario() -> tuple[int, str]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session)
                await _reset(session, file_id)
                from src.lib.errors import RateLimited

                succeeded = 0
                message = ""
                for _ in range(5):
                    try:
                        await propose.propose(
                            session, file_id, upload_dir=Path("."), client_ip_hash="ip-j"
                        )
                        succeeded += 1
                    except RateLimited as error:
                        message = error.message
                        break
                return succeeded, message
        finally:
            await db.dispose()

    succeeded, message = _run(scenario)
    assert succeeded == 3, f"the limit is 3 per hour; {succeeded} got through"
    assert "mapping proposals" in message, "the message names the action, not uploads"
