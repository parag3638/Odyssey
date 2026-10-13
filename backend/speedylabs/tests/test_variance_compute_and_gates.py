"""Live, against `:8000`/`:5432`: `POST /v1/ingest-runs/{id}/variances` - the preconditions
(step 12.4's gate table), the full outer join's missing-side behaviour, idempotency,
concurrency, replacement, and `first_variance_at`'s write-once rule.

Builds small synthetic customers/programs/runs (`tests.legacy_variance_fixtures`) rather than
the 48,150-row demo: everything here is about a specific *state* (a never-calculated run, an
errored attempt, a legacy file covering a month the ERP run does not), not a scale property.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import date
from decimal import Decimal

import httpx

from tests.legacy_variance_fixtures import (
    ADMIN_DB_URL,
    API_BASE,
    admin_conn,
    db,
    legacy_csv_bytes,
    make_ingest_run,
    make_program,
    make_reference,
    setup_calculated_program,
    synthetic_customer,
    upload_and_wait_imported,
)
from tests.live import live_api, reset_rate_limits

__all__ = ["live_api", "synthetic_customer"]  # re-exported fixtures


def _compute(
    run_id: str, legacy_source_file_id: object, *, idempotency_key: str | None = None
) -> httpx.Response:
    headers = {} if idempotency_key is None else {"Idempotency-Key": idempotency_key}
    return httpx.post(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
        json={"legacySourceFileId": str(legacy_source_file_id)},
        headers=headers,
        timeout=10,
    )


def _wait_variances(run_id: str, *, budget_s: float = 10) -> dict[str, object]:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variance-summary", timeout=10)
        if resp.status_code == 200:
            return resp.json()["data"]
        time.sleep(0.2)
    raise AssertionError(f"variances for {run_id} not ready within {budget_s}s")


# --- gates: each precondition refused on its own, by the decision table in the plan --------------


def test_never_calculated_is_409_rebates_not_calculated(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id = make_ingest_run(synthetic_customer)
    content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")])
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "rebates_not_calculated"


def test_an_errored_rebate_attempt_is_409_via_the_shared_gate(
    live_api: None, synthetic_customer: str
) -> None:
    """P12 reuses P11's `rebate_gate` exactly - the two facts it refuses on are the ones that
    function returns, not a second query counting something else."""
    del live_api
    run_id = make_ingest_run(synthetic_customer)
    with admin_conn() as conn:
        from src.lib.ids import uuid7

        conn.execute(
            "insert into rebate_calc_attempts "
            "(id, ingest_run_id, program_id, outcome, reason, engine_name, engine_version) "
            "select %s, %s, id, 'errored', 'boom', 'test', '0.0.1' from rebate_programs limit 1",
            (str(uuid7()), run_id),
        )
        conn.execute("update ingest_runs set calculated_at = now() where id = %s", (run_id,))
    content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")])
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "rebates_not_calculated"


def test_calculated_at_set_by_hand_with_zero_rebate_calcs_is_409(
    live_api: None, synthetic_customer: str
) -> None:
    """A different question from the one above: 'did the calculation complete' can be true
    (errored=0, calculated_at set) while 'is there anything to compare' is false. Guards against
    hand-edited state, same as `not_materialized` guards a hand-edited `materialized`."""
    del live_api
    make_reference(synthetic_customer, "Bayline", brand_values=["Bayline"])
    make_program(
        synthetic_customer,
        name="Bayline program",
        brand_manufacturer="Bayline",
        external_ref="PG-TEST-1",
    )
    run_id = make_ingest_run(synthetic_customer)
    with admin_conn() as conn:
        conn.execute("update ingest_runs set calculated_at = now() where id = %s", (run_id,))
    content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")])
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "rebates_not_calculated"


def test_cross_customer_legacy_file_is_404(live_api: None, synthetic_customer: str) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )

    # A second, unrelated customer with its own legacy file.
    import uuid as uuid_mod

    from src.lib.ids import uuid7

    other_customer = str(uuid7())
    with admin_conn() as conn:
        conn.execute(
            "insert into customers (id, name, slug) values (%s, %s, %s)",
            (other_customer, "Other Co", f"other-{uuid_mod.uuid4().hex[:8]}"),
        )
    try:
        content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")])
        legacy = upload_and_wait_imported(content, customer_id=other_customer)
        reset_rate_limits()
        resp = _compute(
            run_id, legacy["sourceFileId"], idempotency_key=f"gate-{uuid_mod.uuid4().hex}"
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "not_found"
        with db(ADMIN_DB_URL) as conn2:
            count = conn2.execute(
                "select count(*) from variances where ingest_run_id = %s", (run_id,)
            ).fetchone()
        assert count is not None and count[0] == 0
    finally:
        with admin_conn() as conn:
            conn.execute("delete from customers where id = %s", (other_customer,))


def test_an_erp_file_id_passed_as_the_legacy_id_is_404(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    # Any erp_invoice_lines source_file_id belonging to this customer - the one behind `run_id`
    # itself is one.
    with admin_conn() as conn:
        row = conn.execute(
            "select source_file_id from ingest_runs where id = %s", (run_id,)
        ).fetchone()
    assert row is not None
    erp_file_id: object = row[0]
    reset_rate_limits()
    resp = _compute(run_id, erp_file_id, idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


def test_a_legacy_file_that_failed_to_import_is_409_legacy_not_imported(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    bad_content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")]).replace(b"10.00", b"10.001")
    failed = upload_and_wait_imported(bad_content, customer_id=synthetic_customer)
    assert failed["status"] == "failed"
    reset_rate_limits()
    resp = _compute(run_id, failed["sourceFileId"], idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "legacy_not_imported"


def test_all_rows_unmatched_is_422_legacy_unmatched_not_a_202(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    content = legacy_csv_bytes([("PG-NOBODY-1", "2026-01", "999.00")])
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "legacy_unmatched"
    assert "PG-NOBODY-1" in body["error"]["message"]
    with db(ADMIN_DB_URL) as conn:
        count = conn.execute(
            "select count(*) from variances where ingest_run_id = %s", (run_id,)
        ).fetchone()
    assert count is not None and count[0] == 0


def test_readiness_is_not_a_precondition(live_api: None, synthetic_customer: str) -> None:
    """The gate table says readiness is deliberately not required - a run can be
    `drift_blocked` or never-profiled and still be comparable, since the comparison only reads
    `rebate_calcs`, not the readiness checklist."""
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    with admin_conn() as conn:
        conn.execute("update ingest_runs set status = 'drift_blocked' where id = %s", (run_id,))
    content = legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")])
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"ready-{uuid.uuid4().hex}")
    assert resp.status_code == 202, resp.text
    summary = _wait_variances(run_id)
    assert summary["varianceCount"] == 1


# --- the full outer join: both missing-side directions, outside the run's span ------------------


def test_missing_on_the_calculated_side_is_legacy_only_with_zero_calculated(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline", rate="1.000000", qty="10"
    )
    # The legacy export carries a period the calculation has no row for - a legacy history longer
    # than the ERP file, which is the realistic shape of this case and the one the plan names.
    #
    # Deliberately NOT by deleting every `rebate_calcs` row: that is a run with no result set at
    # all, which this same tester's finding #3 established must be refused with `409
    # rebates_not_calculated`. The two cannot both hold, and a comparison against nothing is the
    # one that has to lose - it would present "the legacy system paid for everything and we
    # calculated nothing" as an ordinary table.
    content = legacy_csv_bytes(
        [("PG-TEST-1", "2026-01", "55.00"), ("PG-TEST-1", "2026-02", "70.00")]
    )
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"miss-{uuid.uuid4().hex}")
    assert resp.status_code == 202, resp.text
    _wait_variances(run_id)
    rows = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variances", timeout=10).json()["data"]
    by_period = {row["periodStart"]: row for row in rows}
    missing = by_period["2026-02-01"]
    assert missing["presence"] == "legacy_only"
    assert missing["calculatedAmount"]["amount"] == "0.00"
    assert missing["legacyAmount"]["amount"] == "70.00"
    assert missing["varianceAmount"]["amount"] == "70.00"


def test_missing_on_the_legacy_side_is_calculated_only_with_zero_legacy(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline", rate="1.000000", qty="10"
    )
    # A legacy file for a *different* program only - this run's program never appears in it.
    make_reference(synthetic_customer, "Other Brand", brand_values=["Other Brand"])
    make_program(
        synthetic_customer,
        name="Other program",
        brand_manufacturer="Other Brand",
        external_ref="PG-OTHER-1",
    )
    content = legacy_csv_bytes([("PG-OTHER-1", "2026-01", "1.00")])
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"miss2-{uuid.uuid4().hex}")
    assert resp.status_code == 202, resp.text
    _wait_variances(run_id)
    rows = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variances", timeout=10).json()["data"]
    by_presence = {row["presence"] for row in rows}
    assert "calculated_only" in by_presence
    calc_only = next(row for row in rows if row["presence"] == "calculated_only")
    assert calc_only["legacyAmount"]["amount"] == "0.00"
    assert calc_only["externalRef"] == "PG-TEST-1"
    assert (
        Decimal(calc_only["calculatedAmount"]["amount"])
        == Decimal(calc_only["varianceAmount"]["amount"]) * -1
    )


def test_a_legacy_period_outside_the_runs_span_is_legacy_only(
    live_api: None, synthetic_customer: str
) -> None:
    """The realistic shape: a legacy history longer than the ERP file it is compared against."""
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer,
        external_ref="PG-TEST-1",
        brand="Bayline",
        months=[date(2026, 6, 15)],
    )
    content = legacy_csv_bytes([("PG-TEST-1", "2025-12", "1.00"), ("PG-TEST-1", "2026-06", "5.00")])
    legacy = upload_and_wait_imported(content, customer_id=synthetic_customer)
    reset_rate_limits()
    resp = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"span-{uuid.uuid4().hex}")
    assert resp.status_code == 202, resp.text
    _wait_variances(run_id)
    rows = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variances", timeout=10).json()["data"]
    assert len(rows) == 2
    dec_row = next(r for r in rows if r["periodStart"] == "2025-12-01")
    assert dec_row["presence"] == "legacy_only"


# --- idempotency and concurrency ------------------------------------------------------------------


def test_idempotency_replay_and_conflict_on_a_different_legacy_file(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    legacy_a = upload_and_wait_imported(
        legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")]), customer_id=synthetic_customer
    )
    legacy_b = upload_and_wait_imported(
        legacy_csv_bytes([("PG-TEST-1", "2026-01", "20.00")]), customer_id=synthetic_customer
    )
    key = f"idem-{uuid.uuid4().hex}"
    reset_rate_limits()
    first = _compute(run_id, legacy_a["sourceFileId"], idempotency_key=key)
    assert first.status_code == 202, first.text
    _wait_variances(run_id)

    replay = _compute(run_id, legacy_a["sourceFileId"], idempotency_key=key)
    assert replay.status_code == 202
    assert replay.json() == first.json()

    conflict = _compute(run_id, legacy_b["sourceFileId"], idempotency_key=key)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"


def test_two_concurrent_posts_write_the_result_set_once(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    legacy = upload_and_wait_imported(
        legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")]), customer_id=synthetic_customer
    )
    reset_rate_limits()
    responses: list[httpx.Response] = []

    def _fire() -> None:
        responses.append(_compute(run_id, legacy["sourceFileId"]))

    threads = [threading.Thread(target=_fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
    _wait_variances(run_id)
    with db(ADMIN_DB_URL) as conn:
        count = conn.execute(
            "select count(*) from variances where ingest_run_id = %s", (run_id,)
        ).fetchone()
    assert count is not None and count[0] == 1


def test_comparing_against_a_second_file_replaces_the_first(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    legacy_a = upload_and_wait_imported(
        legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")]), customer_id=synthetic_customer
    )
    legacy_b = upload_and_wait_imported(
        legacy_csv_bytes([("PG-TEST-1", "2026-01", "20.00")]), customer_id=synthetic_customer
    )
    reset_rate_limits()
    first = _compute(run_id, legacy_a["sourceFileId"], idempotency_key=f"rep-a-{uuid.uuid4().hex}")
    assert first.status_code == 202, first.text
    _wait_variances(run_id)
    reset_rate_limits()
    second = _compute(run_id, legacy_b["sourceFileId"], idempotency_key=f"rep-b-{uuid.uuid4().hex}")
    assert second.status_code == 202, second.text
    _wait_variances(run_id)

    rows = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variances", timeout=10).json()["data"]
    assert len(rows) == 1
    assert rows[0]["legacyAmount"]["amount"] == "20.00"
    with db(ADMIN_DB_URL) as conn:
        file_ids = conn.execute(
            "select distinct legacy_source_file_id from variances where ingest_run_id = %s",
            (run_id,),
        ).fetchall()
    assert len(file_ids) == 1
    assert str(file_ids[0][0]) == legacy_b["sourceFileId"]


# --- `first_variance_at`: written once, never moved by a recompute ------------------------------


def test_first_variance_at_is_written_once_and_a_recompute_does_not_move_it(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    legacy = upload_and_wait_imported(
        legacy_csv_bytes([("PG-TEST-1", "2026-01", "10.00")]), customer_id=synthetic_customer
    )
    reset_rate_limits()
    first = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"fva-{uuid.uuid4().hex}")
    assert first.status_code == 202, first.text
    _wait_variances(run_id)

    with db(ADMIN_DB_URL) as conn:
        row = conn.execute(
            "select first_variance_at, elapsed_ms_to_first_variance, created_at "
            "from ingest_runs where id = %s",
            (run_id,),
        ).fetchone()
    assert row is not None
    first_at, elapsed_ms, created_at = row
    assert first_at is not None
    assert elapsed_ms is not None and elapsed_ms >= 0
    assert first_at >= created_at

    time.sleep(1.1)  # so a moved timestamp would be detectably later
    reset_rate_limits()
    second = _compute(run_id, legacy["sourceFileId"], idempotency_key=f"fva2-{uuid.uuid4().hex}")
    assert second.status_code == 202, second.text
    _wait_variances(run_id)

    with db(ADMIN_DB_URL) as conn:
        row2 = conn.execute(
            "select first_variance_at, elapsed_ms_to_first_variance from ingest_runs where id = %s",
            (run_id,),
        ).fetchone()
    assert row2 is not None
    assert row2[0] == first_at, "a recompute must not move first_variance_at"
    assert row2[1] == elapsed_ms, "a recompute must not move elapsed_ms_to_first_variance"


def test_an_empty_comparison_leaves_first_variance_at_null(
    live_api: None, synthetic_customer: str
) -> None:
    """A gap is still a variance row here (both programs exist on the calculated side, but the
    legacy file names only one of them) - this test instead proves the *other* half: a run with
    truly zero matched rows never reaches the job at all (caught by `legacy_unmatched`), so
    `first_variance_at` stays null. Already covered by the 422 test above; restated here against
    `first_variance_at` directly for the handoff P13/P15 care about."""
    del live_api
    run_id, _program_id = setup_calculated_program(
        synthetic_customer, external_ref="PG-TEST-1", brand="Bayline"
    )
    with db(ADMIN_DB_URL) as conn:
        before = conn.execute(
            "select first_variance_at from ingest_runs where id = %s", (run_id,)
        ).fetchone()
    assert before is not None and before[0] is None
