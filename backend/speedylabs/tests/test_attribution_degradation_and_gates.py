"""Attribution's degradation paths and preconditions, against synthetic customers smaller and
more targeted than the 48,150-row demo file - in `tests.legacy_variance_fixtures`'s own style.

Covers:
- the two seeded-legacy-quirk null paths (`redistributor_rate`, `legacy_period_cutoff_day`),
- that a raising rule degrades the run rather than losing it entirely (plan 13.3, Security
  section: "A broken rule degrades the score, it does not lose the report"),
- the `variances_not_computed` gate, gated on a count rather than a status,
- idempotency and the queueing lock.
"""

from __future__ import annotations

import io
import time
import uuid
from datetime import date

import httpx
import psycopg

from tests.attribution_fixtures import (
    ADMIN_DB_URL,
    API_BASE,
    admin_conn,
    attribute_and_wait,
    calculate_and_wait,
    canonical_line,
    compute_variances_and_wait,
    fetch_attribution_summary,
    insert_canonical_lines,
    make_ingest_run,
    make_program,
    make_reference,
    set_customer_cutoff_day,
    set_program_redistributor_rate,
    synthetic_customer,
)
from tests.live import live_api

__all__ = ["live_api", "synthetic_customer"]


def _attribute(run_id: str, *, idempotency_key: str | None = None) -> httpx.Response:
    headers = {} if idempotency_key is None else {"Idempotency-Key": idempotency_key}
    return httpx.post(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances/attribute", headers=headers, timeout=10
    )


# --- the redistributor-rate null path -------------------------------------------------------


