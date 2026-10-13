"""`GET /v1/ingest-runs/{id}/variance-summary/attribution` - the headline percentage shown on
screen, the cause breakdown, and the two filters that must not be conflated.

Live, against the real API and database, against whatever attributed CSV run exists (the demo
run, found rather than hardcoded - see `docs/progress.md` on hardcoded run ids).
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import psycopg
import pytest

from tests.attribution_fixtures import (
    ADMIN_DB_URL,
    API_BASE,
    api_reachable,
    find_attributed_csv_run,
)


@pytest.fixture(scope="module")
def attributed_run() -> str:
    if not api_reachable():
        pytest.skip("API is not reachable on :8000 - run `docker compose up -d`")
    run_id = find_attributed_csv_run()
    if run_id is None:
        pytest.skip("No attributed CSV run found - run `make demo` and attribute it first")
    return run_id


def _db_absolute_variance(run_id: str) -> Decimal:
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select coalesce(sum(abs(variance_amount)), 0) from variances where ingest_run_id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        assert row is not None
        return Decimal(row[0])


def _db_residue(run_id: str) -> Decimal:
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select coalesce(sum(abs(contribution_amount)), 0) from variance_line_links "
            "where ingest_run_id = %s and root_cause = 'unexplained'",
            (run_id,),
        )
        row = cur.fetchone()
        assert row is not None
        return Decimal(row[0])


def test_explained_pct_is_over_absolute_variance_not_over_sum_of_contributions(
    attributed_run: str,
) -> None:
    """`docs/plans/phase-13-attribution.md` ("The explained percentage is absolute dollars...")
    and `docs/decisions.md` (P13.7: "the bars are sized by absolute dollars... the same reason
    the percentage's denominator is absolute") both specify:

        explainedPct = (sum|variance_amount| - sum|residue|) / sum|variance_amount|

    with `sum|variance_amount|` being exactly the fourth totals card already on screen
    (`GET .../variance-summary`'s `absoluteVariance`). The plan explicitly rejects
    `sum|contribution|` as a denominator/numerator substitute, by name, because `date_cutoff`
    counts every relocated dollar twice (once leaving its period, once arriving in the next)
    and a percentage built on it moves when a rule re-describes the same dollars rather than
    when the explanation changes.

    Measured on the demo run: the route computes `explainedAmount` as
    `sum(abs(contribution_amount))` over every non-`unexplained` link, which double-counts
    `date_cutoff`'s ~254*2 links. `absoluteVariance` (9539.64) and `explainedAmount` (10965+)
    are returned side by side in the same payload with `explainedAmount > absoluteVariance` -
    which is already incoherent on its face, a total exceeding the total it is a part of."""
    resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{attributed_run}/variance-summary/attribution")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    absolute_variance = Decimal(data["absoluteVariance"]["amount"])
    explained_amount = Decimal(data["explainedAmount"]["amount"])
    unexplained_amount = Decimal(data["unexplainedAmount"]["amount"])
    reported_pct = Decimal(data["explainedPct"])

    db_absolute = _db_absolute_variance(attributed_run)
    db_residue = _db_residue(attributed_run)
    assert absolute_variance == db_absolute
    assert unexplained_amount == db_residue

    correct_pct = (db_absolute - db_residue) / db_absolute

    # The incoherence, stated plainly: explained dollars should never exceed the total variance
    # they are explaining a share of.
    assert explained_amount <= absolute_variance, (
        f"explainedAmount ({explained_amount}) exceeds absoluteVariance ({absolute_variance}) - "
        "the API is summing |contribution| across links (which double-counts date_cutoff's "
        "relocated dollars) instead of (absoluteVariance - unexplainedAmount)"
    )
    assert reported_pct == Decimal(f"{correct_pct:.4f}"), (
        f"explainedPct {reported_pct} does not match the plan's formula "
        f"(absoluteVariance - unexplainedAmount) / absoluteVariance = {correct_pct:.4f}. "
        "repo.py's _SUMMARY computes explained_amount as sum(abs(contribution_amount)) over "
        "non-unexplained links, which double-counts date_cutoff; it must instead be "
        "absolute_variance - unexplained_amount."
    )


def test_by_cause_is_grouped_on_the_link_not_on_variances_root_cause(attributed_run: str) -> None:
    """Measured in the plan: `duplicate_line` is the dominant cause of zero variances (so
    grouping on `variances.root_cause` would drop it from the chart) while appearing in
    fourteen. `byCause` must therefore include it with a nonzero amount."""
    resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{attributed_run}/variance-summary/attribution")
    data = resp.json()["data"]
    by_cause = {row["rootCause"]: row for row in data["byCause"]}
    assert "duplicate_line" in by_cause, "duplicate_line must appear in byCause"
    assert Decimal(by_cause["duplicate_line"]["absoluteAmount"]["amount"]) > 0

    # Confirm the premise: grouping on variances.root_cause really does lose it.
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from variances "
            "where ingest_run_id = %s and root_cause = 'duplicate_line'",
            (attributed_run,),
        )
        fetched = cur.fetchone()
        assert fetched is not None
        dominant_count = fetched[0]
    assert dominant_count == 0, (
        "duplicate_line is expected to dominate zero variances on the demo data - if this "
        "assertion fails the premise for byCause's link-based grouping needs re-checking"
    )


def test_unexplained_is_always_present_in_by_cause(attributed_run: str) -> None:
    resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{attributed_run}/variance-summary/attribution")
    data = resp.json()["data"]
    causes = {row["rootCause"] for row in data["byCause"]}
    assert "unexplained" in causes


def test_cause_filter_on_variances_list_differs_from_link_cause_filter(attributed_run: str) -> None:
    """P12's `?cause=` (dominant cause) and P13's `?linkCause=` (semi-join on links) ask
    different questions. Plan: `?cause=duplicate_line` returns 0, `?linkCause=duplicate_line`
    returns 14 on the demo."""
    by_dominant = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{attributed_run}/variances",
        params={"cause": "duplicate_line"},
    )
    assert by_dominant.status_code == 200, by_dominant.text
    assert by_dominant.json()["data"] == []

    by_link = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{attributed_run}/variances",
        params={"linkCause": "duplicate_line"},
    )
    assert by_link.status_code == 200, by_link.text
    assert len(by_link.json()["data"]) > 0, (
        "?linkCause=duplicate_line returned zero rows - either the filter is unimplemented or "
        "it is not semi-joining on variance_line_links.root_cause"
    )


def test_lines_route_rejects_a_variance_vocabulary_cause(attributed_run: str) -> None:
    """`unattributed` is a variance word, not a link word - the lines route's `?cause=` only
    accepts `LINK_CAUSES`."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select id from variances where ingest_run_id = %s limit 1", (attributed_run,))
        fetched = cur.fetchone()
        assert fetched is not None
        variance_id = fetched[0]
    resp = httpx.get(
        f"{API_BASE}/v1/variances/{variance_id}/lines", params={"cause": "unattributed"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "malformed_request"


def test_lines_route_rejects_an_unknown_cause(attributed_run: str) -> None:
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select id from variances where ingest_run_id = %s limit 1", (attributed_run,))
        fetched = cur.fetchone()
        assert fetched is not None
        variance_id = fetched[0]
    resp = httpx.get(f"{API_BASE}/v1/variances/{variance_id}/lines", params={"cause": "nonsense"})
    assert resp.status_code == 400, resp.text


def test_residue_link_has_no_canonical_line_in_the_lines_response(attributed_run: str) -> None:
    """Given the residue link, `canonicalLineId` is null and the line fields are absent."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select variance_id from variance_line_links "
            "where ingest_run_id = %s and root_cause = 'unexplained' limit 1",
            (attributed_run,),
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip("no unexplained residue link on this run")
    variance_id = row[0]
    resp = httpx.get(
        f"{API_BASE}/v1/variances/{variance_id}/lines", params={"cause": "unexplained"}
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["canonicalLineId"] is None
    assert rows[0]["invoiceNo"] is None
