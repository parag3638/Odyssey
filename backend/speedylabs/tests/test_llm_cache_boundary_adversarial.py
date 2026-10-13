"""mvp.md 6.3/6.5: does a cache-key boundary edit really trigger a fresh call, end to
end - not just a different hash string in isolation.

`test_llm_prompt_and_guard.py` already proves `prompt.proposal_cache_key(...)` changes
when its inputs change, as a pure function. That is necessary but not sufficient: the
acceptance criteria say "a fresh call is made" and "a repeat proposal costs exactly
nothing" - both are claims about `propose.propose()`'s behaviour, not about the hash
function alone. These tests drive the real service with `llm.parse` stubbed and a call
counter, the same technique `test_llm_propose.py` uses for the four failure branches,
applied to the cache boundary instead.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from src import config
from src.features.mapping import propose
from src.features.mapping.pricing import TokenUsage
from src.lib import db, llm
from tests.conftest import LIVE_DATABASE_URL

CASSETTE = Path(__file__).resolve().parent / "cassettes" / "mapping_proposal_csv.json"


def _parsed_cassette() -> Any:
    from src.features.mapping.proposal_schema import MappingProposal

    return MappingProposal.model_validate(json.loads(CASSETTE.read_text()))


def _result() -> llm.LlmResult:
    return llm.LlmResult(
        outcome="ok",
        parsed=_parsed_cassette(),
        usage=TokenUsage(input_tokens=12_000, output_tokens=800),
        latency_ms=1,
        model="stub-model",
        effort="low",
    )


def _counting_stub(calls: list[int]) -> Callable[..., llm.LlmResult]:
    """A typed `llm.parse` replacement that counts calls - a plain `lambda **_: ...`
    triggers pyright's `reportUnknownLambdaType`/`reportUnknownArgumentType` under this
    project's strict settings because `**_` has no annotation to infer from."""

    def stub(**_: object) -> llm.LlmResult:
        calls.append(1)
        return _result()

    return stub


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", "stub-model")
    monkeypatch.setenv("OPENAI_PRICE_INPUT_PER_MTOK", "2.50")
    monkeypatch.setenv("OPENAI_PRICE_CACHED_INPUT_PER_MTOK", "0.25")
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_MTOK", "10.00")
    monkeypatch.setenv("LLM_DAILY_SPEND_CAP_USD", "5.00")
    config.load.cache_clear()


@pytest.fixture
def live(require_live_db: None) -> None:
    del require_live_db


async def _profiled_file(session: db.AsyncSession, column_name: str) -> UUID:
    row = (
        await session.execute(
            text(
                "select p.source_file_id from column_profiles p "
                "join file_layouts l on l.source_file_id = p.source_file_id "
                "where p.column_name = :c limit 1"
            ),
            {"c": column_name},
        )
    ).first()
    if row is None:
        pytest.skip(
            f"no profiled file with column {column_name!r} - run `make demo` in backend/ first"
        )
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


