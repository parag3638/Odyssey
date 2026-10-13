"""Shared scaffolding for P12 (legacy import + variance) tests, in `tests.rebate_fixtures`'s own
style: synthetic customers built through the admin role, smaller and more targeted than the
48,150-row demo file. Reused across the legacy-import and variance-compute suites rather than
copy-pasted, per the rule-of-two (BACKEND.md §9).
"""

from __future__ import annotations

import io
import time
import uuid
from datetime import date

import httpx

from tests.live import ADMIN_DB_URL, API_BASE, db
from tests.rebate_fixtures import (
    admin_conn,
    canonical_line,
    insert_canonical_lines,
    make_ingest_run,
    make_program,
    make_reference,
    synthetic_customer,
)

__all__ = [
    "ADMIN_DB_URL",
    "API_BASE",
    "admin_conn",
    "canonical_line",
    "db",
    "insert_canonical_lines",
    "legacy_csv_bytes",
    "make_ingest_run",
    "make_program",
    "make_reference",
    "post_legacy_upload",
    "setup_calculated_program",
    "synthetic_customer",
    "upload_and_wait_imported",
    "wait_run_status",
]


def legacy_csv_bytes(rows: list[tuple[str, str, str]], *, program_name: str | None = None) -> bytes:
    """`[(program_external_ref, period "YYYY-MM", amount), ...]` -> the five/four-column CSV
    the reader understands. `program_name` adds a constant `PROGRAM_NAME` column when given."""
    header = "PROGRAM,PERIOD,REBATE_PAID" + (",PROGRAM_NAME" if program_name is not None else "")
    lines = [header]
    for ref, period, amount in rows:
        line = f"{ref},{period},{amount}"
        if program_name is not None:
            line += f",{program_name}"
        lines.append(line)
    return ("\n".join(lines) + "\n").encode()


def post_legacy_upload(
    content: bytes, *, customer_id: str, filename: str = "legacy.csv"
) -> httpx.Response:
    return httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": customer_id, "kind": "legacy_rebate"},
        files={"file": (filename, io.BytesIO(content), "text/csv")},
        timeout=30,
    )


def wait_run_status(run_id: str, *, budget_s: float = 10) -> dict[str, object]:
    """Poll `GET /v1/ingest-runs/{id}` until it is settled (any terminal status), return the run."""
    deadline = time.monotonic() + budget_s
    run: dict[str, object] = {}
    while time.monotonic() < deadline:
        resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}", timeout=10)
        run = resp.json()["data"]
        if run.get("isSettled"):
            return run
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not settle within {budget_s}s: {run}")


def upload_and_wait_imported(
    content: bytes, *, customer_id: str, budget_s: float = 10
) -> dict[str, object]:
    resp = post_legacy_upload(content, customer_id=customer_id)
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["data"]["ingestRunId"]
    run = wait_run_status(run_id, budget_s=budget_s)
    return {**run, "sourceFileId": resp.json()["data"]["sourceFileId"]}


def setup_calculated_program(
    customer_id: str,
    *,
    external_ref: str,
    brand: str,
    rate: str = "1.000000",
    months: list[date] | None = None,
    qty: str = "10",
) -> tuple[str, str]:
    """A reference row, one program carrying `external_ref`, a materialized run with one
    canonical line per month in `months` (default: just January 2026), and its rebates
    calculated through the real route. Returns `(run_id, program_id)`."""
    make_reference(customer_id, brand, brand_values=[brand])
    program_id = make_program(
        customer_id,
        name=f"{brand} program",
        brand_manufacturer=brand,
        rate=rate,
        external_ref=external_ref,
    )
    run_id = make_ingest_run(customer_id)
    for month in months or [date(2026, 1, 15)]:
        insert_canonical_lines(
            [
                canonical_line(
                    run_id, brand_manufacturer=brand, uom="CS", qty=qty, invoice_date=month
                )
            ]
        )
    _calculate_and_wait(run_id)
    return run_id, program_id


def _calculate_and_wait(run_id: str, *, budget_s: float = 10) -> None:
    from tests.live import reset_rate_limits

    reset_rate_limits()
    resp = httpx.post(
        f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-calcs",
        headers={"Idempotency-Key": f"setup-{uuid.uuid4().hex}"},
        timeout=10,
    )
    assert resp.status_code == 202, resp.text
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        summary = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-summary", timeout=10)
        if summary.status_code == 200 and summary.json()["data"]["calculated"]:
            return
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish calculating within {budget_s}s")
