"""Shared setup for the propose-path tests: a stubbed model, an in-process app, a profiled file.

Not a test module. `test_llm_propose.py` predates it and keeps its own copies of the first
three helpers, deliberately left alone (rewriting the scaffolding of a suite is a change to the
thing doing the measuring); the new suites import from here instead of making a third copy.

Nothing in here reaches a model. `stub_model` names a model that exists only in the environment
the test builds, and the tests replace `llm.parse` themselves.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from src import config
from src.features.mapping.pricing import TokenUsage
from src.features.mapping.proposal_schema import MappingProposal
from src.lib import db, llm
from tests.conftest import LIVE_DATABASE_URL

CASSETTE = Path(__file__).resolve().parent / "cassettes" / "mapping_proposal_csv.json"

#: What `flag_indistinguishable` writes for the demo file's one look-alike pair, verbatim from
#: docs/plans/proposal-ambiguity-guard.md. Kept as a literal so a wording change is a visible
#: edit to a test, not a silent one.
GUARD_LINE = (
    "customer_id: CUSTNO and BILTNO look the same in this file - same type, value shape, number "
    "of distinct values and share of blanks - so the file cannot say which is right. Check the "
    "distributor's record layout and confirm this field by hand."
)
REASON_SENTENCE = (
    "Lowered because CUSTNO looks the same as BILTNO in this file; only the distributor's "
    "record layout can decide."
)


def proposal_from_cassette(
    *,
    customer_id_confidence: float = 0.9,
    ambiguities: list[str] | None = None,
) -> MappingProposal:
    """The recorded proposal with `customer_id -> BILTNO` at the given confidence.

    The cassette ships `BILTNO` at 0.55 with the model naming the pair; the 2026-10-12 live call
    is `BILTNO` at 0.9 with no ambiguity, which is the case the guard exists for.
    """
    document = json.loads(CASSETTE.read_text())
    document["ambiguities"] = [] if ambiguities is None else ambiguities
    for field in document["fields"]:
        if field["canonical_field"] == "customer_id":
            field["source_column"] = "BILTNO"
            field["confidence"] = customer_id_confidence
    return MappingProposal.model_validate(document)


def llm_result(parsed: MappingProposal) -> llm.LlmResult:
    return llm.LlmResult(
        outcome="ok",
        parsed=parsed,
        # Deliberately tiny: the ledger these calls settle into is the real dev database's, shared
        # with the user's live spend figure, and a stub that "cost" 3.8 cents would eat the daily
        # cap after a few runs. At the stub model's prices this is about 0.035 of a cent.
        usage=TokenUsage(input_tokens=100, output_tokens=10),
        latency_ms=1_234,
        model="stub-model",
        effort="low",
    )


def stub_parse(parsed: MappingProposal, calls: list[int]) -> Callable[..., llm.LlmResult]:
    """`llm.parse` that records each call and answers with `parsed`."""

    def parse(**_: Any) -> llm.LlmResult:
        calls.append(1)
        return llm_result(parsed)

    return parse


@pytest.fixture
def stub_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Configure a model **unique to this test**, with real prices, so the ledger is exercised.

    Unique because the cache key is (file digest, layout version, model) and the same fixture
    bytes are uploaded by many tests: a shared model name lets one test's stored row answer
    another's cache lookup (or swallow its insert through `on conflict do nothing`).
    """
    model = f"stub-model-{uuid4().hex[:10]}"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", model)
    monkeypatch.setenv("OPENAI_PRICE_INPUT_PER_MTOK", "2.50")
    monkeypatch.setenv("OPENAI_PRICE_CACHED_INPUT_PER_MTOK", "0.25")
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_MTOK", "10.00")
    monkeypatch.setenv("LLM_DAILY_SPEND_CAP_USD", "5.00")
    config.load.cache_clear()
    yield model
    config.load.cache_clear()


def run_with_database[T](work: Callable[[], Awaitable[T]]) -> T:
    """Run a coroutine with the process-wide engine pointed at the real database."""

    async def scenario() -> T:
        db.init(LIVE_DATABASE_URL)
        try:
            return await work()
        finally:
            await db.dispose()

    return asyncio.run(scenario())


def in_process[T](work: Callable[[httpx.AsyncClient], Awaitable[T]]) -> T:
    """Run `work` against the real app and the real database, with no network hop.

    `db.init` runs first on purpose: `ASGITransport` does not run the app's lifespan, and
    `db.init` is a no-op once an engine exists - the pre-emption `test_llm_propose.py` uses.
    """

    async def with_client() -> T:
        from src.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
            return await work(client)

    return run_with_database(with_client)


def profiled_file_id(customer_id: str) -> UUID:
    """A freshly uploaded, parsed and profiled 500-row `month1_invoices.csv` (live stack)."""
    from tests.live import SAMPLE_CSV, upload_and_settle

    run = upload_and_settle(SAMPLE_CSV, customer_id=customer_id)
    return UUID(str(run["sourceFileId"]))
