"""Tester pass, P4 - adversarial input against `PATCH /v1/source-files/{id}/layout`.

mvp.md's Security paragraph for P4: "`fields` is user input - validated for overlap,
bounds within `record_width`, count cap (200 fields), name length and character set.
Field names are used as column identifiers downstream, never as SQL identifiers or file
paths. `impliedScale` bounded 0-6."

Live tests: they drive the real stack, like `test_layouts_routes.py`, and skip rather
than fail when Compose is not up.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from tests import live
from tests.live import SAMPLE_FWF, get_layout, upload_and_settle

live_api = live.live_api
customer = live.customer

API_BASE = live.API_BASE


def _fields(file_id: str) -> list[dict[str, Any]]:
    data = get_layout(file_id).json()["data"]
    return [
        {"name": f["name"], "start": f["start"], "end": f["end"], "quirks": f["quirks"]}
        for f in data["fields"]
    ]


def _patch(file_id: str, body: dict[str, Any]) -> httpx.Response:
    return httpx.patch(f"{API_BASE}/v1/source-files/{file_id}/layout", json=body, timeout=30)


# --------------------------------------------------------------------------------------
# Unicode and control characters in a field name
# --------------------------------------------------------------------------------------


def test_a_unicode_field_name_is_refused(customer: str) -> None:
    """The name regex is an explicit ASCII character class, not `\\w` (which matches
    unicode letters by default) - this proves it actually behaves that way end to end,
    not just in the regex source."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _fields(file_id)
    fields[0]["name"] = "café"

    response = _patch(file_id, {"fields": fields})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_layout"


def test_a_null_byte_in_a_field_name_is_refused(customer: str) -> None:
    """A field name becomes a column identifier downstream. A null byte is invisible
    in most renderings and has caused real path/identifier-truncation bugs elsewhere -
    the ASCII identifier regex must reject it rather than silently truncate at it."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _fields(file_id)
    fields[0]["name"] = "A" + "\x00" + "B"

    response = _patch(file_id, {"fields": fields})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_layout"


# --------------------------------------------------------------------------------------
# impliedScale type confusion
# --------------------------------------------------------------------------------------


def test_an_implied_scale_as_a_float_is_refused_not_truncated(customer: str) -> None:
    """2.5 must not be silently floored to 2 - that would apply a scale the client
    never asked for, to a field whose scale controls a money-at-stake decimal point."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _fields(file_id)
    fields[3]["impliedScale"] = 2.5

    response = _patch(file_id, {"fields": fields})

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "malformed_request"


