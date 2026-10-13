"""The rebate routes (`features/rebates/routes.py`), live against `:8000` and `:5432` - the
contract, the gates, idempotency, concurrency, cross-run isolation, and the brand-coverage /
engine-staleness surfaces that make a `0.00` legible.

Builds its own small synthetic customers (`tests/rebate_fixtures.py`) rather than the shared
48,150-row demo fixture: most of what this file attacks - a customer with no programs yet, a run
that has never been calculated, three distinct brand resolutions on one run, an engine-version
mismatch - is a specific *state*, not a scale property, and a dedicated customer makes each test
independent of what the demo customer happens to look like when this suite runs.
"""

from __future__ import annotations

import threading
import uuid

import httpx
import psycopg

from tests.live import APP_DB_URL, live_api, reset_rate_limits
from tests.rebate_fixtures import (
    canonical_line,
    insert_canonical_lines,
    make_ingest_run,
    make_program,
    make_reference,
    synthetic_customer,
)

__all__ = ["live_api", "synthetic_customer"]  # re-exported fixtures

API_BASE = "http://localhost:8000"


def _calculate(run_id: str, *, idempotency_key: str | None = None) -> httpx.Response:
    headers = {} if idempotency_key is None else {"Idempotency-Key": idempotency_key}
    return httpx.post(
        f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-calcs", headers=headers, timeout=10
    )


def _wait_calculated(run_id: str, *, budget_s: float = 10) -> None:
    import time

    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-summary", timeout=10)
        if resp.status_code == 200 and resp.json()["data"]["calculated"]:
            return
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish calculating within {budget_s}s")


def _setup_single_program(customer_id: str, *, brand: str = "Acme Brand") -> tuple[str, str]:
    """A reference row, one program and one run for `brand`. `rebate_programs` has a
    `unique (customer_id, name)` constraint, so the program name is derived from `brand` -
    several tests call this twice for the same customer with different brands."""
    make_reference(customer_id, brand, brand_values=[brand])
    program_id = make_program(
        customer_id, name=f"{brand} program", brand_manufacturer=brand, rate="1.000000"
    )
    run_id = make_ingest_run(customer_id)
    insert_canonical_lines([canonical_line(run_id, brand_manufacturer=brand, uom="CS", qty="10")])
    return run_id, program_id


def _calculate_and_wait(run_id: str) -> None:
    reset_rate_limits()
    resp = _calculate(run_id, idempotency_key=f"setup-{uuid.uuid4().hex}")
    assert resp.status_code == 202, resp.text
    _wait_calculated(run_id)


# --- gates: the three preconditions and the output-side gate ------------------------------------


