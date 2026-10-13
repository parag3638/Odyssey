"""Shared scaffolding for P13 (attribution) tests, built the way
`tests.legacy_variance_fixtures` builds P12's: synthetic customers through the admin role,
smaller and more targeted than the 48,150-row demo file.
"""

from __future__ import annotations

import time
import uuid
from datetime import date
from typing import Any

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
    post_legacy_upload,
    synthetic_customer,
    upload_and_wait_imported,
    wait_run_status,
)
from tests.live import reset_rate_limits
from tests.rebate_fixtures import canonical_line, insert_canonical_lines

__all__ = [
    "ADMIN_DB_URL",
    "API_BASE",
    "admin_conn",
    "api_reachable",
    "attribute_and_wait",
    "calculate_and_wait",
    "canonical_line",
    "compute_variances_and_wait",
    "db",
    "fetch_attribution_summary",
    "find_attributed_csv_run",
    "insert_canonical_lines",
    "legacy_csv_bytes",
    "make_ingest_run",
    "make_program",
    "make_reference",
    "post_legacy_upload",
    "synthetic_customer",
    "upload_and_wait_imported",
    "wait_run_status",
]


def api_reachable() -> bool:
    try:
        httpx.get(f"{API_BASE}/healthz", timeout=2)
        return True
    except httpx.HTTPError:
        return False


def find_attributed_csv_run() -> str | None:
    """An ingest run with links written, sourced from a CSV file - the shape the committed
    demo seed produces. Looked up rather than hardcoded (`docs/progress.md`: "a committed e2e
    must not hardcode a run id" - a gate run wipes and reseeds the demo customer, changing
    every id)."""
    import psycopg

    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select r.id
            from ingest_runs r
            join source_files sf on sf.id = r.source_file_id
            where sf.format = 'csv'
              and exists (select 1 from variance_line_links k where k.ingest_run_id = r.id)
            order by r.created_at desc
            limit 1
            """
        )
        row = cur.fetchone()
        return None if row is None else str(row[0])


def calculate_and_wait(run_id: str, *, budget_s: float = 10) -> None:
    reset_rate_limits()
    resp = httpx.post(
        f"{API_BASE}/v1/ingest-runs/{run_id}/rebate-calcs",
        headers={"Idempotency-Key": f"calc-{uuid.uuid4().hex}"},
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


def compute_variances_and_wait(
    run_id: str, legacy_source_file_id: str, *, budget_s: float = 10
) -> dict[str, Any]:
    reset_rate_limits()
    resp = httpx.post(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances",
        json={"legacySourceFileId": legacy_source_file_id},
        headers={"Idempotency-Key": f"cv-{uuid.uuid4().hex}"},
        timeout=10,
    )
    assert resp.status_code == 202, resp.text
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        summary = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variance-summary", timeout=10)
        if summary.status_code == 200:
            return summary.json()["data"]
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish comparing within {budget_s}s")


def attribute_and_wait(
    run_id: str, *, idempotency_key: str | None = None, budget_s: float = 10
) -> dict[str, Any]:
    reset_rate_limits()
    resp = httpx.post(
        f"{API_BASE}/v1/ingest-runs/{run_id}/variances/attribute",
        headers={"Idempotency-Key": idempotency_key or f"attr-{uuid.uuid4().hex}"},
        timeout=10,
    )
    assert resp.status_code == 202, resp.text
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        summary = httpx.get(
            f"{API_BASE}/v1/ingest-runs/{run_id}/variance-summary/attribution", timeout=10
        )
        if summary.status_code == 200 and summary.json()["data"]["attributed"]:
            return summary.json()["data"]
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish attributing within {budget_s}s")


def fetch_attribution_summary(run_id: str) -> dict[str, Any]:
    resp = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/variance-summary/attribution", timeout=10)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def legacy_upload_bytes(rows: list[tuple[str, str, str]]) -> bytes:
    return legacy_csv_bytes(rows)


def upload_legacy_and_wait(content: bytes, *, customer_id: str) -> dict[str, Any]:
    return upload_and_wait_imported(content, customer_id=customer_id)


def set_customer_cutoff_day(customer_id: str, day: date | None) -> None:
    with admin_conn() as conn:
        conn.execute(
            "update customers set legacy_period_cutoff_day = %s where id = %s",
            (day, customer_id),
        )


def set_program_redistributor_rate(program_id: str, rate: str | None) -> None:
    with admin_conn() as conn:
        conn.execute(
            "update rebate_programs set redistributor_rate = %s where id = %s",
            (rate, program_id),
        )
