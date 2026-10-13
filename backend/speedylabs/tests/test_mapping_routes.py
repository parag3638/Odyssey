"""mvp.md 5.4 - draft persistence and the mapping routes, against the live stack."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx

from src.features.canonical.model import CANONICAL_SCHEMA_VERSION, REQUIRED_FIELD_NAMES
from tests import live
from tests.live import ADMIN_DB_URL, API_BASE, SAMPLE_CSV, db, upload_and_settle
from tests.mappings import CSV_MAPPING

live_api = live.live_api
customer = live.customer


def _get(file_id: str) -> httpx.Response:
    return httpx.get(f"{API_BASE}/v1/source-files/{file_id}/mapping", timeout=15)


def _patch(file_id: str, body: dict[str, Any]) -> httpx.Response:
    return httpx.patch(f"{API_BASE}/v1/source-files/{file_id}/mapping", json=body, timeout=30)


def _validate(file_id: str) -> httpx.Response:
    return httpx.post(f"{API_BASE}/v1/source-files/{file_id}/mapping/validate", timeout=60)


def _mapped_file(customer_id: str, fields: dict[str, Any] | None = None) -> str:
    run = upload_and_settle(SAMPLE_CSV, customer_id=customer_id)
    file_id = str(run["sourceFileId"])
    if fields:
        assert _patch(file_id, {"fields": fields}).status_code == 200
    return file_id


# --------------------------------------------------------------------------------------
# GET
# --------------------------------------------------------------------------------------


def test_a_never_mapped_file_returns_an_empty_draft_not_a_404(customer: str) -> None:
    """ "Nobody has mapped this yet" is a state the UI renders, not a missing resource."""
    file_id = _mapped_file(customer)
    response = _get(file_id)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["fields"] == {}
    assert data["validations"] == []
    assert data["layoutVersion"] == 1
    assert data["canonicalSchemaVersion"] == CANONICAL_SCHEMA_VERSION
    assert set(data["unmappedRequired"]) == set(REQUIRED_FIELD_NAMES)


def test_an_unknown_file_is_a_404_with_no_internals(customer: str) -> None:
    del customer
    response = _get(str(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    body = response.text.lower()
    for leak in ("select", "mapping_drafts", "traceback", "psycopg", "/app"):
        assert leak not in body, leak


# --------------------------------------------------------------------------------------
# PATCH: partial semantics
# --------------------------------------------------------------------------------------


def test_a_patch_contains_exactly_that_field_and_leaves_the_others_alone(
    customer: str,
) -> None:
    file_id = _mapped_file(customer)

    first = _patch(file_id, {"fields": {"invoice_no": CSV_MAPPING["invoice_no"]}})
    assert first.status_code == 200, first.text
    assert list(first.json()["data"]["fields"]) == ["invoice_no"]

    second = _patch(file_id, {"fields": {"invoice_date": CSV_MAPPING["invoice_date"]}})
    assert second.status_code == 200
    assert sorted(second.json()["data"]["fields"]) == ["invoice_date", "invoice_no"]

    stored = _get(file_id).json()["data"]["fields"]
    assert stored["invoice_no"]["sourceColumn"] == "IVHDR01"
    assert stored["invoice_date"]["transform"]["kind"] == "cyymmdd_to_date"


def test_repatching_a_field_replaces_only_that_entry(customer: str) -> None:
    file_id = _mapped_file(customer, {"invoice_no": CSV_MAPPING["invoice_no"]})

    _patch(
        file_id,
        {"fields": {"invoice_no": {"sourceColumn": "BILTNO", "transform": {"kind": "upper"}}}},
    )
    entry = _get(file_id).json()["data"]["fields"]["invoice_no"]
    assert entry["sourceColumn"] == "BILTNO"
    assert entry["transform"] == {"kind": "upper"}


def test_the_stored_transform_is_the_registrys_normal_form_not_the_clients(
    customer: str,
) -> None:
    """`map_values` is accepted as an object for convenience and stored as pairs, which
    is the shape P6's model must emit and the shape `parse` round-trips."""
    file_id = _mapped_file(customer)
    _patch(
        file_id,
        {
            "fields": {
                "uom": {
                    "sourceColumn": "UMCODE",
                    "transform": {"kind": "map_values", "table": {"CS": "CS"}},
                }
            }
        },
    )
    transform = _get(file_id).json()["data"]["fields"]["uom"]["transform"]
    assert transform == {"kind": "map_values", "table": [{"from": "CS", "to": "CS"}]}