def test_redistributor_rate_null_names_the_cause_with_zero_dollars(
    live_api: None, synthetic_customer: str
) -> None:
    """P13.0's decision: a customer who cannot supply the redistributor rate still gets the
    cause *identified*, with a 0.00 contribution, and the dollars fall to residue rather than
    being invented."""
    del live_api
    make_reference(
        synthetic_customer,
        "RedistBrand",
        brand_values=["RedistBrand"],
        direct_vendor_values=["DIRECT-VENDOR-1"],
        redistributor_only=True,
    )
    program_id = make_program(
        synthetic_customer,
        name="Redist program",
        brand_manufacturer="RedistBrand",
        kind="per_lb_billback",
        uom_basis="LB",
        rate="0.900000",
        external_ref="PG-RRN-1",
    )
    set_program_redistributor_rate(program_id, None)  # explicit, though it is the default
    run_id = make_ingest_run(synthetic_customer)
    row = canonical_line(
        run_id,
        brand_manufacturer="RedistBrand",
        uom="LB",
        qty="1",
        catch_weight_lbs="100",
        invoice_date=date(2026, 1, 15),
    )
    row["invoice_vendor"] = "DIRECT-VENDOR-1"
    insert_canonical_lines([row])
    calculate_and_wait(run_id)

    content = b"PROGRAM,PERIOD,REBATE_PAID\nPG-RRN-1,2026-01,999.00\n"
    up = httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": synthetic_customer, "kind": "legacy_rebate"},
        files={"file": ("legacy.csv", io.BytesIO(content), "text/csv")},
        timeout=30,
    )
    legacy_run_id = up.json()["data"]["ingestRunId"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = httpx.get(f"{API_BASE}/v1/ingest-runs/{legacy_run_id}", timeout=10)
        if r.json()["data"].get("isSettled"):
            break
        time.sleep(0.2)
    compute_variances_and_wait(run_id, up.json()["data"]["sourceFileId"])
    attribute_and_wait(run_id)

    with admin_conn() as conn:
        cur = conn.execute(
            "select root_cause, contribution_amount, evidence from variance_line_links "
            "where ingest_run_id = %s and root_cause = 'redistributor_misattribution'",
            (run_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1, "the cause must still be named even with no rate"
    _root_cause, contribution, evidence = rows[0]
    assert str(contribution) == "0.00"
    assert evidence["redistributorRateKnown"] is False


def test_redistributor_rate_present_gives_the_positive_sign_against_the_docstring(
    live_api: None, synthetic_customer: str
) -> None:
    """The generator's docstring claims a misattributed line is "always underpaid"; measured
    false for a per-pound program where the redistributor rate (0.65) exceeds the program's own
    rate (0.18) - legacy pays *more*, so the contribution must be positive."""
    del live_api
    make_reference(
        synthetic_customer,
        "RedistBrand2",
        brand_values=["RedistBrand2"],
        direct_vendor_values=["DIRECT-VENDOR-2"],
        redistributor_only=True,
    )
    program_id = make_program(
        synthetic_customer,
        name="Redist program 2",
        brand_manufacturer="RedistBrand2",
        kind="per_lb_billback",
        uom_basis="LB",
        rate="0.180000",
        external_ref="PG-RRS-1",
    )
    set_program_redistributor_rate(program_id, "0.650000")
    run_id = make_ingest_run(synthetic_customer)
    row = canonical_line(
        run_id,
        brand_manufacturer="RedistBrand2",
        uom="LB",
        qty="1",
        catch_weight_lbs="100",
        invoice_date=date(2026, 1, 15),
    )
    row["invoice_vendor"] = "DIRECT-VENDOR-2"
    insert_canonical_lines([row])
    calculate_and_wait(run_id)

    content = b"PROGRAM,PERIOD,REBATE_PAID\nPG-RRS-1,2026-01,999.00\n"
    up = httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": synthetic_customer, "kind": "legacy_rebate"},
        files={"file": ("legacy.csv", io.BytesIO(content), "text/csv")},
        timeout=30,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = httpx.get(f"{API_BASE}/v1/ingest-runs/{up.json()['data']['ingestRunId']}", timeout=10)
        if r.json()["data"].get("isSettled"):
            break
        time.sleep(0.2)
    compute_variances_and_wait(run_id, up.json()["data"]["sourceFileId"])
    attribute_and_wait(run_id)

    with admin_conn() as conn:
        cur = conn.execute(
            "select contribution_amount from variance_line_links "
            "where ingest_run_id = %s and root_cause = 'redistributor_misattribution'",
            (run_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    # -(0.18 - 0.65) * 100 = +47.00
    assert str(rows[0][0]) == "47.00", (
        "expected a POSITIVE contribution (legacy paid more, redistributor rate > program rate) "
        f"- got {rows[0][0]}, which would be the sign the generator's docstring wrongly implies"
    )


# --- the legacy_period_cutoff_day null path --------------------------------------------------


def test_legacy_period_cutoff_day_null_makes_no_date_cutoff_claim_at_all(
    live_api: None, synthetic_customer: str
) -> None:
    """P13.2's second found gap: with no seeded cutoff day, a line invoiced on what *would* be
    a cutoff day in other customers' data must not be claimed by `date_cutoff` at all - no
    inferring "last day of the month", no reading it off the variance signs."""
    del live_api
    set_customer_cutoff_day(synthetic_customer, None)
    make_reference(synthetic_customer, "PlainBrand", brand_values=["PlainBrand"])
    make_program(
        synthetic_customer,
        name="Plain program",
        brand_manufacturer="PlainBrand",
        kind="per_case_volume",
        uom_basis="CS",
        rate="1.000000",
        external_ref="PG-CUT-1",
    )
    run_id = make_ingest_run(synthetic_customer)
    row = canonical_line(
        run_id,
        brand_manufacturer="PlainBrand",
        uom="CS",
        qty="10",
        invoice_date=date(2026, 3, 31),
    )
    insert_canonical_lines([row])
    calculate_and_wait(run_id)

    content = b"PROGRAM,PERIOD,REBATE_PAID\nPG-CUT-1,2026-03,5.00\nPG-CUT-1,2026-04,5.00\n"
    up = httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": synthetic_customer, "kind": "legacy_rebate"},
        files={"file": ("legacy.csv", io.BytesIO(content), "text/csv")},
        timeout=30,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = httpx.get(f"{API_BASE}/v1/ingest-runs/{up.json()['data']['ingestRunId']}", timeout=10)
        if r.json()["data"].get("isSettled"):
            break
        time.sleep(0.2)
    compute_variances_and_wait(run_id, up.json()["data"]["sourceFileId"])
    attribute_and_wait(run_id)

    with admin_conn() as conn:
        cur = conn.execute(
            "select count(*) from variance_line_links "
            "where ingest_run_id = %s and root_cause = 'date_cutoff'",
            (run_id,),
        )
        fetched = cur.fetchone()
        assert fetched is not None
        count = fetched[0]
    assert count == 0, "no date_cutoff claim should be possible with a null cutoff day"


# --- a broken rule must degrade the run, not crash it ----------------------------------------


def test_a_raising_rule_degrades_the_run_instead_of_losing_the_report(
    live_api: None, synthetic_customer: str
) -> None:
    """Plan 13.3: "given one rule patched to raise, then that rule is recorded in
    `detail.ruleFailures` and the summary's `ruleFailures`, its candidate lines fall through to
    the next rule, every other cause is attributed normally, conservation still holds on every
    row, and the run completes with a lower `explainedPct`." Security section: "A broken rule
    degrades the score, it does not lose the report."

    The seeded line is `EA` with no pack size under a per-case program, so with the ordinary
    rule order `case_each_conversion` claims it. Patching `duplicate_line` (position 0, which
    never matches this line anyway) to raise must not change that outcome at all: the correct
    behaviour is for the failing rule to be skipped (recorded as a failure) and the next rule in
    order to still classify the line normally.

    Measured directly against `run_attribution` (in-process, not through the queued job, so the
    failure is deterministic rather than racing procrastinate's retry)."""
    del live_api
    make_reference(synthetic_customer, "DegradeBrand", brand_values=["DegradeBrand"])
    make_program(
        synthetic_customer,
        name="Degrade program",
        brand_manufacturer="DegradeBrand",
        kind="per_case_volume",
        uom_basis="CS",
        rate="1.000000",
        external_ref="PG-DEG-1",
    )
    run_id = make_ingest_run(synthetic_customer)
    row = canonical_line(
        run_id,
        brand_manufacturer="DegradeBrand",
        uom="EA",
        qty="10",
        pack_size=None,
        invoice_date=date(2026, 1, 15),
    )
    insert_canonical_lines([row])
    calculate_and_wait(run_id)

    content = b"PROGRAM,PERIOD,REBATE_PAID\nPG-DEG-1,2026-01,50.00\n"
    up = httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": synthetic_customer, "kind": "legacy_rebate"},
        files={"file": ("legacy.csv", io.BytesIO(content), "text/csv")},
        timeout=30,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = httpx.get(f"{API_BASE}/v1/ingest-runs/{up.json()['data']['ingestRunId']}", timeout=10)
        if r.json()["data"].get("isSettled"):
            break
        time.sleep(0.2)
    compute_variances_and_wait(run_id, up.json()["data"]["sourceFileId"])

    from src.features.attribution import attribute as attribute_mod
    from src.features.attribution import rules as rules_mod
    from src.features.attribution.rules import AttributionLine, Claim, RuleContext

    original_rules = rules_mod.RULES

    def _boom(line: AttributionLine, context: RuleContext) -> Claim | None:
        raise RuntimeError("synthetic rule failure")

    # Position 0 is `_duplicate`, which never matches this (non-duplicate) line anyway - so a
    # correctly-isolated failure here must be invisible to this line's own classification.
    rules_mod.RULES = (_boom, *original_rules[1:])
    conn = psycopg.connect(ADMIN_DB_URL, autocommit=False)
    try:
        outcome = attribute_mod.run_attribution(conn, run_id=run_id, customer_id=synthetic_customer)
        conn.commit()
    finally:
        rules_mod.RULES = original_rules
        conn.close()

    assert outcome.links >= 1, (
        "a failing rule must not crash the whole attribution pass - the other rules should "
        "still run and classify this line via case_each_conversion"
    )
    with admin_conn() as admin:
        cur = admin.execute(
            "select root_cause, contribution_amount from variance_line_links "
            "where ingest_run_id = %s and root_cause <> 'unexplained'",
            (run_id,),
        )
        rows = cur.fetchall()
    assert rows == [("case_each_conversion", 10)] or (
        len(rows) == 1 and rows[0][0] == "case_each_conversion"
    ), f"expected the line to fall through to case_each_conversion, got {rows}"

    # Conservation must still hold even with a rule degraded.
    with admin_conn() as admin:
        cur = admin.execute(
            """
            select v.id, v.variance_amount, coalesce(sum(k.contribution_amount), 0)
            from variances v left join variance_line_links k on k.variance_id = v.id
            where v.ingest_run_id = %s
            group by v.id, v.variance_amount
            having v.variance_amount <> coalesce(sum(k.contribution_amount), 0)
            """,
            (run_id,),
        )
        mismatches = cur.fetchall()
    assert mismatches == [], f"conservation violated while a rule was degraded: {mismatches}"

    # The failure itself must be surfaced, not swallowed - the summary route is where a reader
    # (and the UI's "degraded" panel per plan 13.7) would see it.
    summary = fetch_attribution_summary(run_id)
    assert summary.get("ruleFailures"), (
        "a degraded rule run must report ruleFailures in the attribution summary so the "
        "percentage is never read as a clean result - this field is currently absent"
    )


# --- the gate: variances_not_computed, on a count --------------------------------------------


def test_attribute_refuses_409_when_no_variances_exist_regardless_of_run_status(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    run_id = make_ingest_run(synthetic_customer)
    resp = _attribute(run_id, idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "variances_not_computed"

    # Hand-edit the status; the gate must still refuse, because it counts rows, not status.
    with admin_conn() as conn:
        conn.execute("update ingest_runs set status = 'materialized' where id = %s", (run_id,))
    resp2 = _attribute(run_id, idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp2.status_code == 409, resp2.text
    assert resp2.json()["error"]["code"] == "variances_not_computed"


def test_attribute_404s_on_an_unknown_run(live_api: None) -> None:
    del live_api
    resp = _attribute(str(uuid.uuid4()), idempotency_key=f"gate-{uuid.uuid4().hex}")
    assert resp.status_code == 404, resp.text


# --- idempotency and concurrency --------------------------------------------------------------


def test_same_idempotency_key_does_not_queue_a_second_job(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    make_reference(synthetic_customer, "IdemBrand", brand_values=["IdemBrand"])
    make_program(
        synthetic_customer,
        name="Idem program",
        brand_manufacturer="IdemBrand",
        rate="1.000000",
        external_ref="PG-IDEM-1",
    )
    run_id = make_ingest_run(synthetic_customer)
    row = canonical_line(run_id, brand_manufacturer="IdemBrand", uom="CS", qty="10")
    insert_canonical_lines([row])
    calculate_and_wait(run_id)
    content = b"PROGRAM,PERIOD,REBATE_PAID\nPG-IDEM-1,2026-01,15.00\n"
    up = httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": synthetic_customer, "kind": "legacy_rebate"},
        files={"file": ("legacy.csv", io.BytesIO(content), "text/csv")},
        timeout=30,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = httpx.get(f"{API_BASE}/v1/ingest-runs/{up.json()['data']['ingestRunId']}", timeout=10)
        if r.json()["data"].get("isSettled"):
            break
        time.sleep(0.2)
    compute_variances_and_wait(run_id, up.json()["data"]["sourceFileId"])

    key = f"idem-{uuid.uuid4().hex}"
    first = _attribute(run_id, idempotency_key=key)
    assert first.status_code == 202
    second = _attribute(run_id, idempotency_key=key)
    assert second.status_code == 202
    assert first.json() == second.json()

    attribute_and_wait(run_id)
    with admin_conn() as conn:
        cur = conn.execute(
            "select count(*) from variance_line_links where ingest_run_id = %s", (run_id,)
        )
        fetched = cur.fetchone()
        assert fetched is not None
        count = fetched[0]
    assert count == 1, "replaying the same key must not duplicate links"


def test_rerunning_attribution_is_idempotent_and_replaces_rather_than_accumulates(
    live_api: None, synthetic_customer: str
) -> None:
    del live_api
    make_reference(synthetic_customer, "RerunBrand", brand_values=["RerunBrand"])
    make_program(
        synthetic_customer,
        name="Rerun program",
        brand_manufacturer="RerunBrand",
        rate="1.000000",
        external_ref="PG-RERUN-1",
    )
    run_id = make_ingest_run(synthetic_customer)
    row = canonical_line(run_id, brand_manufacturer="RerunBrand", uom="CS", qty="10")
    insert_canonical_lines([row])
    calculate_and_wait(run_id)
    content = b"PROGRAM,PERIOD,REBATE_PAID\nPG-RERUN-1,2026-01,15.00\n"
    up = httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": synthetic_customer, "kind": "legacy_rebate"},
        files={"file": ("legacy.csv", io.BytesIO(content), "text/csv")},
        timeout=30,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = httpx.get(f"{API_BASE}/v1/ingest-runs/{up.json()['data']['ingestRunId']}", timeout=10)
        if r.json()["data"].get("isSettled"):
            break
        time.sleep(0.2)
    compute_variances_and_wait(run_id, up.json()["data"]["sourceFileId"])

    first_summary = attribute_and_wait(run_id, idempotency_key=f"rerun1-{uuid.uuid4().hex}")
    second_summary = attribute_and_wait(run_id, idempotency_key=f"rerun2-{uuid.uuid4().hex}")
    assert first_summary["linkCount"] == second_summary["linkCount"] == 1
