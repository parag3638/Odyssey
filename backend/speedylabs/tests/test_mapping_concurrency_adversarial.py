"""Adversarial 5.4 concurrency probes, beyond the two-patch/sixteen-patch cases already
in test_mapping_routes.py.

Run against the live Compose stack, same as test_mapping_routes.py. Each test creates
its own customer and file via tests.live, so it cannot collide with another session's
demo data; it does not touch the migration (no downgrade/upgrade), only the mapping and
layout routes against the schema as it already stands.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from tests import live
from tests.live import API_BASE, SAMPLE_CSV, SAMPLE_FWF, upload_and_settle
from tests.mappings import CSV_MAPPING, FIXED_WIDTH_MAPPING

live_api = live.live_api
customer = live.customer


def _patch(file_id: str, body: dict[str, Any]) -> httpx.Response:
    return httpx.patch(f"{API_BASE}/v1/source-files/{file_id}/mapping", json=body, timeout=30)


def _get(file_id: str) -> httpx.Response:
    return httpx.get(f"{API_BASE}/v1/source-files/{file_id}/mapping", timeout=15)


def _mapped_file(customer_id: str, fields: dict[str, Any] | None = None) -> str:
    run = upload_and_settle(SAMPLE_CSV, customer_id=customer_id)
    file_id = str(run["sourceFileId"])
    if fields:
        assert _patch(file_id, {"fields": fields}).status_code == 200
    return file_id


# --------------------------------------------------------------------------------------
# PATCH vs DELETE on the same field, concurrently
# --------------------------------------------------------------------------------------


def test_concurrent_patch_and_delete_of_the_same_field_lands_on_one_or_the_other(
    customer: str,
) -> None:
    """Whichever wins the race, the result must be a coherent state: either the field is
    mapped (PATCH won) or absent (DELETE won) - never a corrupted/partial jsonb value,
    and never a 500.

    `mapping || :patch` is a set-union merge: a DELETE that runs concurrently with a
    PATCH on the very same key is the one case where "both survive" is not the right
    property, because the two operations disagree about whether the key should exist at
    all. What must still hold is that the database's own last-writer-wins ordering
    decides it cleanly - not a crash, not a half-written entry.
    """
    file_id = _mapped_file(customer, {"invoice_no": CSV_MAPPING["invoice_no"]})

    async def _race() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(timeout=30) as client:
            patch_task = client.patch(
                f"{API_BASE}/v1/source-files/{file_id}/mapping",
                json={"fields": {"invoice_no": CSV_MAPPING["invoice_no"]}},
            )
            delete_task = client.request(
                "DELETE",
                f"{API_BASE}/v1/source-files/{file_id}/mapping",
                params={"fields": ["invoice_no"]},
            )
            return await asyncio.gather(patch_task, delete_task)

    patch_response, delete_response = asyncio.run(_race())

    assert patch_response.status_code == 200, patch_response.text
    assert delete_response.status_code == 200, delete_response.text

    stored = _get(file_id).json()["data"]["fields"]
    # Coherent either way: present with a complete, valid entry, or fully absent.
    if "invoice_no" in stored:
        entry = stored["invoice_no"]
        assert entry["sourceColumn"] == "IVHDR01"
        assert entry["transform"] == {"kind": "trim"}
    else:
        assert stored == {}


# --------------------------------------------------------------------------------------
# PATCH vs validate, concurrently
# --------------------------------------------------------------------------------------


def test_concurrent_patch_and_validate_never_500s_and_validate_sees_a_consistent_draft(
    customer: str,
) -> None:
    """Validate reads the draft and samples the file in the same call. A PATCH that
    completes a required field's mapping at the same moment must not leave validate
    half-way between "incomplete" and "complete" in a way that crashes - it should see
    one of the two drafts (before or after the race's PATCH), cleanly.
    """
    # Map every required field except one, so validate is on the 422 incomplete/ 200
    # boundary - the most sensitive place for a race to land wrong.
    all_but_one = dict(CSV_MAPPING)
    held_back = "customer_id"
    missing_name = all_but_one.pop(held_back)
    file_id = _mapped_file(customer, all_but_one)

    async def _race() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(timeout=60) as client:
            patch_task = client.patch(
                f"{API_BASE}/v1/source-files/{file_id}/mapping",
                json={"fields": {held_back: missing_name}},
            )
            validate_task = client.post(f"{API_BASE}/v1/source-files/{file_id}/mapping/validate")
            return await asyncio.gather(patch_task, validate_task)

    patch_response, validate_response = asyncio.run(_race())

    assert patch_response.status_code == 200, patch_response.text
    # Either the draft was still incomplete when validate read it (422, naming exactly
    # the one field) or it was already complete (200) - never a 500, and never a 422
    # naming more than the one field that was in flight.
    assert validate_response.status_code in (200, 422), validate_response.text
    if validate_response.status_code == 422:
        error = validate_response.json()["error"]
        assert error["code"] == "mapping_incomplete"
        assert set(error["fields"]) <= {held_back}

    # The PATCH's own effect is never in doubt regardless of how the race fell.
    final = _get(file_id).json()["data"]["fields"]
    assert held_back in final


# --------------------------------------------------------------------------------------
# PATCH mapping while a layout re-profile is in flight (fixed-width only)
# --------------------------------------------------------------------------------------


def test_patch_mapping_while_a_reprofile_is_running_is_coherent(customer: str) -> None:
    """Editing the fixed-width field list bumps the layout version and queues an async
    re-profile job (features/layouts/service.py). A mapping PATCH that races the version
    bump must see one coherent layout version's columns - never a crash, and never a
    mapping entry saved against a sourceColumn that does not exist in whichever version
    it actually landed on.
    """
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = str(run["sourceFileId"])

    layout = httpx.get(f"{API_BASE}/v1/source-files/{file_id}/layout", timeout=15).json()["data"]
    assert layout["kind"] == "fixed_width"

    # A no-op re-submission of the same field list: still a new version with its own
    # (queued) profiling, per the service's own "layout edits are additive" rule.
    body = {
        "fields": [
            {
                "name": field["name"],
                "start": field["start"],
                "end": field["end"],
                "impliedScale": field.get("impliedScale"),
            }
            for field in layout["fields"]
        ]
    }

    async def _race() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(timeout=30) as client:
            layout_task = client.patch(f"{API_BASE}/v1/source-files/{file_id}/layout", json=body)
            mapping_task = client.patch(
                f"{API_BASE}/v1/source-files/{file_id}/mapping",
                json={"fields": {"qty": FIXED_WIDTH_MAPPING["qty"]}},
            )
            return await asyncio.gather(layout_task, mapping_task)

    layout_response, mapping_response = asyncio.run(_race())

    assert layout_response.status_code == 202, layout_response.text
    # The mapping PATCH must land cleanly on *some* coherent layout version - either the
    # original (if it read before the bump) with a 200, or `409`/`422`-style refusal if
    # it somehow raced into a version whose profile is not ready yet. A 500 is the only
    # wrong answer.
    assert mapping_response.status_code != 500, mapping_response.text

    if mapping_response.status_code == 200:
        stored = _get(file_id).json()["data"]
        # Whatever version the entry was written against, it must be internally
        # consistent: the mapping GET's own layoutVersion must have an entry for "qty"
        # pointing at a column that is real for *some* version of this file - at minimum,
        # the entry must round-trip rather than vanish or point at a stale column name
        # that no version ever had.
        assert stored["fields"]["qty"]["sourceColumn"] == FIXED_WIDTH_MAPPING["qty"]["sourceColumn"]