def test_an_implied_scale_as_a_non_numeric_string_is_refused(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _fields(file_id)
    fields[3]["impliedScale"] = "two"

    response = _patch(file_id, {"fields": fields})

    assert response.status_code == 400, response.text


def test_an_implied_scale_given_as_the_numeric_string_two_is_refused(
    customer: str,
) -> None:
    """Pydantic v2's lax `int` validator accepts a JSON *string* that parses as an
    integer, so `"2"` and `2` were indistinguishable to this route - a client bug that
    sent a stringified number was accepted and stored as if it had sent the right type.
    BACKEND.md §9 asks for strict types, and the generated TS type for `impliedScale` is
    `number | null`, never `number | string | null`.

    Fixed by declaring the field `StrictInt`. The docstring that shipped with this test
    said it should be re-read rather than deleted "if the route is ever tightened to
    Pydantic strict mode" - that is what happened, and this is the tightened assertion.

    Worth the strictness here specifically: an implied scale is the most consequential
    number in a layout. Wrong by one is wrong by a factor of ten on every rebate dollar,
    so a client confused about its own types is better told than accommodated.
    """
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _fields(file_id)
    fields[3]["impliedScale"] = "2"

    response = _patch(file_id, {"fields": fields})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "malformed_request"
    # No second version was written, so the refusal happened before any storage.
    assert get_layout(file_id, version=2).status_code == 404


# --------------------------------------------------------------------------------------
# No cap on `quirks` list length, and no request body size limit
# --------------------------------------------------------------------------------------


def test_an_unbounded_quirks_list_is_not_rejected_for_size(customer: str) -> None:
    """BACKEND.md §4: "Cap request body size (1 MB default; uploads set their own)."
    `LayoutFieldIn.quirks: list[str]` carries no `max_length`, and no middleware caps
    the body of a non-upload route (`src/main.py` has security-header, CORS and
    request-id middleware only - grepping the module confirms no body-size guard).

    This sends one field with 150,000 duplicate (individually valid - each is
    "cyymmdd", a real `QUIRK_CODES` entry) quirk entries: over 1.5 MB for that one
    field alone, comfortably past the 1 MB default BACKEND.md names, with no other rule
    broken. The correct behaviour is a 413 or a 422 naming the per-field quirks cap;
    this test asserts that and is expected to FAIL today, which is the point - it is a
    bug report; please read the failure rather than deleting the test.

    A client that can inflate `quirks` without bound can bloat `file_layouts.fields`
    jsonb (TOAST compression softens the stored-bytes number, but every subsequent
    `GET .../layout` still serialises and transfers the whole array - BACKEND.md §6's
    "compress responses over 1 KB" is likewise unmet: see
    `test_the_layout_response_is_not_gzip_compressed_despite_being_well_over_1kb`
    below), and 200 fields times no per-field cap is a much larger number than the
    200-field count cap alone suggests the route bounds.
    """
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _fields(file_id)
    fields[0]["quirks"] = ["cyymmdd"] * 150_000
    body = {"fields": fields}
    body_bytes = len(json.dumps(body).encode())
    assert body_bytes > 1_000_000, f"test body is only {body_bytes} bytes - make it bigger"

    response = _patch(file_id, body)

    assert response.status_code in (413, 422), (
        f"a {body_bytes:,}-byte body with an unbounded per-field quirks list was "
        f"accepted with {response.status_code}: {response.text[:300]}"
    )


def test_the_layout_response_is_not_gzip_compressed_despite_being_well_over_1kb(
    customer: str,
) -> None:
    """BACKEND.md §6: "Compress responses over 1 KB." No `GZipMiddleware` (or
    equivalent) is registered in `src/main.py`. This matters more than usual for this
    route because, per the test above, nothing bounds how large a layout's `fields`
    document can get - every byte a client inflates it by is a byte every future GET
    re-transfers, uncompressed, forever.

    Fixed: `src/main.py` now registers `GZipMiddleware(minimum_size=1024)`.

    This no longer inflates `quirks` to build a large response - that list is bounded by
    the quirk vocabulary now, which is the fix for the test above. It does not need to:
    an inferred fixed-width layout is already tens of kilobytes without any help, because
    it carries a 186-entry blank histogram, three 186-character sample records and the
    per-field quirk evidence. Using the natural response is the better test anyway, since
    that is the payload real clients actually fetch."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]

    response = httpx.get(
        f"{API_BASE}/v1/source-files/{file_id}/layout",
        headers={"Accept-Encoding": "gzip"},
        timeout=10,
    )
    assert len(response.content) > 1_024, (
        "this layout is unexpectedly small; the test needs a payload over the 1KB "
        "threshold for compression to be required at all"
    )
    assert response.headers.get("content-encoding") == "gzip", (
        f"a {len(response.content):,}-byte response carried no content-encoding "
        f"header ({response.headers.get('content-encoding')!r})"
    )


# --------------------------------------------------------------------------------------
# 4.4's 409: the check and the write are not one atomic operation
# --------------------------------------------------------------------------------------


def test_a_materialise_cannot_slip_between_the_check_and_the_layout_write(
    customer: str,
) -> None:
    """The check and the write are one transaction, so a concurrent materialise waits.

    This test began life as a bug report: `service.patch_layout` called
    `repo.has_materialized_run` and then `repo.insert_next_version` as two unguarded
    statements, so a materialisation committed in between still let a new layout version
    be written - the opposite of what the 409 exists to guarantee. The fix was the one
    the report sketched: `has_materialized_run` now takes `select ... for update` on this
    file's runs, so the lock is held until the caller commits.

    The assertion therefore inverts. What is proven now is *serialisation*: a second
    connection trying to set `materialized_at` while the layout transaction is open
    cannot proceed. It is given a short `lock_timeout` so the test fails fast instead of
    blocking - without that timeout this test hangs forever, which is itself the
    clearest possible evidence that the lock is doing its job.

    `materialized_at` has no writer in `src/` yet (P7 is not built), so this is a
    repo-level proof of the mechanism `patch_layout` relies on, not an HTTP-level race.
    """
    import asyncio

    from sqlalchemy import text as sa_text

    from src.features.layouts import repo
    from src.lib import db

    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    run_id = run["id"]

    async def _race() -> tuple[bool, str]:
        db.init("postgresql+psycopg://speedy:speedy@127.0.0.1:5432/speedy_onboard")
        try:
            async with db.session() as session:
                # Takes the row lock, exactly as `patch_layout` does before validating.
                before = await repo.has_materialized_run(session, file_id)

                outcome = "the update was never attempted"
                async with db.session() as other:
                    await other.execute(sa_text("set local lock_timeout = '1500ms'"))
                    try:
                        await other.execute(
                            sa_text(
                                "update ingest_runs set materialized_at = now() where id = :id"
                            ),
                            {"id": run_id},
                        )
                        await other.commit()
                        outcome = "materialised while the layout transaction held the lock"
                    except Exception as exc:  # the exception message IS the result here
                        await other.rollback()
                        outcome = str(exc)
                return before, outcome
        finally:
            await db.dispose()

    before, outcome = asyncio.run(_race())

    assert before is False, "the run must not already be materialised"
    assert "lock timeout" in outcome.lower(), (
        "a concurrent materialise should have been made to wait behind the layout "
        f"transaction's row lock; instead: {outcome}"
    )


# --------------------------------------------------------------------------------------
# A layout that passes validation but is pathological for the re-profile job
# --------------------------------------------------------------------------------------


def test_claiming_every_quirk_on_every_field_does_not_crash_or_hang_the_reprofile(
    customer: str,
) -> None:
    """`validate_fields` only checks a claimed quirk is in the known vocabulary, not
    that it is consistent with the field's actual content - a human (or an attacker)
    can claim `cyymmdd` on a field of company names. Profiling never decodes by quirk
    (`_profile_fixed_width` / `profile_rows` only slice and classify raw strings), so
    this must re-profile cleanly rather than crashing the worker on a bad decode."""
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _fields(file_id)
    all_quirks = ["cyymmdd", "implied_decimal", "right_padded", "trailing_sign", "leading_zeros"]
    for field in fields:
        field["quirks"] = list(all_quirks)
        field["impliedScale"] = 6

    response = _patch(file_id, {"fields": fields})
    assert response.status_code == 202, response.text

    import time

    deadline = time.monotonic() + 30
    status = None
    while time.monotonic() < deadline:
        status = httpx.get(f"{API_BASE}/v1/ingest-runs/{run['id']}", timeout=10).json()["data"][
            "status"
        ]
        if status in ("profiled", "failed"):
            break
        time.sleep(0.3)
    assert status == "profiled", f"reprofile ended in {status!r} instead of completing cleanly"


# --------------------------------------------------------------------------------------
# A field name cannot reach a file path or a generated header unescaped
# --------------------------------------------------------------------------------------


def test_a_path_traversal_shaped_name_is_refused(customer: str) -> None:
    run = upload_and_settle(SAMPLE_FWF, customer_id=customer)
    file_id = run["sourceFileId"]
    fields = _fields(file_id)
    fields[0]["name"] = "../../../etc/passwd"

    response = _patch(file_id, {"fields": fields})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_layout"


def test_an_unknown_source_file_patch_does_not_leak_whether_it_exists_elsewhere(
    customer: str,
) -> None:
    del customer
    response = _patch(str(uuid.uuid4()), {"fields": [{"name": "A", "start": 1, "end": 5}]})
    assert response.status_code == 404
    body = response.text.lower()
    for leak in ("file_layouts", "traceback", "sqlalchemy", "/app", "psycopg"):
        assert leak not in body
