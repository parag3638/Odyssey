"""mvp.md 4.3 - layout written at parse time, and the GET route.

Live tests: they drive the real stack so what is asserted is what a browser would get.
They skip rather than fail when Compose is not up.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from decimal import Decimal
from typing import Any

import httpx

from src.lib.parsers.fwf_infer import BLANK_THRESHOLD
from tests import live
from tests.live import (
    ADMIN_DB_URL,
    API_BASE,
    REPO_ROOT,
    SAMPLE_CSV,
    SAMPLE_FWF,
    db,
    get_layout,
    upload_and_settle,
)

# Rebound rather than imported by name: pytest discovers fixtures as module attributes,
# and an import that exists only to be discovered reads as dead to every linter.
live_api = live.live_api
customer = live.customer

#: Where docker-compose.yml mounts the uploads volume inside the containers.
CONTAINER_UPLOAD_DIR = "/var/lib/speedy/uploads"


def _layout_row(file_id: str) -> dict[str, Any]:
    with db(ADMIN_DB_URL) as conn, conn.cursor() as cur:
        cur.execute(
            "select version, kind, encoding, delimiter, quote_char, has_header, "
            "header_row_index, footer_row_count, record_width, inferred, edited_by_human, "
            "fields, inference_evidence from file_layouts "
            "where source_file_id = %s order by version desc",
            (file_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1, f"expected exactly one layout version, got {len(rows)}"
    columns = [
        "version",
        "kind",
        "encoding",
        "delimiter",
        "quote_char",
        "has_header",
        "header_row_index",
        "footer_row_count",
        "record_width",
        "inferred",
        "edited_by_human",
        "fields",
        "inference_evidence",
    ]
    return dict(zip(columns, rows[0], strict=True))


# --------------------------------------------------------------------------------------
# Version 1 is written at parse time
# --------------------------------------------------------------------------------------


def test_a_fixed_width_upload_gets_an_inferred_layout_with_quirks(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    assert run["status"] == "profiled", run

    row = _layout_row(run["sourceFileId"])
    assert row["version"] == 1
    assert row["kind"] == "fixed_width"
    assert row["inferred"] is True
    assert row["edited_by_human"] is False
    assert row["record_width"] == 186
    assert row["delimiter"] is None, "a fixed-width file has no delimiter to record"
    assert row["has_header"] is False, "the names live in a record layout, not the file"
    assert row["footer_row_count"] == 1, "the TRLR record"

    fields: list[dict[str, Any]] = list(row["fields"])
    assert len(fields) == 14
    assert [field["index"] for field in fields] == list(range(14))
    # Quirks per field, and at least one field carrying a real one.
    assert all("quirks" in field for field in fields)
    claimed = {quirk for field in fields for quirk in field["quirks"]}
    assert "right_padded" in claimed
    assert "trailing_sign" in claimed, "SHPUNT and LINAMT carry trailing signs"


def test_a_fixed_width_layout_claims_nothing_on_a_field_it_thinks_is_several(
    customer: str,
) -> None:
    """A transform on a slice that is really a date, a filler and an account number is
    certainly wrong, so it must not be offered as a claim."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    fields: list[dict[str, Any]] = list(_layout_row(run["sourceFileId"])["fields"])

    ambiguous = [field for field in fields if field["confidence"] == "low"]
    assert len(ambiguous) == 3, [field["index"] for field in ambiguous]
    for field in ambiguous:
        assert field["quirks"] == []
        assert field["impliedScale"] is None
        assert field["proposedScale"] is None
        assert "several fields" in field["note"]