def test_origin_defaults_to_manual_and_is_stored(customer: str) -> None:
    file_id = _mapped_file(customer, {"invoice_no": CSV_MAPPING["invoice_no"]})
    assert _get(file_id).json()["data"]["fields"]["invoice_no"]["origin"] == "manual"


def test_deleting_a_field_removes_only_that_one(customer: str) -> None:
    file_id = _mapped_file(
        customer,
        {"invoice_no": CSV_MAPPING["invoice_no"], "uom": CSV_MAPPING["uom"]},
    )
    response = httpx.request(
        "DELETE",
        f"{API_BASE}/v1/source-files/{file_id}/mapping",
        params={"fields": ["uom"]},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    assert list(response.json()["data"]["fields"]) == ["invoice_no"]


# --------------------------------------------------------------------------------------
# PATCH: denials
# --------------------------------------------------------------------------------------


def test_a_field_that_is_not_canonical_is_refused_naming_it(customer: str) -> None:
    file_id = _mapped_file(customer)
    response = _patch(
        file_id,
        {"fields": {"invoiceNumber": {"sourceColumn": "IVHDR01", "transform": {"kind": "trim"}}}},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "unknown_canonical_field"
    assert "invoiceNumber" in error["fields"]
    assert _get(file_id).json()["data"]["fields"] == {}, "nothing was stored"


def test_a_source_column_absent_from_the_file_is_refused_naming_it(customer: str) -> None:
    file_id = _mapped_file(customer)
    response = _patch(
        file_id,
        {"fields": {"invoice_no": {"sourceColumn": "NOPE", "transform": {"kind": "trim"}}}},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "unknown_source_column"
    assert "NOPE" in error["fields"]["invoice_no"]
    assert _get(file_id).json()["data"]["fields"] == {}


def test_an_unknown_key_in_an_entry_is_a_malformed_request(customer: str) -> None:
    file_id = _mapped_file(customer)
    response = _patch(
        file_id,
        {"fields": {"invoice_no": {"sourceColumn": "IVHDR01", "surprise": 1}}},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "malformed_request"


def test_a_transform_outside_the_closed_set_is_refused(customer: str) -> None:
    file_id = _mapped_file(customer)
    for kind in ("eval", "lambda x: os.system(x)", "os.system"):
        response = _patch(
            file_id,
            {"fields": {"invoice_no": {"sourceColumn": "IVHDR01", "transform": {"kind": kind}}}},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_transform"
    assert _get(file_id).json()["data"]["fields"] == {}


def test_an_empty_field_list_is_refused(customer: str) -> None:
    file_id = _mapped_file(customer)
    assert _patch(file_id, {"fields": {}}).status_code == 400


def test_patching_a_materialised_run_is_locked(customer: str) -> None:
    run = upload_and_settle(SAMPLE_CSV, customer_id=customer)
    file_id = str(run["sourceFileId"])
    with db(ADMIN_DB_URL) as conn:
        conn.execute("update ingest_runs set materialized_at = now() where id = %s", (run["id"],))

    response = _patch(file_id, {"fields": {"invoice_no": CSV_MAPPING["invoice_no"]}})

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "mapping_locked"
    assert "new run" in error["message"]


# --------------------------------------------------------------------------------------
# Concurrency: the merge is one statement
# --------------------------------------------------------------------------------------


def test_two_concurrent_patches_to_different_fields_both_survive(customer: str) -> None:
    """5.4's failure criterion. Read-modify-write in the application would lose one of
    these; `mapping || :patch` in a single statement cannot.

    Sent concurrently from two connections rather than simulated, because the thing
    under test is what the database does when two transactions overlap.
    """
    file_id = _mapped_file(customer)

    async def _both() -> tuple[int, int]:
        async with httpx.AsyncClient(timeout=30) as client:
            first, second = await asyncio.gather(
                client.patch(
                    f"{API_BASE}/v1/source-files/{file_id}/mapping",
                    json={"fields": {"invoice_no": CSV_MAPPING["invoice_no"]}},
                ),
                client.patch(
                    f"{API_BASE}/v1/source-files/{file_id}/mapping",
                    json={"fields": {"uom": CSV_MAPPING["uom"]}},
                ),
            )
            return first.status_code, second.status_code

    first_status, second_status = asyncio.run(_both())
    assert (first_status, second_status) == (200, 200)

    stored = _get(file_id).json()["data"]["fields"]
    assert sorted(stored) == ["invoice_no", "uom"], f"one edit was lost: {sorted(stored)}"


def test_many_concurrent_patches_all_survive(customer: str) -> None:
    """The same property at a scale where a lost update would be likely rather than
    lucky: sixteen fields patched at once, one request each."""
    file_id = _mapped_file(customer)

    async def _all() -> list[int]:
        async with httpx.AsyncClient(timeout=60) as client:
            responses = await asyncio.gather(
                *(
                    client.patch(
                        f"{API_BASE}/v1/source-files/{file_id}/mapping",
                        json={"fields": {name: entry}},
                    )
                    for name, entry in CSV_MAPPING.items()
                )
            )
            return [response.status_code for response in responses]

    assert set(_run(_all())) == {200}
    stored = _get(file_id).json()["data"]["fields"]
    assert sorted(stored) == sorted(CSV_MAPPING), (
        f"{len(CSV_MAPPING) - len(stored)} edits were lost"
    )


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


# --------------------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------------------


def test_validating_an_incomplete_mapping_names_the_missing_required_fields(
    customer: str,
) -> None:
    file_id = _mapped_file(customer, {"invoice_no": CSV_MAPPING["invoice_no"]})
    response = _validate(file_id)

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "mapping_incomplete"
    assert "customer_id" in error["fields"]
    assert "invoice_no" not in error["fields"], "the one mapped field is not missing"


def test_validating_the_hand_written_mapping_passes_every_field(customer: str) -> None:
    """The browser milestone's backend half: a whole file mapped by hand, no LLM, with
    real verdicts."""
    file_id = _mapped_file(customer, CSV_MAPPING)
    response = _validate(file_id)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["checkedRows"] == 500
    assert len(data["validations"]) == 17, "16 fields plus the identity"

    by_field = {v["canonicalField"]: v for v in data["validations"]}
    failing = {name: v["verdict"] for name, v in by_field.items() if v["verdict"] == "fail"}
    assert failing == {}, failing

    identity = by_field["ext_amount_identity"]
    assert identity["verdict"] == "warn"
    assert identity["failedRows"] == 2
    assert identity["exampleRowNumbers"] == [134, 189]


def test_verdicts_are_stored_and_come_back_from_a_later_get(customer: str) -> None:
    file_id = _mapped_file(customer, CSV_MAPPING)
    assert _validate(file_id).status_code == 200

    data = _get(file_id).json()["data"]
    assert len(data["validations"]) == 17
    assert all(
        v["checkedRows"] == 500
        for v in data["validations"]
        if v["canonicalField"] != "ext_amount_identity"
    )


def test_revalidating_replaces_the_previous_verdicts_rather_than_appending(
    customer: str,
) -> None:
    file_id = _mapped_file(customer, CSV_MAPPING)
    assert _validate(file_id).status_code == 200
    assert _validate(file_id).status_code == 200

    assert len(_get(file_id).json()["data"]["validations"]) == 17, (
        "a second run must replace the first, not add to it"
    )


def test_a_verdict_for_a_field_that_is_no_longer_mapped_is_dropped(customer: str) -> None:
    """A stale verdict beside a live one is worse than no verdict: a reader cannot tell
    which run it belongs to."""
    file_id = _mapped_file(customer, CSV_MAPPING)
    assert _validate(file_id).status_code == 200
    assert "deviated_price" in {
        v["canonicalField"] for v in _get(file_id).json()["data"]["validations"]
    }

    httpx.request(
        "DELETE",
        f"{API_BASE}/v1/source-files/{file_id}/mapping",
        params={"fields": ["deviated_price"]},
        timeout=15,
    )
    assert _validate(file_id).status_code == 200

    remaining = {v["canonicalField"] for v in _get(file_id).json()["data"]["validations"]}
    assert "deviated_price" not in remaining
