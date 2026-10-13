"""A5: the project's headline claim, scored against the committed oracle - live, against the
real API and database.

Per `docs/plans/phase-13-attribution.md` step 13.4 (and the row-numbering convention,
`docs/progress.md`): the oracle numbers *data records* from 1, so for a CSV-sourced run the
comparable value is `canonical_lines.source_row_number - 1`; for fixed-width they are equal.
We only have a CSV demo fixture committed, so the CSV branch is exercised directly and the
fixed-width adjustment is asserted by a unit-level table test instead of a second live run.

Scoring, exactly the plan's corrected formula - *not* mvp.md's (which compares
`variances.root_cause` and divides by our own dollars; both measured broken in the plan):

    A5 = sum(oracle_amount(L) for L in oracle lines where our link's cause for L == oracle's
             cause for L) / oracle.totalSeededVarianceUsd

This file is tester-owned (named in the plan itself), because nobody else had written it:
`backend/tests/` contained zero files mentioning attribution, redistributor rates, the cutoff
day or variance_line_links before this suite.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import psycopg
import pytest

from tests.attribution_fixtures import (
    ADMIN_DB_URL,
    api_reachable,
    find_attributed_csv_run,
)

ORACLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "oracle"
    / "expected_variance_attribution.json"
)


class OracleCause(TypedDict):
    cause: str
    amountUsd: str
    lineCount: int
    rows: list[int]


class OracleDocument(TypedDict):
    totalSeededVarianceUsd: str
    seed: int
    causes: list[OracleCause]


def _claimed_causes_by_source_row(run_id: str) -> dict[int, set[str]]:
    """`source_row_number -> {causes that claimed it}`, non-residue links only. A `date_cutoff`
    line is named on two links (two variances) but always the *same* cause, so this collapses
    to one cause per row unless something is actually wrong."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select distinct l.source_row_number, k.root_cause
            from variance_line_links k
            join canonical_lines l on l.id = k.canonical_line_id
            where k.ingest_run_id = %s and k.root_cause <> 'unexplained'
            """,
            (run_id,),
        )
        out: dict[int, set[str]] = defaultdict(set)
        for row_number, cause in cur.fetchall():
            out[int(row_number)].add(str(cause))
        return out


@pytest.fixture(scope="module")
def oracle() -> OracleDocument:
    document: OracleDocument = json.loads(ORACLE_PATH.read_text())
    return document


@pytest.fixture(scope="module")
def attributed_run() -> str:
    if not api_reachable():
        pytest.skip("API is not reachable on :8000 - run `docker compose up -d`")
    run_id = find_attributed_csv_run()
    if run_id is None:
        pytest.skip(
            "No attributed CSV run found - run `make demo` and POST "
            "/v1/ingest-runs/{id}/variances/attribute against the demo run first"
        )
    return run_id


def test_a5_the_formula_scores_at_least_90_percent(
    attributed_run: str, oracle: OracleDocument
) -> None:
    """The acceptance criterion's own sentence, read literally: each of the 521 seeded lines
    counts once, weighted by the oracle's own dollars - not by ours, and not by the variance's
    dominant cause (mvp.md's two measured defects)."""
    claimed = _claimed_causes_by_source_row(attributed_run)
    total_oracle = Decimal(oracle["totalSeededVarianceUsd"])
    score = Decimal("0")
    per_cause_report: list[tuple[str, int, int]] = []
    for cause_block in oracle["causes"]:
        cause = cause_block["cause"]
        oracle_rows = cause_block["rows"]
        amount_per_row = Decimal(cause_block["amountUsd"]) / len(oracle_rows)
        matched_rows = 0
        for oracle_row in oracle_rows:
            our_row = oracle_row + 1  # CSV: oracle numbers data records from 1
            if cause in claimed.get(our_row, set()):
                score += amount_per_row
                matched_rows += 1
        per_cause_report.append((cause, len(oracle_rows), matched_rows))

    print(f"\nA5 = {score} / {total_oracle} = {score / total_oracle:.4f}")
    for cause, n, matched in per_cause_report:
        print(f"  {cause:35s} {matched}/{n} oracle lines scored correct")

    assert score / total_oracle >= Decimal("0.90")


def test_a5_compares_the_links_cause_not_the_variances_dominant_cause(
    attributed_run: str,
) -> None:
    """mvp.md's defect #1, pinned by the thing it would have broken: six of the eighteen demo
    variances have `root_cause='date_cutoff'` and also hold 85 links of other causes. Scoring
    those 85 lines against the *variance's* dominant cause marks a correct attribution wrong."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select v.root_cause, count(*)
            from variance_line_links k
            join variances v on v.id = k.variance_id
            where k.ingest_run_id = %s and v.root_cause = 'date_cutoff'
              and k.root_cause <> 'date_cutoff' and k.root_cause <> 'unexplained'
            group by v.root_cause
            """,
            (attributed_run,),
        )
        row = cur.fetchone()
    assert row is not None and row[1] > 0, (
        "expected at least one non-date_cutoff link inside a date_cutoff-dominant variance - "
        "if this is zero the demo data changed and this regression test needs new numbers"
    )


