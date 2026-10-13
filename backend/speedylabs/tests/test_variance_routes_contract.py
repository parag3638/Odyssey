"""Live, against `:8000`/`:5432`: the variance list/summary routes' contract (step 12.5) -
pagination, the server-side totals, cursor validation, and the cause/presence filters.

The exact-number checks reuse the committed demo customer (`demo-distributor`) rather than
building a synthetic 18-row comparison: those eighteen numbers are the phase's own acceptance
data (`docs/plans/phase-12-variances.md`, "The eighteen rows this phase must produce"), so
reproducing them against the real seeded run is a stronger proof than a synthetic restatement.

`demo_run_id` depends on the `seeded_demo` fixture (`tests/conftest.py`), which resets and fully
re-seeds the demo once per test session rather than assuming a previous `make demo` or test run
left it behind - see that fixture's docstring for why a leftover from an earlier run in this
session cannot be trusted. Never hardcoded, since a reseed changes every id (`docs/progress.md`'s
own rule for e2e).
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from tests.legacy_variance_fixtures import (
    ADMIN_DB_URL,
    API_BASE,
    db,
    legacy_csv_bytes,
    make_program,
    make_reference,
    setup_calculated_program,
    synthetic_customer,
    upload_and_wait_imported,
)
from tests.live import DEMO_SLUG, live_api, reset_rate_limits

__all__ = ["live_api", "synthetic_customer"]  # re-exported fixtures


def _demo_compared_run() -> str | None:
    """The demo ERP run that already carries a variance comparison, or `None` if the demo has
    not been seeded/compared in this environment."""
    with db(ADMIN_DB_URL) as conn:
        row = conn.execute(
            "select v.ingest_run_id from variances v "
            "join ingest_runs r on r.id = v.ingest_run_id "
            "join customers c on c.id = r.customer_id "
            "where c.slug = %s limit 1",
            (DEMO_SLUG,),
        ).fetchone()
    return None if row is None else str(row[0])


@pytest.fixture
def demo_run_id(seeded_demo: None) -> str:
    del seeded_demo
    run_id = _demo_compared_run()
    if run_id is None:
        # Defensive only: the seeded_demo fixture should already have made this true, or
        # skipped the test itself (reset disabled / stack unreachable) before this fixture ran.
        pytest.skip(
            "no demo run has a variance comparison yet even after the seeded_demo fixture ran "
            "- see its docstring in tests/conftest.py"
        )
    return run_id


# --- the committed eighteen rows, reproduced through the real API -------------------------------


def test_the_eighteen_demo_rows_reproduce_through_the_api(live_api: None, demo_run_id: str) -> None:
    del live_api
    run_id = demo_run_id
    rows = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances", params={"limit": 25}, timeout=10
    ).json()["data"]
    assert len(rows) == 18
    assert all(row["presence"] == "both" for row in rows)
    negatives = [row for row in rows if Decimal(row["varianceAmount"]["amount"]) < 0]
    assert len(negatives) == 2
    by_ref_period = {(row["externalRef"], row["periodStart"]): row for row in rows}
    cpc_march = by_ref_period[("PG-CPC-CS", "2026-03-01")]
    assert cpc_march["varianceAmount"]["amount"] == "-1003.50"
    nvf_march = by_ref_period[("PG-NVF-CS", "2026-03-01")]
    assert nvf_march["varianceAmount"]["amount"] == "-1748.75"
    blc_jan = by_ref_period[("PG-BLC-LB", "2026-01-01")]
    assert blc_jan["legacyAmount"]["amount"] == "8930.71"
    assert blc_jan["calculatedAmount"]["amount"] == "8506.04"
    assert blc_jan["varianceAmount"]["amount"] == "424.67"


def test_the_demo_summary_totals_match_the_plans_committed_numbers(
    live_api: None, demo_run_id: str
) -> None:
    del live_api
    run_id = demo_run_id
    summary = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variance-summary", timeout=10).json()[
        "data"
    ]
    assert summary["varianceCount"] == 18
    assert summary["legacyTotal"]["amount"] == "391683.16"
    assert summary["calculatedTotal"]["amount"] == "387648.02"
    assert summary["netVariance"]["amount"] == "4035.14"
    assert summary["absoluteVariance"]["amount"] == "9539.64"


def test_totals_are_server_side_not_a_sum_of_the_page(live_api: None, demo_run_id: str) -> None:
    """The bug eighteen rows in a page of 25 would hide: paging the list must not change the
    totals, because they are computed server-side over every row, not summed from what the
    client happened to fetch."""
    del live_api
    run_id = demo_run_id
    full = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variance-summary", timeout=10).json()[
        "data"
    ]
    small_page = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances", params={"limit": 2}, timeout=10
    ).json()
    assert len(small_page["data"]) == 2
    assert small_page["nextCursor"] is not None
    again = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variance-summary", timeout=10).json()[
        "data"
    ]
    assert again["legacyTotal"] == full["legacyTotal"]
    assert again["calculatedTotal"] == full["calculatedTotal"]
    assert again["netVariance"] == full["netVariance"]
    assert again["absoluteVariance"] == full["absoluteVariance"]


def test_cursor_pagination_covers_all_rows_with_no_duplicates(
    live_api: None, demo_run_id: str
) -> None:
    del live_api
    run_id = demo_run_id
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # 18 rows at limit=5 is at most 4 pages; generous bound against a bug
        params: dict[str, str | int] = {"limit": 5}
        if cursor is not None:
            params["cursor"] = cursor
        page = httpx.get(
            f"{API_BASE}/v1/ingest-runs/{run_id}/variances", params=params, timeout=10
        ).json()
        seen.extend(row["varianceId"] for row in page["data"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert len(seen) == 18
    assert len(set(seen)) == 18


def test_a_tampered_cursor_is_400_malformed_request(live_api: None, demo_run_id: str) -> None:
    del live_api
    run_id = demo_run_id
    resp = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
        params={"cursor": "not-a-real-cursor"},
        timeout=10,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "malformed_request"


def test_cause_filter_is_honoured_both_present_and_absent(live_api: None, demo_run_id: str) -> None:
    """Whether a given run's variances have been attributed is a property of *when this
    environment's demo was seeded*, not of this test - `finish_demo_seed` (P15 round-2) now
    drives every seeded run through attribution, so a freshly reset demo has zero variances
    left at the generic `unattributed` root_cause. Hardcoding either 18 (the original
    assumption here, true only pre-attribution) or 0 (true only post-attribution) would make
    this test describe one moment's seed state rather than the filter's own behaviour, which is
    what it is supposed to prove. So both expectations are computed from the unfiltered list -
    the same convention this file already uses for ids - rather than assumed.
    """
    del live_api
    run_id = demo_run_id
    all_rows = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances", params={"limit": 25}, timeout=10
    ).json()["data"]
    expected_unattributed = [row for row in all_rows if row["rootCause"] == "unattributed"]
    expected_present_cause = next(
        (row["rootCause"] for row in all_rows if row["rootCause"] != "unattributed"), None
    )
    absent_cause = next(
        cause
        for cause in (
            "case_each_conversion",
            "missing_catch_weight",
            "redistributor_misattribution",
            "duplicate_line",
            "date_cutoff",
        )
        if cause not in {row["rootCause"] for row in all_rows}
    )

    unattributed = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
        params={"cause": "unattributed"},
        timeout=10,
    ).json()
    assert len(unattributed["data"]) == len(expected_unattributed)

    absent = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
        params={"cause": absent_cause},
        timeout=10,
    ).json()
    assert absent["data"] == []
    assert absent["nextCursor"] is None

    if expected_present_cause is not None:
        present = httpx.get(
            f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
            params={"cause": expected_present_cause},
            timeout=10,
        ).json()
        assert len(present["data"]) > 0
        assert all(row["rootCause"] == expected_present_cause for row in present["data"])


def test_an_unknown_cause_is_400_listing_the_valid_values(live_api: None, demo_run_id: str) -> None:
    del live_api
    run_id = demo_run_id
    resp = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances", params={"cause": "nonsense"}, timeout=10
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "malformed_request"
    for cause in (
        "unattributed",
        "case_each_conversion",
        "missing_catch_weight",
        "redistributor_misattribution",
        "duplicate_line",
        "date_cutoff",
    ):
        assert cause in body["error"]["message"]


def test_an_unknown_run_is_404_on_every_variance_route(live_api: None) -> None:
    del live_api
    import uuid

    bogus = str(uuid.uuid4())
    for path in (
        f"/v1/ingest-runs/{bogus}/variances",
        f"/v1/ingest-runs/{bogus}/variance-summary",
    ):
        resp = httpx.get(f"{API_BASE}{path}", timeout=10)
        assert resp.status_code == 404, path
        assert resp.json()["error"]["code"] == "not_found"


def test_legacy_files_route_never_lists_an_unrelated_customers_file(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    resp = httpx.get(f"{API_BASE}/v1/customers/{synthetic_customer}/legacy-files", timeout=10)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


# --- documented gaps: the plan's own acceptance criteria for 12.5, pinned as failing -------------


def test_presence_filter_narrows_the_list(live_api: None, synthetic_customer: str) -> None:
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    make_reference(synthetic_customer, "Other Brand", brand_values=["Other Brand"])
    make_program(
        synthetic_customer,
        name="Other program",
        brand_manufacturer="Other Brand",
        external_ref="PG-OTHER-1",
    )
    # PG-TEST-1 matches the calculated side (both); PG-OTHER-1 has no calc row (legacy_only).
    content = legacy_csv_bytes(
        [("PG-TEST-1", "2026-01", "1.00"), ("PG-OTHER-1", "2026-01", "2.00")]
    )
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = httpx.post(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
        json={"legacySourceFileId": legacy["sourceFileId"]},
        headers={"Idempotency-Key": f"presence-{run_id}"},
        timeout=10,
    )
    assert resp.status_code == 202, resp.text
    import time

    time.sleep(1.5)

    legacy_only = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
        params={"presence": "legacy_only"},
        timeout=10,
    ).json()["data"]
    assert len(legacy_only) == 1
    assert legacy_only[0]["externalRef"] == "PG-OTHER-1"

    bad = httpx.get(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
        params={"presence": "nonsense"},
        timeout=10,
    )
    assert bad.status_code == 400


def test_legacy_lines_route_exists_and_lists_matched_and_unmatched_rows(
    live_api: None, synthetic_customer: str
) -> None:
    make_reference(synthetic_customer, "Bayline", brand_values=["Bayline"])
    make_program(
        synthetic_customer,
        name="Bayline program",
        brand_manufacturer="Bayline",
        external_ref="PG-TEST-1",
    )
    content = legacy_csv_bytes(
        [("PG-TEST-1", "2026-01", "10.00"), ("PG-XXX-CS", "2026-01", "5.00")]
    )
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    resp = httpx.get(
        f"{API_BASE}/v1/source-files/{legacy['sourceFileId']}/legacy-lines", timeout=10
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert len(rows) == 2
    assert {row["sourceRowNumber"] for row in rows} == {2, 3}

    unmatched_only = httpx.get(
        f"{API_BASE}/v1/source-files/{legacy['sourceFileId']}/legacy-lines",
        params={"match": "unmatched_program"},
        timeout=10,
    ).json()["data"]
    assert len(unmatched_only) == 1
    assert unmatched_only[0]["programExternalRef"] == "PG-XXX-CS"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MAJOR: step 12.5 specifies 'GET .../variance-summary on a run with no variance rows "
        '-> 404 with "No comparison has been run for this run yet. Choose a legacy export and '
        "compare.\" - the one place that state is reported, and what the UI's Empty is driven "
        "by.' The actual code path (`features/variance/service.summary`) raises "
        "`LegacyNotImported` when `variance_count == 0`, which is a 409 "
        "('legacy_not_imported') with the message 'This run has not been compared against a "
        "legacy export yet. Pick one and compare.' - a 409 using a code whose own docstring "
        "says it means 'a legacy export was asked for before it finished importing', reused "
        "here for an unrelated state ('nothing has been compared yet'). The frontend happens "
        "to still work because `variance-view.tsx` branches on *any* ApiError code rather than "
        "on the HTTP status, but the API contract itself does not match the plan and the "
        "semantics of the reused code are now wrong for one of its two callers."
    ),
)
def test_an_uncompared_run_is_404_not_409(live_api: None, synthetic_customer: str) -> None:
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variance-summary", timeout=10)
    assert resp.status_code == 404, resp.text
    assert "No comparison has been run" in resp.json()["error"]["message"]