def test_no_field_anywhere_carries_an_applied_implied_scale(customer: str) -> None:
    """Nothing in the characters distinguishes an item number from a price, so inference
    may propose a scale but must never arrive at one already applied."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    fields: list[dict[str, Any]] = list(_layout_row(run["sourceFileId"])["fields"])

    assert all(field["impliedScale"] is None for field in fields)
    proposed = [field for field in fields if field["proposedScale"] is not None]
    assert proposed, "the packed-numeric fields should still offer a default"
    assert all(field["scaleSource"] == "proposed" for field in proposed)


def test_a_csv_upload_gets_a_layout_naming_its_header_columns(customer: str) -> None:
    run = upload_and_settle(SAMPLE_CSV, customer_id=customer)
    assert run["status"] == "profiled", run

    row = _layout_row(run["sourceFileId"])
    assert row["version"] == 1
    assert row["kind"] == "csv"
    assert row["inferred"] is True
    assert row["edited_by_human"] is False
    assert row["delimiter"] == ",", "the sniffed delimiter"
    assert row["quote_char"] == '"', "the sniffed quote character"
    assert row["has_header"] is True
    assert row["header_row_index"] == 0
    assert row["record_width"] is None, "CSV records have no fixed width"
    assert row["inference_evidence"] is None, "there is no histogram for a CSV"

    fields: list[dict[str, Any]] = list(row["fields"])
    assert len(fields) == 21
    assert fields[0]["name"] == "IVHDR01"
    assert fields[4]["name"] == "BILTNO"
    assert all(field["start"] is None and field["end"] is None for field in fields), (
        "a CSV column has an ordinal, not a character range"
    )


def test_parsing_the_same_file_twice_leaves_one_version_one(customer: str) -> None:
    """`do nothing` on conflict: version 1 is what inference said the first time, and a
    redelivered parse job must not quietly rewrite it."""
    from src import config
    from src.jobs.tasks import parse_source_file

    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    before = _layout_row(file_id)

    import os

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgresql+psycopg://speedy:speedy@127.0.0.1:5432/speedy_onboard"
    config.load.cache_clear()
    try:
        parse_source_file(ingest_run_id=run["id"])
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        config.load.cache_clear()

    after = _layout_row(file_id)  # asserts exactly one version internally
    assert after["fields"] == before["fields"]


# --------------------------------------------------------------------------------------
# GET /v1/source-files/{id}/layout
# --------------------------------------------------------------------------------------


def test_the_layout_route_returns_the_current_version_with_its_evidence(
    customer: str,
) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    response = get_layout(run["sourceFileId"])

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sourceFileId"] == run["sourceFileId"]
    assert data["version"] == 1
    assert data["kind"] == "fixed_width"
    assert data["recordWidth"] == 186
    assert data["editedByHuman"] is False

    evidence = data["inferenceEvidence"]
    assert evidence is not None
    assert evidence["threshold"] == f"{BLANK_THRESHOLD:f}", "the threshold used, shown"
    assert Decimal(evidence["threshold"]) == BLANK_THRESHOLD
    assert evidence["recordsSampled"] == 500, "the trailer is not a sampled record"
    assert len(evidence["blankFractions"]) == 186, "one entry per character position"
    assert len(data["sampleRecords"]) == 3, "the ruler needs real records"
    assert all(len(record) == 186 for record in data["sampleRecords"])
    assert all(Decimal(value) <= 1 for value in evidence["blankFractions"])
    assert len(evidence["ambiguousRegions"]) == 3
    assert any("lower bound" in note for note in evidence["notes"])


def test_the_layout_route_is_camel_case_throughout(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    data = get_layout(run["sourceFileId"]).json()["data"]

    for key in ("sourceFileId", "quoteChar", "hasHeader", "footerRowCount", "recordWidth"):
        assert key in data, key
    for key in ("source_file_id", "quote_char", "has_header", "record_width"):
        assert key not in data, f"{key} leaked snake_case"
    field = data["fields"][0]
    for key in ("impliedScale", "proposedScale", "quirkEvidence"):
        assert key in field, key


def test_an_unknown_file_id_is_a_404_with_no_internal_names(customer: str) -> None:
    del customer
    response = get_layout(str(uuid.uuid4()))

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    body = response.text.lower()
    for leak in ("file_layouts", "select", "traceback", "sqlalchemy", "/app", "psycopg"):
        assert leak not in body, f"{leak} leaked into a 404 body"


def test_a_malformed_file_id_is_refused_without_a_stack_trace(customer: str) -> None:
    del customer
    response = get_layout("not-a-uuid")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_request"
    assert "traceback" not in response.text.lower()


def test_asking_for_a_version_that_does_not_exist_is_a_404(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    response = get_layout(run["sourceFileId"], version=7)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_version_zero_is_refused_by_the_route_not_the_database(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    response = get_layout(run["sourceFileId"], version=0)

    assert response.status_code == 400, response.text


def test_the_layout_route_sets_the_same_security_headers_as_every_other(
    customer: str,
) -> None:
    """A new router is the easiest place to lose the middleware, so assert it here too.

    Only the four headers the app actually guarantees (see `test_security_headers.py`).
    `Cache-Control: no-store` would be a reasonable fifth for responses carrying one
    customer's data, but it is not in the app's contract today and adding it is a
    cross-cutting change, not a layout change - noted in `docs/progress.md` instead.
    """
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    response = httpx.get(f"{API_BASE}/v1/source-files/{run['sourceFileId']}/layout", timeout=10)

    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("content-security-policy") is not None


# --------------------------------------------------------------------------------------
# 4.4 - PATCH /v1/source-files/{id}/layout
# --------------------------------------------------------------------------------------


def _field_list(file_id: str) -> list[dict[str, Any]]:
    data = get_layout(file_id).json()["data"]
    return [
        {"name": f["name"], "start": f["start"], "end": f["end"], "quirks": f["quirks"]}
        for f in data["fields"]
    ]


def _patch(file_id: str, fields: list[dict[str, Any]], **extra: Any) -> httpx.Response:
    return httpx.patch(
        f"{API_BASE}/v1/source-files/{file_id}/layout",
        json={"fields": fields, **extra},
        timeout=30,
    )


def _version_count(file_id: str) -> int:
    with db(ADMIN_DB_URL) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from file_layouts where source_file_id = %s", (file_id,))
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _wait_for_profile_version(file_id: str, version: int, budget_s: float = 45) -> dict[str, Any]:
    deadline = time.monotonic() + budget_s
    data: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = httpx.get(f"{API_BASE}/v1/source-files/{file_id}/profile", timeout=10)
        if response.status_code == 200:
            data = response.json()["data"]
            if data["layoutVersion"] == version:
                return data
        time.sleep(0.4)
    raise AssertionError(f"profile never reached layout version {version}: {data}")


def test_shifting_a_boundary_writes_version_two_and_leaves_version_one_alone(
    customer: str,
) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)
    assert (fields[2]["start"], fields[2]["end"]) == (38, 49)

    # Move two characters from field 3 to field 4.
    fields[2]["end"] = 47
    fields[3]["start"] = 48
    response = _patch(file_id, fields)

    assert response.status_code == 202, response.text
    accepted = response.json()["data"]
    assert accepted["version"] == 2
    assert accepted["profileUrl"].endswith("layoutVersion=2")
    assert _version_count(file_id) == 2

    version_one = get_layout(file_id, version=1).json()["data"]
    assert (version_one["fields"][2]["start"], version_one["fields"][2]["end"]) == (38, 49)
    assert version_one["editedByHuman"] is False
    assert version_one["inferred"] is True

    version_two = get_layout(file_id, version=2).json()["data"]
    assert (version_two["fields"][2]["start"], version_two["fields"][2]["end"]) == (38, 47)
    assert version_two["editedByHuman"] is True
    assert version_two["inferred"] is False
    assert version_two["sampleRecords"] == version_one["sampleRecords"], (
        "the records are the file's own content and must survive an edit - dropping them "
        "with the evidence left the editor with no ruler and no preview"
    )
    assert len(version_two["sampleRecords"]) == 3
    assert version_two["inferenceEvidence"] is None, (
        "a human-authored layout has no histogram; copying one forward would credit "
        "their boundaries to a measurement that did not produce them"
    )


def test_splitting_a_merged_field_recomputes_the_profile_under_the_new_version(
    customer: str,
) -> None:
    """The feature's whole point: inference cannot see the boundaries inside 19-37, a
    human with the record layout can, and the profile must then describe what they said."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]

    fields = _field_list(file_id)
    before = _wait_for_profile_version(file_id, 1)
    merged = next(column for column in before["columns"] if column["columnIndex"] == 1)
    assert merged["typeGuess"] != "date", "the merged slice is not a date"

    split: list[dict[str, Any]] = []
    for field in fields:
        if (field["start"], field["end"]) == (19, 37):
            split += [
                {"name": "IVPSTDT", "start": 19, "end": 25, "quirks": ["cyymmdd"]},
                {"name": "FLD017", "start": 26, "end": 29, "quirks": []},
                {"name": "BILTNO", "start": 30, "end": 37, "quirks": ["right_padded"]},
            ]
        else:
            split.append(field)

    assert _patch(file_id, split).status_code == 202
    after = _wait_for_profile_version(file_id, 2)

    assert len(after["columns"]) == len(before["columns"]) + 2
    date_column = next(column for column in after["columns"] if column["columnName"] == "IVPSTDT")
    assert date_column["typeGuess"] == "date"
    assert date_column["dateFormat"] == "CYYMMDD"
    account = next(column for column in after["columns"] if column["columnName"] == "BILTNO")
    assert account["distinctCount"] == 25, "the bill-to account, now readable on its own"

    # Version 1's profile is still there to compare against.
    still = httpx.get(
        f"{API_BASE}/v1/source-files/{file_id}/profile",
        params={"layoutVersion": 1},
        timeout=10,
    ).json()["data"]
    assert still["layoutVersion"] == 1
    assert len(still["columns"]) == len(before["columns"])