def test_oracle_row_sets_are_set_equal_in_both_directions(
    attributed_run: str, oracle: OracleDocument
) -> None:
    """Precision and recall both 1.0000 for every cause: no row claimed by two causes, no
    seeded row left unclaimed. Printed per cause so a regression names the broken rule."""
    claimed = _claimed_causes_by_source_row(attributed_run)
    all_ok = True
    for cause_block in oracle["causes"]:
        cause = cause_block["cause"]
        oracle_rows = {r + 1 for r in cause_block["rows"]}
        claimed_this_cause = {row for row, causes in claimed.items() if cause in causes}
        missing = oracle_rows - claimed_this_cause
        extra = claimed_this_cause - oracle_rows
        precision = (
            len(oracle_rows & claimed_this_cause) / len(claimed_this_cause)
            if claimed_this_cause
            else 0.0
        )
        recall = len(oracle_rows & claimed_this_cause) / len(oracle_rows) if oracle_rows else 0.0
        print(
            f"{cause:35s} oracle={len(oracle_rows):4d} claimed={len(claimed_this_cause):4d} "
            f"missing={len(missing):3d} extra={len(extra):3d} "
            f"precision={precision:.4f} recall={recall:.4f}"
        )
        if missing or extra:
            all_ok = False
    assert all_ok, "see printed per-cause report for the mismatching cause(s)"


def test_no_seeded_row_is_claimed_by_two_different_causes(attributed_run: str) -> None:
    """The oracle's five row sets are disjoint by construction. If attribution ever claims one
    line for two different causes (a rule-order regression), this is where it shows."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select l.source_row_number, array_agg(distinct k.root_cause)
            from variance_line_links k
            join canonical_lines l on l.id = k.canonical_line_id
            where k.ingest_run_id = %s and k.root_cause <> 'unexplained'
              and k.root_cause <> 'date_cutoff'
            group by l.source_row_number
            having count(distinct k.root_cause) > 1
            """,
            (attributed_run,),
        )
        rows = cur.fetchall()
    assert rows == [], f"rows claimed by more than one non-cutoff cause: {rows}"


def test_conservation_holds_for_every_variance_in_the_run(attributed_run: str) -> None:
    """The level conservation is actually asserted at - every row, not a sample. A5 cannot be
    gamed by dropping dollars because the score is computed on top of this invariant."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select v.id, v.variance_amount, coalesce(sum(k.contribution_amount), 0)
            from variances v
            left join variance_line_links k on k.variance_id = v.id
            where v.ingest_run_id = %s
            group by v.id, v.variance_amount
            having v.variance_amount <> coalesce(sum(k.contribution_amount), 0)
            """,
            (attributed_run,),
        )
        mismatches = cur.fetchall()
    assert mismatches == [], f"conservation violated on variances: {mismatches}"


def test_the_whole_file_net_total_does_not_equal_the_oracle_and_that_is_expected(
    attributed_run: str, oracle: OracleDocument
) -> None:
    """The naive whole-file check nobody should write: `date_cutoff` relocates money between
    periods rather than creating it, so it cancels in the net total. This test pins that the
    net total does not simply equal the oracle's magnitude total, so a future reader finds the
    relocation explanation attached to the number instead of filing it as a bug."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select coalesce(sum(variance_amount), 0) from variances where ingest_run_id = %s",
            (attributed_run,),
        )
        fetched = cur.fetchone()
        assert fetched is not None
        net_variance = Decimal(fetched[0])

    total_oracle = Decimal(oracle["totalSeededVarianceUsd"])
    gap = total_oracle - net_variance
    print(f"\nnet variance {net_variance}, oracle total {total_oracle}, gap {gap}")
    # The demo's net variance is the ERP run's own net, not necessarily seeded-cause-for-cause,
    # but the *relocating* cause is always the reason a net total cannot be compared to the
    # oracle's per-cause magnitude total directly.
    assert gap != 0, (
        "the net total unexpectedly equals the oracle total - if date_cutoff's lines changed "
        "shape this assertion needs revisiting, but on the committed demo it must not hold"
    )
