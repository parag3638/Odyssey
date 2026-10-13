"""Attack item 5: concurrency, with two real connections. Two simultaneous spec approvals
for the same `(customer_id, kind)` (one `201`, one `409 spec_conflict`, exactly one `approved`
row afterwards) and two simultaneous drift decisions on the same run.

Run against the live Compose stack (`docker compose up -d`). Two real OS threads, each on its
own connection, released at the same instant via a `threading.Barrier` - a mocked race proves
nothing about `mapping_specs`' partial unique index or `drift_already_decided`'s refusal.
"""

from __future__ import annotations

from typing import Any

import httpx

from tests import live
from tests.live import API_BASE, FIXTURES, upload_and_settle
from tests.mappings import CSV_MAPPING

live_api = live.live_api
customer = live.customer

MONTH2_NODRIFT_CSV = FIXTURES / "month2_nodrift_invoices.csv"
MONTH2_DRIFTED_CSV = FIXTURES / "month2_invoices.csv"


def _validate(file_id: str) -> httpx.Response:
    return httpx.post(f"{API_BASE}/v1/source-files/{file_id}/mapping/validate", timeout=30)


def _mapped_and_validated_file(
    customer_id: str, fixture: Any, mapping: dict[str, Any] | None = None
) -> str:
    run = upload_and_settle(fixture, customer_id=customer_id)
    file_id = str(run["sourceFileId"])
    response = httpx.patch(
        f"{API_BASE}/v1/source-files/{file_id}/mapping",
        json={"fields": mapping if mapping is not None else CSV_MAPPING},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    assert _validate(file_id).status_code == 200
    return file_id


def test_two_concurrent_first_time_approvals_leave_exactly_one_approved_spec(
    customer: str,
) -> None:
    """Neither file has anything approved yet for this customer, so both approvals race to
    write a spec under the partial unique index `(customer_id, kind) where status =
    'approved'`. The two mappings differ (one field's transform), so this is a real content
    race, not the 'approving the same thing twice' idempotent-200 path.

    Two OS threads (not `asyncio.gather` on one event loop, which under load can serialise two
    awaits before either request leaves the process) release at the same instant via a
    `threading.Barrier`, each on its own real connection - the plan's "two real connections".
    Whether that produces a genuine simultaneous write (one `201` + one `409 spec_conflict`) or
    the DB happens to serialise them into v1 then v2 (two `201`s), the invariant that must hold
    either way is the database fact: never two approved rows, never a 500, never a lost write.
    """
    different_mapping = dict(CSV_MAPPING)
    different_mapping["mfr_item_code"] = {
        "sourceColumn": "MFGPRT",
        "transform": {"kind": "upper"},
    }
    file_a = _mapped_and_validated_file(customer, live.SAMPLE_CSV)
    file_b = _mapped_and_validated_file(customer, MONTH2_NODRIFT_CSV, different_mapping)

    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2)

    def _approve(file_id: str, note: str) -> httpx.Response:
        with httpx.Client(timeout=30) as client:
            barrier.wait()
            return client.post(
                f"{API_BASE}/v1/mapping-specs",
                json={"sourceFileId": file_id, "note": note, "force": False},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_approve, file_a, "race A")
        future_b = pool.submit(_approve, file_b, "race B")
        response_a, response_b = future_a.result(), future_b.result()

    statuses = sorted([response_a.status_code, response_b.status_code])
    assert statuses in ([201, 201], [201, 409]), (
        f"expected either two clean sequential versions or one winner plus one conflict, got "
        f"{response_a.status_code} / {response_b.status_code} "
        f"({response_a.text!r}, {response_b.text!r})"
    )
    assert 500 not in statuses

    if statuses == [201, 409]:
        loser = response_a if response_a.status_code == 409 else response_b
        assert loser.json()["error"]["code"] == "spec_conflict"
    else:
        # Both committed: the database's own serialisation must have given them different
        # version numbers - two 201s for the *same* version would be the real bug.
        version_a = response_a.json()["data"]["version"]
        version_b = response_b.json()["data"]["version"]
        assert version_a != version_b, (
            f"BUG: both approvals landed 201 with the same version ({version_a}) - the partial "
            "unique index on 'approved' should have refused one of them"
        )

    listing = httpx.get(
        f"{API_BASE}/v1/customers/{customer}/mapping-specs", params={"kind": "csv"}, timeout=10
    ).json()["data"]
    approved = [row for row in listing if row["status"] == "approved"]
    assert len(approved) == 1, f"expected exactly one approved spec row, found {len(approved)}"


def test_two_concurrent_drift_decisions_on_the_same_run_leave_exactly_one_decision(
    customer: str,
) -> None:
    """A run blocked by drift, decided twice at once: one call must win, the other must be
    refused (`409 drift_already_decided` or `409 spec_conflict`, both honest refusals for a
    second decision), and afterwards the run carries exactly one decision, not a
    half-applied one."""
    baseline_file = _mapped_and_validated_file(customer, live.SAMPLE_CSV)
    spec_response = httpx.post(
        f"{API_BASE}/v1/mapping-specs",
        json={"sourceFileId": baseline_file, "note": "baseline", "force": False},
        timeout=15,
    )
    assert spec_response.status_code == 201, spec_response.text

    run = upload_and_settle(MONTH2_DRIFTED_CSV, customer_id=customer)
    assert run["status"] == "drift_blocked", run

    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2)

    def _reject(note: str) -> httpx.Response:
        with httpx.Client(timeout=30) as client:
            barrier.wait()
            return client.post(
                f"{API_BASE}/v1/ingest-runs/{run['id']}/drift/reject", json={"note": note}
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_1 = pool.submit(_reject, "reject race 1")
        future_2 = pool.submit(_reject, "reject race 2")
        response_1, response_2 = future_1.result(), future_2.result()
    statuses = sorted([response_1.status_code, response_2.status_code])
    assert statuses == [200, 409], (
        f"expected exactly one winner (200) and one refusal (409), got "
        f"{response_1.status_code} / {response_2.status_code} "
        f"({response_1.text!r}, {response_2.text!r})"
    )
    loser = response_1 if response_1.status_code == 409 else response_2
    assert loser.json()["error"]["code"] in ("drift_already_decided", "spec_conflict")

    final_run = httpx.get(f"{API_BASE}/v1/ingest-runs/{run['id']}", timeout=10).json()["data"]
    assert final_run["status"] == "rejected"
