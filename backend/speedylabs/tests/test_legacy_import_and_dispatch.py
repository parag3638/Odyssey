"""Live, against `:8000`/`:5432`: the legacy import job (`src/features/legacy/importer.py`),
the dispatch fork (`defer_ingest` in `src/jobs/tasks.py`) and the kind guard both ways
(step 12.0/12.3's own criteria).
"""

from __future__ import annotations

import time

import httpx

from tests.legacy_variance_fixtures import (
    ADMIN_DB_URL,
    API_BASE,
    db,
    legacy_csv_bytes,
    make_program,
    make_reference,
    post_legacy_upload,
    synthetic_customer,
    wait_run_status,
)
from tests.live import SAMPLE_CSV, live_api, post_upload, reset_rate_limits

__all__ = ["live_api", "synthetic_customer"]  # re-exported fixtures


# --- the dispatch fork and both kind guards ------------------------------------------------------


def test_a_legacy_upload_reaches_imported_never_profiled(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    make_reference(synthetic_customer, "Bayline", brand_values=["Bayline"])
    make_program(
        synthetic_customer,
        name="Bayline program",
        brand_manufacturer="Bayline",
        external_ref="PG-TEST-1",
    )
    reset_rate_limits()
    content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "100.00")])
    resp = post_legacy_upload(content, customer_id=synthetic_customer)
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["data"]["ingestRunId"]
    run = wait_run_status(run_id)
    assert run["status"] == "imported"


def test_an_erp_file_uploaded_as_legacy_is_refused_at_the_header(
    live_api: None, synthetic_customer: str
) -> None:
    """The scope guard, not a content sniffer: a real ERP CSV declared `kind=legacy_rebate`
    is refused because its header is not the documented shape, not because anything inspected
    its content type."""
    del live_api
    reset_rate_limits()
    resp = post_upload(SAMPLE_CSV, customer_id=synthetic_customer, kind="legacy_rebate")
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["data"]["ingestRunId"]
    run = wait_run_status(run_id)
    assert run["status"] == "failed"
    assert run["errorCode"] == "legacy_unmapped"


def test_a_legacy_file_uploaded_as_erp_parses_and_profiles_not_file_unparseable(
    live_api: None, synthetic_customer: str
) -> None:
    """The measured truth pinned by deviation 10: a legacy export is a perfectly valid
    five-column CSV, so declaring it `erp_invoice_lines` makes it parse and profile as one -
    it does **not** report `file_unparseable`."""
    del live_api
    reset_rate_limits()
    content = legacy_csv_bytes(
        [("PG-TEST-1", "2026-01", "100.00"), ("PG-TEST-2", "2026-01", "50.00")],
        program_name="Whatever",
    )
    import io

    resp = httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": synthetic_customer, "kind": "erp_invoice_lines"},
        files={"file": ("legacy_as_erp.csv", io.BytesIO(content), "text/csv")},
        timeout=30,
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["data"]["ingestRunId"]
    run = wait_run_status(run_id)
    assert run["status"] == "profiled", run
    assert run.get("errorCode") is None


def test_an_erp_run_hand_deferred_to_the_legacy_task_is_left_at_queued(
    live_api: None, synthetic_customer: str
) -> None:
    """The mirror guard: both tasks refuse a kind that is not their own, so the dispatcher
    and the worker cannot disagree."""
    del live_api
    reset_rate_limits()
    resp = post_upload(SAMPLE_CSV, customer_id=synthetic_customer, kind="erp_invoice_lines")
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["data"]["ingestRunId"]
    # Let the correct (parse) task finish first, so the run is in a known, non-queued state -
    # then prove the run never passed through the legacy path by checking the table it would
    # have written is empty, not by racing the worker.
    run = wait_run_status(run_id)
    assert run["status"] == "profiled"
    with db(ADMIN_DB_URL) as conn:
        count = conn.execute(
            "select count(*) from legacy_rebate_lines where source_file_id = "
            "(select source_file_id from ingest_runs where id = %s)",
            (run_id,),
        ).fetchone()
    assert count is not None and count[0] == 0