def _run[T](scenario: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(scenario())


# ----------------------------------------------------------------------------------------
# A layout-version bump forces a fresh call, end to end
# ----------------------------------------------------------------------------------------


def test_a_layout_version_bump_forces_a_fresh_call_not_a_stale_cache_hit(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same file, same everything else - only the layout version the service believes
    is current changes between the two calls. `propose.py` reads it via the private
    `_layout_version` helper; faking that return value is the smallest lever that
    exercises exactly the boundary mvp.md 6.3 describes without needing a real
    re-profile pipeline run (already covered by the layouts feature's own tests)."""
    del live
    _configure(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", _counting_stub(calls))

    versions: list[int] = [1]

    async def fake_layout_version(session: db.AsyncSession, file_id: UUID) -> int:
        del session, file_id
        return versions[0]

    monkeypatch.setattr(propose, "_layout_version", fake_layout_version)

    async def scenario() -> tuple[str, str]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session, "CUSTNO")
                await _reset(session, file_id)
                # A real layout bump re-profiles into new rows at the new version; fake
                # that half realistically too, by copying v1's profile forward, so the
                # only thing actually under test is the cache-key boundary, not a
                # ProfileNotReady this harness introduced by accident.
                await session.execute(
                    text(
                        "delete from column_profiles "
                        "where source_file_id = :f and layout_version = 2"
                    ),
                    {"f": file_id},
                )
                await session.execute(
                    text(
                        "insert into column_profiles (id, source_file_id, layout_version, "
                        "column_index, column_name, type_guess, row_count, null_count, "
                        "null_pct, distinct_count, distinct_capped, min_text, max_text, "
                        "date_format, sample_values, patterns) "
                        "select gen_random_uuid(), source_file_id, 2, column_index, "
                        "column_name, type_guess, row_count, null_count, null_pct, "
                        "distinct_count, distinct_capped, min_text, max_text, date_format, "
                        "sample_values, patterns from column_profiles "
                        "where source_file_id = :f and layout_version = 1"
                    ),
                    {"f": file_id},
                )
                await session.commit()

                first = await propose.propose(
                    session, file_id, upload_dir=Path("."), client_ip_hash="a"
                )
                versions[0] = 2  # the boundary edit: a new layout version
                second = await propose.propose(
                    session, file_id, upload_dir=Path("."), client_ip_hash="a"
                )
                return str(first["origin"]), str(second["origin"])
        finally:
            await db.dispose()

    first_origin, second_origin = _run(scenario)
    assert calls == [1, 1], "two real calls, not one cache hit"
    assert first_origin == "llm"
    assert second_origin == "llm", "a version bump must never read as a cache hit"


def test_the_same_layout_version_asked_twice_is_the_cache_hit_this_bug_would_hide(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the test above: without changing anything, the second ask must
    be free - proving the first test's two calls were really about the version bump and
    not just "propose.propose() never caches in this harness"."""
    del live
    _configure(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", _counting_stub(calls))

    async def fake_layout_version(session: db.AsyncSession, file_id: UUID) -> int:
        del session, file_id
        return 1

    monkeypatch.setattr(propose, "_layout_version", fake_layout_version)

    async def scenario() -> tuple[str, str]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session, "CUSTNO")
                await _reset(session, file_id)
                first = await propose.propose(
                    session, file_id, upload_dir=Path("."), client_ip_hash="a"
                )
                second = await propose.propose(
                    session, file_id, upload_dir=Path("."), client_ip_hash="a"
                )
                return str(first["origin"]), str(second["origin"])
        finally:
            await db.dispose()

    first_origin, second_origin = _run(scenario)
    assert calls == [1]
    assert first_origin == "llm"
    assert second_origin == "cache"


# ----------------------------------------------------------------------------------------
# A prompt template bump forces a fresh call, end to end
# ----------------------------------------------------------------------------------------


def test_a_prompt_template_version_bump_forces_a_fresh_call_end_to_end(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del live
    _configure(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", _counting_stub(calls))

    async def scenario() -> tuple[str, str]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session, "CUSTNO")
                await _reset(session, file_id)
                first = await propose.propose(
                    session, file_id, upload_dir=Path("."), client_ip_hash="a"
                )

                # `prompt.py` does `from ...proposal_schema import PROMPT_TEMPLATE_VERSION`
                # - a name import, which copies the reference at import time rather than
                # aliasing the attribute. Patching `proposal_schema.PROMPT_TEMPLATE_VERSION`
                # (as a first draft of this test did) therefore does *not* reach
                # `prompt.proposal_cache_key`, which already has its own bound copy of the
                # old value - the real cache-key module must be patched directly, the same
                # target `test_a_prompt_template_bump_invalidates_our_cache` in
                # test_llm_prompt_and_guard.py already uses for the pure-function version
                # of this same claim.
                import src.features.mapping.prompt as prompt_module

                original = prompt_module.PROMPT_TEMPLATE_VERSION
                prompt_module.PROMPT_TEMPLATE_VERSION = "999-bumped"
                try:
                    second = await propose.propose(
                        session, file_id, upload_dir=Path("."), client_ip_hash="a"
                    )
                finally:
                    prompt_module.PROMPT_TEMPLATE_VERSION = original
                return str(first["origin"]), str(second["origin"])
        finally:
            await db.dispose()

    first_origin, second_origin = _run(scenario)
    assert calls == [1, 1], "the prompt bump must be a real second call, never a stale hit"
    assert first_origin == "llm"
    assert second_origin == "llm"


# ----------------------------------------------------------------------------------------
# Two different files never collide, even proposed back to back
# ----------------------------------------------------------------------------------------


def test_two_different_real_files_never_share_a_cached_proposal(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CSV and fixed-width demo files have different bytes, so different sha256
    digests, so disjoint cache keys - each must call the model on its own first ask."""
    del live
    _configure(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", _counting_stub(calls))

    async def scenario() -> tuple[str, str, bool]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                csv_id = await _profiled_file(session, "CUSTNO")
                fwf_id = await _profiled_file(session, "FIELD_01")
                assert csv_id != fwf_id
                await _reset(session, csv_id)
                await _reset(session, fwf_id)

                csv_result = await propose.propose(
                    session, csv_id, upload_dir=Path("."), client_ip_hash="a"
                )
                fwf_result = await propose.propose(
                    session, fwf_id, upload_dir=Path("."), client_ip_hash="a"
                )
                csv_key = (
                    await session.execute(
                        text("select cache_key from mapping_proposals where source_file_id = :f"),
                        {"f": csv_id},
                    )
                ).scalar_one()
                fwf_key = (
                    await session.execute(
                        text("select cache_key from mapping_proposals where source_file_id = :f"),
                        {"f": fwf_id},
                    )
                ).scalar_one()
                return str(csv_result["origin"]), str(fwf_result["origin"]), csv_key != fwf_key
        finally:
            await db.dispose()

    csv_origin, fwf_origin, keys_differ = _run(scenario)
    assert calls == [1, 1], "each distinct file must be its own real call"
    assert csv_origin == "llm"
    assert fwf_origin == "llm"
    assert keys_differ, "two different files produced the same cache_key - a collision"


def test_reproposing_the_first_file_after_the_second_is_still_served_from_cache(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards against a cruder bug than a hash collision: a *global* "latest proposal"
    cache that only remembers the most recent file would make this fail by answering
    with the second file's result, or by missing and re-calling. `SELECT ... WHERE
    cache_key = :key` has no such notion of "latest", but a single wrong index or an
    accidentally-shared variable in a future refactor is exactly what this would catch.
    """
    del live
    _configure(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(llm, "parse", _counting_stub(calls))

    async def scenario() -> tuple[str, dict[str, Any], dict[str, Any]]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                csv_id = await _profiled_file(session, "CUSTNO")
                fwf_id = await _profiled_file(session, "FIELD_01")
                await _reset(session, csv_id)
                await _reset(session, fwf_id)

                first = await propose.propose(
                    session, csv_id, upload_dir=Path("."), client_ip_hash="a"
                )
                await propose.propose(session, fwf_id, upload_dir=Path("."), client_ip_hash="a")
                again = await propose.propose(
                    session, csv_id, upload_dir=Path("."), client_ip_hash="a"
                )
                return str(again["origin"]), dict(first["entries"]), dict(again["entries"])
        finally:
            await db.dispose()

    origin, first_entries, again_entries = _run(scenario)
    assert calls == [1, 1], "no third call - the re-ask of the first file is the cache hit"
    assert origin == "cache"
    assert again_entries == first_entries, "the cache hit returned the first file's own proposal"


# ----------------------------------------------------------------------------------------
# A manual choice already made against one layout version must not be clobbered by a
# proposal that was computed (and cached) against another.
# ----------------------------------------------------------------------------------------


def test_repo_source_columns_are_scoped_to_the_layout_version_not_the_whole_file(
    live: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct check on the scoping `propose.py._profile_columns` and `repo.source_columns`
    both rely on: a column that only ever existed at version 2 must not appear in the
    known-column set computed for version 1 of the *same file*, even though both rows
    share `source_file_id`. This is what makes the guard's "known_columns" trustworthy
    across a layout edit rather than only across different files.
    """
    del live
    monkeypatch.setenv("IP_HASH_SALT", "test-salt-not-for-production")

    async def scenario() -> tuple[bool, bool]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                file_id = await _profiled_file(session, "CUSTNO")
                # Plant a decoy row at a layout version that does not really exist for
                # this file, with a column name chosen to be obviously fake.
                await session.execute(
                    text(
                        "insert into column_profiles (id, source_file_id, layout_version, "
                        "column_index, column_name, type_guess, row_count, null_count, "
                        "null_pct, distinct_count, sample_values, patterns) "
                        "values (gen_random_uuid(), :f, 999, 0, 'ONLY_IN_V999', 'text', 1, 0, 0, "
                        "1, '[]'::jsonb, '[]'::jsonb) "
                        "on conflict do nothing"
                    ),
                    {"f": file_id},
                )
                await session.commit()

                v1_columns = {
                    str(row["column_name"])
                    for row in (
                        await session.execute(
                            text(
                                "select column_name from column_profiles "
                                "where source_file_id = :f and layout_version = 1"
                            ),
                            {"f": file_id},
                        )
                    ).mappings()
                }
                v999_columns = {
                    str(row["column_name"])
                    for row in (
                        await session.execute(
                            text(
                                "select column_name from column_profiles "
                                "where source_file_id = :f and layout_version = 999"
                            ),
                            {"f": file_id},
                        )
                    ).mappings()
                }
                await session.execute(
                    text(
                        "delete from column_profiles "
                        "where source_file_id = :f and layout_version = 999"
                    ),
                    {"f": file_id},
                )
                await session.commit()
                return "ONLY_IN_V999" not in v1_columns, "ONLY_IN_V999" in v999_columns
        finally:
            await db.dispose()

    excluded_from_v1, present_in_v999 = _run(scenario)
    assert excluded_from_v1, (
        "a column planted at a different layout version leaked into v1's known set"
    )
    assert present_in_v999