def test_calculating_before_materialising_returns_409_not_materialized(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id = make_ingest_run(synthetic_customer)  # no canonical_lines rows at all
    reset_rate_limits()
    resp = _calculate(run_id, idempotency_key=f"notmat-{uuid.uuid4().hex}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "not_materialized"


def test_calculating_with_no_rebate_programs_returns_422(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id = make_ingest_run(synthetic_customer)
    insert_canonical_lines(
        [canonical_line(run_id, brand_manufacturer="Acme Brand", uom="CS", qty="10")]
    )
    reset_rate_limits()
    resp = _calculate(run_id, idempotency_key=f"noprog-{uuid.uuid4().hex}")
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "no_rebate_programs"
    assert "rebate_programs" not in body["error"]["message"]  # no internal table name leaked

    # And the summary/calcs routes must say "not calculated", never a 200 of zeros.
    summary = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-summary", timeout=10)
    assert summary.status_code == 409
    assert summary.json()["error"]["code"] == "rebates_not_calculated"


def test_summary_before_any_calculation_is_409_not_a_200_of_zeros(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _ = _setup_single_program(synthetic_customer)
    resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-summary", timeout=10)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "rebates_not_calculated"


# --- 404s: no internal names leaked --------------------------------------------------------------


def test_unknown_run_returns_404_on_every_run_scoped_route(live_api: None) -> None:
    del live_api
    bogus = str(uuid.uuid4())
    for path in (
        f"/v1/ingest-runs/{bogus}/rebate-summary",
        f"/v1/ingest-runs/{bogus}/rebate-calcs",
    ):
        resp = httpx.get(f"{API_BASE}{path}", timeout=10)
        assert resp.status_code == 404, path
        body = resp.json()
        assert body["error"]["code"] == "not_found"
        assert "select" not in body["error"]["message"].lower()
        assert "ingest_runs" not in body["error"]["message"]


def test_unknown_calc_id_returns_404(live_api: None) -> None:
    del live_api
    resp = httpx.get(f"{API_BASE}/v1/rebate-calcs/{uuid.uuid4()}/lines", timeout=10)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_unknown_customer_returns_404_on_programs_route(live_api: None) -> None:
    del live_api
    resp = httpx.get(f"{API_BASE}/v1/customers/{uuid.uuid4()}/rebate-programs", timeout=10)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# --- idempotency, re-verified against the live API (docs/decisions.md records this once failed) -


def test_idempotency_key_replay_and_conflict(live_api: None, synthetic_customer: str) -> None:
    del live_api
    run_id, _ = _setup_single_program(synthetic_customer)
    key = f"idem-{uuid.uuid4().hex}"
    reset_rate_limits()

    first = _calculate(run_id, idempotency_key=key)
    assert first.status_code == 202, first.text
    _wait_calculated(run_id)

    replay = _calculate(run_id, idempotency_key=key)
    assert replay.status_code == 202
    assert replay.json() == first.json()

    other_run, _ = _setup_single_program(synthetic_customer, brand="Other Brand")
    conflict = _calculate(other_run, idempotency_key=key)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"


def test_an_oversized_idempotency_key_is_rejected_as_malformed_not_500(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _ = _setup_single_program(synthetic_customer)
    reset_rate_limits()
    resp = _calculate(run_id, idempotency_key="x" * 300)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "malformed_request"


# --- concurrency: two POSTs in flight write the result set once -------------------------------


def test_two_concurrent_posts_write_the_result_set_exactly_once(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = _setup_single_program(synthetic_customer)
    reset_rate_limits()

    responses: list[httpx.Response] = []

    def _fire() -> None:
        responses.append(_calculate(run_id))

    threads = [threading.Thread(target=_fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
    _wait_calculated(run_id)

    with psycopg.connect(APP_DB_URL, autocommit=True) as conn:
        counts = conn.execute(
            "select "
            "(select count(*) from rebate_calcs where ingest_run_id = %s), "
            "(select count(*) from rebate_calc_lines where ingest_run_id = %s), "
            "(select count(*) from rebate_calc_attempts where ingest_run_id = %s)",
            (run_id, run_id, run_id),
        ).fetchone()
    assert counts is not None
    calcs, lines, attempts = counts
    assert calcs == 1, "one program, one month -> exactly one rebate_calcs row, not duplicated"
    assert lines == 1
    assert attempts == 1


# --- cross-run isolation: a calc's lines never leak another run's rows -------------------------


def test_a_calcs_lines_belong_only_to_its_own_run(live_api: None, synthetic_customer: str) -> None:
    del live_api
    run_a, _ = _setup_single_program(synthetic_customer, brand="Brand A")
    run_b, _ = _setup_single_program(synthetic_customer, brand="Brand B")
    _calculate_and_wait(run_a)
    _calculate_and_wait(run_b)

    calcs_a = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_a}/rebate-calcs", timeout=10).json()[
        "data"
    ]
    calc_id_a = calcs_a[0]["rebateCalcId"]
    lines_a = httpx.get(f"{API_BASE}/v1/rebate-calcs/{calc_id_a}/lines", timeout=10).json()["data"]
    assert len(lines_a) == 1

    with psycopg.connect(APP_DB_URL, autocommit=True) as conn:
        row = conn.execute(
            "select r.ingest_run_id from rebate_calc_lines r where r.canonical_line_id = %s",
            (lines_a[0]["canonicalLineId"],),
        ).fetchone()
    assert row is not None
    assert str(row[0]) == run_a, "a line returned for run A's calc must belong to run A"


# --- brand coverage: the three-way resolution ---------------------------------------------------


def test_brand_coverage_reports_program_no_program_and_unknown_brand_separately(
    live_api: None, synthetic_customer: str
) -> None:
    customer_id = synthetic_customer
    del live_api
    # "Covered Brand": has a program and lines - resolution "program".
    make_reference(customer_id, "Covered Brand", brand_values=["Covered Brand"])
    make_program(
        customer_id, name="Covered program", brand_manufacturer="Covered Brand", rate="1.0"
    )
    # "Known But Uncovered": a real reference row, but no program points at it - "no_program".
    make_reference(customer_id, "Known But Uncovered", brand_values=["Known But Uncovered"])
    # "Mystery Brand" appears on a line but is in no reference row at all - "unknown_brand".
    run_id = make_ingest_run(customer_id)
    insert_canonical_lines(
        [
            canonical_line(run_id, brand_manufacturer="Covered Brand", uom="CS", qty="10"),
            canonical_line(run_id, brand_manufacturer="Known But Uncovered", uom="CS", qty="5"),
            canonical_line(run_id, brand_manufacturer="Mystery Brand", uom="CS", qty="3"),
        ]
    )
    _calculate_and_wait(run_id)

    summary = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-summary", timeout=10).json()[
        "data"
    ]
    by_value = {row["brandValue"]: row for row in summary["brands"]}
    assert by_value["Covered Brand"]["resolution"] == "program"
    assert by_value["Known But Uncovered"]["resolution"] == "no_program"
    assert by_value["Known But Uncovered"]["manufacturerName"] == "Known But Uncovered"
    assert by_value["Mystery Brand"]["resolution"] == "unknown_brand"
    assert by_value["Mystery Brand"]["manufacturerName"] is None
    assert summary["brandsTruncated"] is False


# --- engineStale: the label never lies about which engine produced the stored numbers ----------


def test_engine_stale_is_true_when_the_stored_version_differs_from_the_current_one(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id, _program_id = _setup_single_program(synthetic_customer)
    _calculate_and_wait(run_id)

    before = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-summary", timeout=10).json()[
        "data"
    ]
    assert before["engineStale"] is False
    assert before["engine"]["version"] == before["currentEngine"]["version"]

    with psycopg.connect(APP_DB_URL, autocommit=True) as conn:
        conn.execute(
            "update rebate_calc_attempts set engine_version = '0.0.1' where ingest_run_id = %s",
            (run_id,),
        )
        conn.execute(
            "update rebate_calcs set engine_version = '0.0.1' where ingest_run_id = %s", (run_id,)
        )

    after = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-summary", timeout=10).json()[
        "data"
    ]
    assert after["engineStale"] is True
    assert after["engine"]["version"] == "0.0.1"  # the label keeps reporting what made the number
    assert after["currentEngine"]["version"] != "0.0.1"
    calcs = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-calcs", timeout=10).json()["data"]
    assert all(row["engine"]["version"] == "0.0.1" for row in calcs)


# --- negative period, end to end through the API -------------------------------------------------


def test_a_negative_period_total_serialises_with_its_sign_through_the_api(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    customer_id = synthetic_customer
    make_reference(customer_id, "Credit Brand", brand_values=["Credit Brand"])
    make_program(customer_id, name="Credit program", brand_manufacturer="Credit Brand", rate="1.0")
    run_id = make_ingest_run(customer_id)
    insert_canonical_lines(
        [
            canonical_line(run_id, brand_manufacturer="Credit Brand", uom="CS", qty="10"),
            canonical_line(run_id, brand_manufacturer="Credit Brand", uom="CS", qty="-30"),
        ]
    )
    _calculate_and_wait(run_id)

    calcs = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-calcs", timeout=10).json()["data"]
    assert len(calcs) == 1
    assert calcs[0]["amount"] == {"amount": "-20.00", "currency": "USD"}
    assert calcs[0]["basisQty"] == "-20.0000"

    summary = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-summary", timeout=10).json()[
        "data"
    ]
    assert summary["totalAmount"]["amount"] == "-20.00"


# --- list/lines contract: pagination, status filter, bad cursor, rate shape --------------------


def test_lines_status_filter_and_skipped_detail_sentence(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    customer_id = synthetic_customer
    make_reference(customer_id, "Weighty Brand", brand_values=["Weighty Brand"])
    make_program(
        customer_id,
        name="Weighty program",
        brand_manufacturer="Weighty Brand",
        kind="per_lb_billback",
        uom_basis="LB",
        rate="0.100000",
    )
    run_id = make_ingest_run(customer_id)
    insert_canonical_lines(
        [
            canonical_line(
                run_id,
                brand_manufacturer="Weighty Brand",
                uom="LB",
                qty="5",
                catch_weight_lbs="12.0",
            ),
            canonical_line(
                run_id, brand_manufacturer="Weighty Brand", uom="LB", qty="5", catch_weight_lbs=None
            ),
        ]
    )
    _calculate_and_wait(run_id)

    calcs = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-calcs", timeout=10).json()["data"]
    calc_id = calcs[0]["rebateCalcId"]

    skipped = httpx.get(
        f"{API_BASE}/v1/rebate-calcs/{calc_id}/lines", params={"status": "skipped"}, timeout=10
    ).json()["data"]
    assert len(skipped) == 1
    assert skipped[0]["skippedReason"] == "missing_catch_weight"
    assert "weight" in skipped[0]["skippedDetail"].lower()

    bad_status = httpx.get(
        f"{API_BASE}/v1/rebate-calcs/{calc_id}/lines", params={"status": "nonsense"}, timeout=10
    )
    assert bad_status.status_code == 400
    assert bad_status.json()["error"]["code"] == "malformed_request"

    bad_cursor = httpx.get(
        f"{API_BASE}/v1/rebate-calcs/{calc_id}/lines", params={"cursor": "not-a-cursor"}, timeout=10
    )
    assert bad_cursor.status_code == 400
    assert bad_cursor.json()["error"]["code"] == "malformed_request"


def test_programs_route_returns_a_bare_6dp_rate_with_a_sibling_currency(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    customer_id = synthetic_customer
    make_reference(customer_id, "Priced Brand", brand_values=["Priced Brand"])
    make_program(
        customer_id, name="Priced program", brand_manufacturer="Priced Brand", rate="0.185000"
    )
    resp = httpx.get(f"{API_BASE}/v1/customers/{customer_id}/rebate-programs", timeout=10)
    assert resp.status_code == 200
    [program] = resp.json()["data"]
    assert program["rate"] == "0.185000"  # 6dp, bare string - never rounded to cents
    assert program["rateCurrency"] == "USD"
    assert "amount" not in program  # not a Money object - the deliberate §3 deviation