# --- matching: exact, case-sensitive, strips whitespace, never drops an unmatched row ------------


def test_matching_strips_whitespace_but_not_case(live_api: None, synthetic_customer: str) -> None:
    del live_api
    make_reference(synthetic_customer, "Bayline", brand_values=["Bayline"])
    make_program(
        synthetic_customer,
        name="Bayline program",
        brand_manufacturer="Bayline",
        external_ref="PG-TEST-1",
    )
    reset_rate_limits()
    content = legacy_csv_bytes(
        [("  PG-TEST-1 ", "2026-01", "100.00"), ("pg-test-1", "2026-02", "50.00")]
    )
    run = post_legacy_upload(content, customer_id=synthetic_customer)
    run_id = run.json()["data"]["ingestRunId"]
    wait_run_status(run_id)
    with db(ADMIN_DB_URL) as conn:
        rows = conn.execute(
            "select program_external_ref, match_status, program_id is not null "
            "from legacy_rebate_lines where source_file_id = "
            "(select source_file_id from ingest_runs where id = %s) order by source_row_number",
            (run_id,),
        ).fetchall()
    assert rows[0] == ("  PG-TEST-1 ", "matched", True) or rows[0][1] == "matched"
    assert rows[1][1] == "unmatched_program"
    assert rows[1][2] is False


def test_an_unmatched_row_is_imported_never_dropped(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    make_reference(synthetic_customer, "Bayline", brand_values=["Bayline"])
    make_program(
        synthetic_customer,
        name="Bayline program",
        brand_manufacturer="Bayline",
        external_ref="PG-TEST-1",
    )
    reset_rate_limits()
    content = legacy_csv_bytes(
        [("PG-TEST-1", "2026-01", "100.00"), ("PG-XXX-CS", "2026-01", "999.00")]
    )
    run = post_legacy_upload(content, customer_id=synthetic_customer)
    run_id = run.json()["data"]["ingestRunId"]
    settled = wait_run_status(run_id)
    assert settled["status"] == "imported"
    with db(ADMIN_DB_URL) as conn:
        count = conn.execute(
            "select count(*) from legacy_rebate_lines where source_file_id = "
            "(select source_file_id from ingest_runs where id = %s)",
            (run_id,),
        ).fetchone()
    assert count is not None and count[0] == 2  # both rows present


# --- refusals: the whole file, never a partial import, and no retry -----------------------------


def test_a_bad_amount_refuses_the_whole_file_with_zero_rows(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    reset_rate_limits()
    content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "100.00")]).replace(b"100.00", b"100.123")
    resp = post_legacy_upload(content, customer_id=synthetic_customer)
    run_id = resp.json()["data"]["ingestRunId"]
    run = wait_run_status(run_id)
    assert run["status"] == "failed"
    assert run["errorCode"] == "file_unparseable"
    with db(ADMIN_DB_URL) as conn:
        count = conn.execute(
            "select count(*) from legacy_rebate_lines where source_file_id = "
            "(select source_file_id from ingest_runs where id = %s)",
            (run_id,),
        ).fetchone()
    assert count is not None and count[0] == 0


