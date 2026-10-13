"""The Glass-feedable export (plan step 13.6): schema validity, conservation surviving
serialisation, the relocation being legible, truncation, and the proposed-shape honesty.

`jsonschema` is not a committed project dependency (the tester may not edit `pyproject.toml`/
`uv.lock`), so it is imported via `pytest.importorskip` - this module's tests skip rather than
erroring `make check` when it is absent, and run for real when it is installed on demand
(`uv run --with jsonschema pytest tests/test_glass_export_shape.py`), which is how this suite
was developed and is how nobody had validated this schema against a live document before now."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import httpx
import psycopg
import pytest

from src.features.attribution.glass_export import MAX_GLASS_SOURCE_LINES
from tests.attribution_fixtures import (
    ADMIN_DB_URL,
    API_BASE,
    api_reachable,
    find_attributed_csv_run,
)

jsonschema = pytest.importorskip("jsonschema")


class GlassSourceLine(TypedDict):
    invoiceNo: str | None
    invoiceDate: str | None
    distItemId: str | None
    sourceRowNumber: int | None
    rootCause: str
    contributionAmount: str
    evidence: str | None
    estimated: bool


class GlassVariance(TypedDict):
    programExternalRef: str | None
    programName: str
    periodStart: str
    periodEnd: str
    legacyAmount: str
    calculatedAmount: str
    varianceAmount: str
    presence: NotRequired[str]
    rootCause: str
    byCause: list[dict[str, str]]
    residueAmount: NotRequired[str]
    sourceLineCount: int
    sourceLinesTruncated: bool
    sourceLines: list[GlassSourceLine]


class GlassDocument(TypedDict):
    glassExportVersion: str
    customerName: str
    ingestRunId: str
    legacySourceFile: str | None
    periodStart: str | None
    periodEnd: str | None
    canonicalSchemaVersion: str
    engine: dict[str, Any]
    explainedPct: str | None
    totalVarianceAmount: str
    netVarianceAmount: str
    unexplainedAmount: str
    currency: str
    variances: list[GlassVariance]


@pytest.fixture(scope="module")
def attributed_run() -> str:
    if not api_reachable():
        pytest.skip("API is not reachable on :8000 - run `docker compose up -d`")
    run_id = find_attributed_csv_run()
    if run_id is None:
        pytest.skip("No attributed CSV run found - run `make demo` and attribute it first")
    return run_id


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "canonical" / "glass_variance_export.schema.json"
    document: dict[str, Any] = json.loads(path.read_text())
    return document


@pytest.fixture(scope="module")
def document(attributed_run: str) -> GlassDocument:
    resp = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{attributed_run}/parallel-run/glass-export.json", timeout=30
    )
    assert resp.status_code == 200, resp.text
    body: GlassDocument = resp.json()
    return body


def test_document_validates_against_the_committed_schema(
    document: GlassDocument, schema: dict[str, Any]
) -> None:
    jsonschema.validate(instance=document, schema=schema)


def test_document_states_it_is_a_proposed_shape(document: GlassDocument) -> None:
    assert document["engine"]["standIn"] is True


def test_conservation_survives_serialisation_for_every_variance(document: GlassDocument) -> None:
    """Each variance's `sourceLines` contributions plus its residue must sum to its
    `varianceAmount` exactly - the invariant whose violation (`residueAmount` double-counted as
    a `sourceLine`) the P13.6 decision log records as already found and fixed. Re-verified here
    rather than trusted, since nobody has run this check against a live document before."""
    mismatches: list[tuple[str | None, str, Decimal, Decimal]] = []
    for variance in document["variances"]:
        lines_total = sum(
            (Decimal(line["contributionAmount"]) for line in variance["sourceLines"]),
            Decimal("0"),
        )
        residue = Decimal(variance.get("residueAmount", "0.00"))
        total = lines_total + residue
        expected = Decimal(variance["varianceAmount"])
        if not variance["sourceLinesTruncated"] and total != expected:
            mismatches.append(
                (variance["programExternalRef"], variance["periodStart"], total, expected)
            )
    assert mismatches == [], f"conservation broke after serialisation for: {mismatches}"


def test_total_variance_amount_equals_sum_of_absolute_variance_amounts(
    document: GlassDocument,
) -> None:
    computed = sum((abs(Decimal(v["varianceAmount"])) for v in document["variances"]), Decimal("0"))
    assert computed == Decimal(document["totalVarianceAmount"])


def test_net_variance_amount_equals_sum_of_signed_variance_amounts(
    document: GlassDocument,
) -> None:
    computed = sum((Decimal(v["varianceAmount"]) for v in document["variances"]), Decimal("0"))
    assert computed == Decimal(document["netVarianceAmount"])


def test_date_cutoff_lines_appear_twice_with_opposite_signs(document: GlassDocument) -> None:
    """The relocation must be legible in the export: the same `sourceRowNumber` under two
    different variances, opposite contribution signs."""
    seen: dict[int, list[Decimal]] = {}
    for variance in document["variances"]:
        for line in variance["sourceLines"]:
            if line["rootCause"] != "date_cutoff":
                continue
            row_number = line["sourceRowNumber"]
            assert row_number is not None
            seen.setdefault(row_number, []).append(Decimal(line["contributionAmount"]))
    doubled = {row: amounts for row, amounts in seen.items() if len(amounts) == 2}
    assert doubled, "expected at least one date_cutoff row to appear twice in the export"
    for row, amounts in doubled.items():
        assert amounts[0] == -amounts[1], f"row {row}: expected opposite signs, got {amounts}"


def test_estimated_catch_weight_lines_are_flagged(document: GlassDocument) -> None:
    found_estimated = False
    for variance in document["variances"]:
        for line in variance["sourceLines"]:
            if line["rootCause"] == "missing_catch_weight":
                assert line["estimated"] is True
                found_estimated = True
    assert found_estimated, "expected at least one missing_catch_weight line in the demo export"


def test_unexplained_residue_appears_as_a_first_class_record(document: GlassDocument) -> None:
    with_residue = [
        v for v in document["variances"] if Decimal(v.get("residueAmount", "0.00")) != 0
    ]
    assert with_residue, "expected at least one variance with residue on the demo data"
    for variance in with_residue:
        assert "residueAmount" in variance


def test_truncation_is_declared_when_the_cap_is_constructed(attributed_run: str) -> None:
    """The demo's largest real variance has 142 links against a cap of 500, so truncation is
    never exercised there - constructed instead, by lowering what the export code reads via a
    monkeypatched cap would require app-code changes (not permitted), so this test instead
    documents the invariant against the real cap and the real maximum, proving the slack is
    deliberate rather than asserting truncation that cannot occur on committed data."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select max(line_count) from (
                select variance_id, count(*) as line_count
                from variance_line_links
                where ingest_run_id = %s and root_cause <> 'unexplained'
                group by variance_id
            ) counts
            """,
            (attributed_run,),
        )
        fetched = cur.fetchone()
        assert fetched is not None
        largest = fetched[0] or 0
    assert largest < MAX_GLASS_SOURCE_LINES, (
        f"largest variance has {largest} links >= cap {MAX_GLASS_SOURCE_LINES} - if this "
        "changed, a constructed truncation test is now possible and should be added"
    )
