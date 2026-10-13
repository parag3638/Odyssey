"""Attack item 2: "the no-drift control is load-bearing. It must reach `mapped` with zero
blocking findings while still showing distributional warnings. Try to break it: a file with an
added unmapped column, a column that becomes entirely empty, a column with 20-25 distinct
values."

End to end against the real, live API (`docker compose up -d`), because the claim under test is
about the whole pipeline (parse -> profile -> drift -> status), not about one pure function.

Flow for every case: upload month 1 (CSV), map all 16 required fields by hand (the same mapping
`tests/mappings.py` uses elsewhere), validate, approve as spec v1 - then upload a mutated
`month2_nodrift_invoices.csv` and check it still lands `mapped` with zero blocking drift
findings.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests import live
from tests.live import API_BASE, FIXTURES, SAMPLE_CSV, reset_rate_limits
from tests.mappings import CSV_MAPPING

live_api = live.live_api
customer = live.customer

MONTH2_NODRIFT_CSV = FIXTURES / "month2_nodrift_invoices.csv"


def _patch_mapping(file_id: str) -> None:
    response = httpx.patch(
        f"{API_BASE}/v1/source-files/{file_id}/mapping",
        json={"fields": CSV_MAPPING},
        timeout=10,
    )
    assert response.status_code == 200, response.text


def _validate(file_id: str) -> None:
    response = httpx.post(f"{API_BASE}/v1/source-files/{file_id}/mapping/validate", timeout=30)
    assert response.status_code == 200, response.text


def _approve_spec(source_file_id: str, note: str) -> dict[str, Any]:
    response = httpx.post(
        f"{API_BASE}/v1/mapping-specs",
        json={"sourceFileId": source_file_id, "note": note, "force": False},
        timeout=10,
    )
    assert response.status_code in (200, 201), response.text
    return dict(response.json()["data"])


def _upload_and_settle(path: Path, *, customer_id: str, budget_s: float = 90) -> dict[str, Any]:
    import time

    reset_rate_limits()
    with path.open("rb") as handle:
        response = httpx.post(
            f"{API_BASE}/v1/uploads",
            data={"customerId": customer_id, "kind": "erp_invoice_lines"},
            files={"file": (path.name, handle, "text/plain")},
            timeout=60,
        )
    assert response.status_code == 202, response.text
    run_id = response.json()["data"]["ingestRunId"]
    deadline = time.monotonic() + budget_s
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}", timeout=10).json()["data"]
        if run["isSettled"]:
            return run
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not settle within {budget_s}s: {run}")


def _establish_baseline_spec(customer_id: str) -> None:
    """Map and approve month 1 as spec v1. Shared setup for every mutation below."""
    run = _upload_and_settle(SAMPLE_CSV, customer_id=customer_id)
    assert run["status"] == "profiled", run
    file_id = run["sourceFileId"]
    _patch_mapping(file_id)
    _validate(file_id)
    spec = _approve_spec(file_id, "initial baseline for the adversarial no-drift control")
    assert spec["status"] == "approved"
    assert spec["version"] == 1


def _rewrite_csv(
    source: Path, destination: Path, *, mutate_header: Any = None, mutate_row: Any = None
) -> None:
    with source.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)
    if mutate_header is not None:
        header = mutate_header(header)
    if mutate_row is not None:
        rows = [mutate_row(header, row) for row in rows]
    with destination.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def _drift_response(run_id: str) -> dict[str, Any]:
    response = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}/drift", timeout=10)
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def _assert_zero_blocking_and_mapped(run: dict[str, Any]) -> dict[str, Any]:
    assert run["status"] == "mapped", (
        f"expected the mutated no-drift file to still reach 'mapped' (spec applies, no blocking "
        f"drift), got status={run['status']!r} / spec={run.get('spec')} / run={run}"
    )
    drift = _drift_response(run["id"])
    assert drift["blockingCount"] == 0, (
        f"BUG: the mutated no-drift file produced a blocking drift finding - {drift['findings']}"
    )
    return drift


@pytest.fixture
def seeded_baseline(customer: str) -> str:
    _establish_baseline_spec(customer)
    return customer


def test_added_unmapped_column_stays_unblocked(seeded_baseline: str, tmp_path: Path) -> None:
    """A brand-new column the mapping never names. `column_added` always warns, never blocks -
    confirm that holds through the whole pipeline, not only in the pure structural comparison."""
    mutated = tmp_path / "month2_nodrift_added_column.csv"

    def add_column_header(header: list[str]) -> list[str]:
        return [*header, "NEWCOL99"]

    def add_column_row(_header: list[str], row: list[str]) -> list[str]:
        return [*row, "SOMEVALUE"]

    _rewrite_csv(
        MONTH2_NODRIFT_CSV, mutated, mutate_header=add_column_header, mutate_row=add_column_row
    )

    run = _upload_and_settle(mutated, customer_id=seeded_baseline)
    drift = _assert_zero_blocking_and_mapped(run)
    codes_with_findings = {f["driftCode"] for f in drift["findings"]}
    assert "column_added" in codes_with_findings, (
        "expected a column_added finding for NEWCOL99 - if this is missing, the structural "
        "comparison is not seeing the new column at all, which is a different bug"
    )
    added_finding = next(f for f in drift["findings"] if f["driftCode"] == "column_added")
    assert added_finding["severity"] == "warning"


def test_a_column_that_becomes_entirely_empty_stays_unblocked(
    seeded_baseline: str, tmp_path: Path
) -> None:
    """`MFGPRT` (mapped to `mfr_item_code`, a cited column) blanked out in every row. The
    structural `empty` rule says this must not be `type_changed`; it is a presence change,
    which `null_rate_spike` (always a warning) should carry instead."""
    mutated = tmp_path / "month2_nodrift_empty_column.csv"

    def blank_mfgprt(header: list[str], row: list[str]) -> list[str]:
        idx = header.index("MFGPRT")
        row = list(row)
        row[idx] = ""
        return row

    _rewrite_csv(MONTH2_NODRIFT_CSV, mutated, mutate_row=blank_mfgprt)

    run = _upload_and_settle(mutated, customer_id=seeded_baseline)
    drift = _assert_zero_blocking_and_mapped(run)
    by_code: dict[str, list[dict[str, Any]]] = {}
    for finding in drift["findings"]:
        by_code.setdefault(finding["driftCode"], []).append(finding)

    assert "type_changed" not in by_code or all(
        f["columnName"] != "MFGPRT" for f in by_code.get("type_changed", [])
    ), (
        "BUG: a column that went entirely empty was reported as type_changed, which the "
        "'empty is not a type change' rule exists specifically to prevent"
    )
    null_findings = by_code.get("null_rate_spike", [])
    assert any(f["columnName"] == "MFGPRT" for f in null_findings), (
        "expected MFGPRT's null-rate spike to be reported as a warning; got "
        f"null_rate_spike findings: {null_findings}"
    )


def test_a_column_with_20_to_25_distinct_values_stays_unblocked(
    seeded_baseline: str, tmp_path: Path
) -> None:
    """`FLD041` is unmapped and near-constant in the baseline. Rewriting it to cycle through 22
    distinct values lands exactly in the gap between `ENUM_MAX_DISTINCT` (20) and
    `VALUE_COUNT_CAP` (25): `value_counts` is still recorded (<=25), but the column is no longer
    treated as a de-facto enumeration by `new_enum_value`'s own baseline-side check. Either way,
    every distribution code is a warning, so this must never block - and must not 500 trying to
    read value_counts at the cap boundary."""
    mutated = tmp_path / "month2_nodrift_22_distinct.csv"
    distinct_values = [f"V{i:02d}" for i in range(22)]
    counter = {"n": 0}

    def cycle_fld041(header: list[str], row: list[str]) -> list[str]:
        idx = header.index("FLD041")
        row = list(row)
        row[idx] = distinct_values[counter["n"] % len(distinct_values)]
        counter["n"] += 1
        return row

    _rewrite_csv(MONTH2_NODRIFT_CSV, mutated, mutate_row=cycle_fld041)

    run = _upload_and_settle(mutated, customer_id=seeded_baseline)
    _assert_zero_blocking_and_mapped(run)