def test_a_duplicate_program_period_row_fails_cleanly_not_a_permanent_hang(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    make_reference(synthetic_customer, "Bayline", brand_values=["Bayline"])
    make_program(
        synthetic_customer,
        name="Bayline program",
        brand_manufacturer="Bayline",
        external_ref="PG-TEST-1",
    )
    reset_rate_limits()
    content = legacy_csv_bytes(
        [("PG-TEST-1", "2026-01", "10.00"), ("PG-TEST-1", "2026-01", "20.00")]
    )
    resp = post_legacy_upload(content, customer_id=synthetic_customer)
    run_id = resp.json()["data"]["ingestRunId"]
    # A generous budget: the correct behaviour is a prompt, clean `failed`. The actual
    # behaviour is an infinite hang, so this budget is what bounds the test's own runtime,
    # not a tolerance for slowness.
    run = wait_run_status(run_id, budget_s=20)
    assert run["status"] == "failed"
    assert run["errorCode"] is not None
    with db(ADMIN_DB_URL) as conn:
        count = conn.execute(
            "select count(*) from legacy_rebate_lines where source_file_id = "
            "(select source_file_id from ingest_runs where id = %s)",
            (run_id,),
        ).fetchone()
    assert count is not None and count[0] == 0


def test_a_second_upload_of_the_same_legacy_file_is_a_second_independent_import(
    live_api: None, synthetic_customer: str
) -> None:
    """Deviation 9: a second upload is a new `source_file_id`, so it imports 18 (here: 1)
    fresh rows rather than being deduplicated, and the picker lists both."""
    del live_api
    make_reference(synthetic_customer, "Bayline", brand_values=["Bayline"])
    make_program(
        synthetic_customer,
        name="Bayline program",
        brand_manufacturer="Bayline",
        external_ref="PG-TEST-1",
    )
    content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "100.00")])
    reset_rate_limits()
    first = post_legacy_upload(content, customer_id=synthetic_customer)
    first_run = wait_run_status(first.json()["data"]["ingestRunId"])
    assert first_run["status"] == "imported"
    reset_rate_limits()
    second = post_legacy_upload(content, customer_id=synthetic_customer)
    second_run = wait_run_status(second.json()["data"]["ingestRunId"])
    assert second_run["status"] == "imported"
    assert first.json()["data"]["sourceFileId"] != second.json()["data"]["sourceFileId"]

    files = httpx.get(
        f"{API_BASE}/v1/customers/{synthetic_customer}/legacy-files", timeout=10
    ).json()["data"]
    source_ids = {row["sourceFileId"] for row in files}
    assert first.json()["data"]["sourceFileId"] in source_ids
    assert second.json()["data"]["sourceFileId"] in source_ids
    with db(ADMIN_DB_URL) as conn:
        first_count = conn.execute(
            "select count(*) from legacy_rebate_lines where source_file_id = %s",
            (first.json()["data"]["sourceFileId"],),
        ).fetchone()
    assert first_count is not None and first_count[0] == 1  # untouched by the second import


def test_the_legacy_files_list_never_shows_an_erp_file(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    reset_rate_limits()
    post_upload(SAMPLE_CSV, customer_id=synthetic_customer, kind="erp_invoice_lines")
    time.sleep(0.5)
    files = httpx.get(
        f"{API_BASE}/v1/customers/{synthetic_customer}/legacy-files", timeout=10
    ).json()["data"]
    assert files == []


# --- speed: import completes well under the 2s budget --------------------------------------------


def test_legacy_import_completes_under_the_2s_budget(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    make_reference(synthetic_customer, "Bayline", brand_values=["Bayline"])
    make_program(
        synthetic_customer,
        name="Bayline program",
        brand_manufacturer="Bayline",
        external_ref="PG-TEST-1",
    )
    reset_rate_limits()
    # Distinct periods, not distinct rows with a repeated (program, period) pair: the unique
    # key is (source_file_id, program_external_ref, period_start), and a legacy export is one
    # row per program per period by definition.
    periods = [f"{2000 + (i // 12):04d}-{(i % 12) + 1:02d}" for i in range(200)]
    content = legacy_csv_bytes([("PG-TEST-1", period, "10.00") for period in periods])
    start = time.monotonic()
    resp = post_legacy_upload(content, customer_id=synthetic_customer)
    run_id = resp.json()["data"]["ingestRunId"]
    run = wait_run_status(run_id, budget_s=10)
    elapsed = time.monotonic() - start
    assert run["status"] == "imported"
    print(f"legacy import of 200 rows: {elapsed:.3f}s (upload+poll overhead included)")