def test_a_human_set_scale_is_recorded_as_confirmed_not_proposed(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)
    fields[3]["impliedScale"] = 2

    assert _patch(file_id, fields).status_code == 202
    field = get_layout(file_id, version=2).json()["data"]["fields"][3]
    assert field["impliedScale"] == 2
    assert field["scaleSource"] == "confirmed", "the human is the authority detection waits for"
    assert field["proposedScale"] is None


# --- denials -------------------------------------------------------------------------


def test_overlapping_boundaries_are_refused_and_no_version_is_written(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)
    fields[2]["end"] = 55  # now overlaps field 4, which starts at 50

    response = _patch(file_id, fields)

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_layout"
    assert set(error["fields"]) == {"2", "3"}, error["fields"]
    assert "characters 50-55" in error["fields"]["2"]
    assert _version_count(file_id) == 1, "a refused layout must not leave a version row"


def test_a_field_past_the_record_width_is_refused_naming_the_width(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)
    fields[-1]["end"] = 400

    response = _patch(file_id, fields)

    assert response.status_code == 422
    message = response.json()["error"]["fields"][str(len(fields) - 1)]
    assert "186" in message, "the record width belongs in the message"
    assert _version_count(file_id) == 1


def test_five_hundred_fields_are_refused_and_no_version_is_written(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields: list[dict[str, Any]] = [
        {"name": f"F{index}", "start": index + 1, "end": index + 1, "quirks": []}
        for index in range(500)
    ]

    response = _patch(file_id, fields)

    # Pydantic's max_length on the list rejects this as a malformed body before the
    # layout rules run; either refusal is correct, so assert the outcome that matters.
    assert response.status_code in (400, 422), response.text
    assert _version_count(file_id) == 1


def test_an_implied_scale_of_99_is_refused_with_the_allowed_range(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)
    fields[3]["impliedScale"] = 99

    response = _patch(file_id, fields)

    assert response.status_code == 422, response.text
    assert "0-6" in response.json()["error"]["fields"]["3"]
    assert _version_count(file_id) == 1


def test_duplicate_field_names_are_refused(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)
    fields[1]["name"] = fields[0]["name"]

    response = _patch(file_id, fields)

    assert response.status_code == 422
    assert set(response.json()["error"]["fields"]) == {"0", "1"}


def test_a_name_that_is_not_an_identifier_is_refused(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)
    fields[0]["name"] = "drop table;--"

    response = _patch(file_id, fields)

    assert response.status_code in (400, 422), response.text
    assert _version_count(file_id) == 1


def test_patching_a_csv_layout_is_refused(customer: str) -> None:
    """A CSV's columns come from its header, so there are no boundaries to move."""
    run = upload_and_settle(SAMPLE_CSV, customer_id=customer)
    file_id = run["sourceFileId"]

    response = _patch(file_id, [{"name": "A", "start": 1, "end": 5, "quirks": []}])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_layout"
    assert _version_count(file_id) == 1


def test_patching_a_materialised_run_is_locked(customer: str) -> None:
    """Changing boundaries under canonical lines would leave them describing a layout
    that no longer exists, silently, because nothing recomputes them."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)

    # Stand in for P7 having materialised: the stage timestamp is what the check reads.
    with db(ADMIN_DB_URL) as conn:
        conn.execute("update ingest_runs set materialized_at = now() where id = %s", (run["id"],))

    response = _patch(file_id, fields)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "layout_locked"
    assert "new run" in error["message"], "tell the user what to do instead"
    assert _version_count(file_id) == 1


def test_patching_an_unknown_file_is_a_404(customer: str) -> None:
    del customer
    response = _patch(str(uuid.uuid4()), [{"name": "A", "start": 1, "end": 5, "quirks": []}])
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_an_unknown_body_key_is_rejected_rather_than_ignored(customer: str) -> None:
    """`extra: forbid`: a client sending `feilds` should be told, not silently ignored."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]

    response = _patch(file_id, _field_list(file_id), surpriseKey="x")

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "malformed_request"


def test_a_failing_reprofile_leaves_version_two_and_version_ones_profile_readable(
    customer: str,
) -> None:
    """4.4 (failure): the edit is saved, the recompute fails, and the user keeps both
    the new layout and the statistics they had before."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _field_list(file_id)
    before = _wait_for_profile_version(file_id, 1)

    # Make the recompute fail the way it really can: the stored file is gone.
    with db(ADMIN_DB_URL) as conn, conn.cursor() as cur:
        cur.execute("select stored_name from source_files where id = %s", (file_id,))
        row = cur.fetchone()
    assert row is not None
    stored_name = row[0]
    container_path = f"{CONTAINER_UPLOAD_DIR}/{stored_name}"
    moved = f"{container_path}.moved"
    # Through `api`, not `worker`: docker-compose.yml mounts the uploads volume into the
    # worker read-only ("the worker consumes uploads, it never writes them"), so `mv`
    # there fails with a permission error. Both containers share the volume, so moving
    # it from `api` hides it from the worker just the same.
    subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "mv", container_path, moved],
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    try:
        fields[2]["end"] = 47
        fields[3]["start"] = 48
        assert _patch(file_id, fields).status_code == 202

        deadline = time.monotonic() + 45
        final: dict[str, Any] = {}
        while time.monotonic() < deadline:
            final = httpx.get(f"{API_BASE}/v1/ingest-runs/{run['id']}", timeout=10).json()["data"]
            if final["status"] == "failed":
                break
            time.sleep(0.4)
        assert final["status"] == "failed", final
        assert final["errorCode"] == "source_file_missing"
        assert "/" not in final["errorMessage"], "no filesystem path in a user message"
    finally:
        subprocess.run(
            ["docker", "compose", "exec", "-T", "api", "mv", moved, container_path],
            check=False,
            capture_output=True,
            cwd=REPO_ROOT,
        )

    # Version 2 survives the failed recompute, and version 1's profile still reads.
    assert _version_count(file_id) == 2
    assert get_layout(file_id, version=2).json()["data"]["editedByHuman"] is True
    still = httpx.get(
        f"{API_BASE}/v1/source-files/{file_id}/profile",
        params={"layoutVersion": 1},
        timeout=10,
    ).json()["data"]
    assert still["layoutVersion"] == 1
    assert len(still["columns"]) == len(before["columns"])
