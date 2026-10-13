"""mvp.md 5.1 - the canonical schema, the format doc, and the route that serves them."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from src.canonical_export import ARTIFACT, serialise
from src.features.canonical import service
from src.features.canonical.model import (
    CANONICAL_FIELD_NAMES,
    CANONICAL_SCHEMA_VERSION,
    MONEY_PATTERN,
    REQUIRED_FIELD_NAMES,
    UOM_VALUES,
    CanonicalInvoiceLine,
    schema_document,
)
from tests import live

FORMAT_DOC = Path(__file__).resolve().parents[1] / "canonical" / "FORMAT.md"

#: The 16 fields the spec's "Canonical invoice line" block names, in its order.
SPEC_FIELDS = (
    "invoice_no",
    "invoice_date",
    "customer_id",
    "customer_name",
    "dist_item_id",
    "mfr_item_code",
    "brand_manufacturer",
    "invoice_vendor",
    "qty",
    "uom",
    "pack_size",
    "catch_weight_lbs",
    "unit_price",
    "ext_amount",
    "is_deviated",
    "deviated_price",
)


def _committed() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    return parsed


# --------------------------------------------------------------------------------------
# The schema document itself
# --------------------------------------------------------------------------------------


def test_the_committed_schema_declares_all_sixteen_spec_fields_in_order() -> None:
    document = _committed()
    assert tuple(document["x-csvColumnOrder"]) == SPEC_FIELDS
    assert set(document["properties"]) == set(SPEC_FIELDS)


def test_the_committed_schema_is_a_well_formed_json_schema() -> None:
    """Checked structurally rather than against the meta-schema: validating a document
    against the JSON Schema meta-schema needs a new dependency, and BACKEND.md §9 asks
    for a reason before adding one. The properties worth asserting are the ones a
    consumer actually relies on."""
    document = _committed()
    assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert document["$id"].endswith(CANONICAL_SCHEMA_VERSION)
    assert document["type"] == "object"
    assert document["additionalProperties"] is False, "a canonical line has no extra fields"
    assert isinstance(document["properties"], dict)
    assert isinstance(document["required"], list)
    properties = cast("dict[str, dict[str, Any]]", document["properties"])
    for name, spec in properties.items():
        assert "type" in spec or "anyOf" in spec, f"{name} declares no type"
        assert spec.get("description"), f"{name} has no description"


def test_uom_is_an_enum_of_exactly_cs_ea_and_lb() -> None:
    document = _committed()
    assert document["properties"]["uom"]["enum"] == list(UOM_VALUES)
    assert document["properties"]["uom"]["enum"] == ["CS", "EA", "LB"]


@pytest.mark.parametrize(
    "field",
    ["qty", "unit_price", "ext_amount", "catch_weight_lbs", "deviated_price"],
)
def test_every_money_or_quantity_field_is_a_patterned_decimal_string(field: str) -> None:
    """Never `"type": "number"`. A JSON number is a float in every mainstream parser,
    and a rebate computed from a float is wrong in a way nobody notices until a customer
    reconciles it."""
    spec: dict[str, Any] = _committed()["properties"][field]
    # An optional field is `anyOf: [{the type}, {"type": "null"}]`.
    branches = spec.get("anyOf", [spec])
    value_branch = next(b for b in branches if b.get("type") != "null")
    assert value_branch["type"] == "string", field
    assert "pattern" in value_branch, field
    assert "number" not in json.dumps(spec), field


def test_the_money_pattern_accepts_real_amounts_and_rejects_junk() -> None:
    pattern = re.compile(MONEY_PATTERN)
    for good in ("0", "0.00", "165.69", "-12.50", "1234567890123.99"):
        assert pattern.fullmatch(good), good
    for bad in ("", "1.234", "1,234.50", "$12.50", "1e5", "12.50; drop table", " 12.50", "abc"):
        assert not pattern.fullmatch(bad), bad


def test_pack_size_is_an_integer_and_optional() -> None:
    """Eaches per case is a count, not a decimal - and it is genuinely absent on files
    where the distributor does not supply it, which is a seeded readiness issue."""
    spec: dict[str, Any] = _committed()["properties"]["pack_size"]
    branches = spec.get("anyOf", [spec])
    assert any(b.get("type") == "integer" for b in branches)
    assert any(b.get("type") == "null" for b in branches)
    assert "pack_size" not in _committed()["required"]


# --------------------------------------------------------------------------------------
# Model and schema cannot drift
# --------------------------------------------------------------------------------------


def test_the_committed_schema_is_current_against_the_model() -> None:
    """The same guard `make check` runs. Here as a test too, so a developer who has not
    run the gate still gets told."""
    assert ARTIFACT.read_text(encoding="utf-8") == serialise(schema_document()), (
        "canonical/invoice_line.schema.json is stale - run `make canonical`"
    )


def test_model_field_names_requiredness_and_enums_match_the_schema_exactly() -> None:
    document = _committed()
    assert tuple(CanonicalInvoiceLine.model_fields) == SPEC_FIELDS
    assert tuple(CANONICAL_FIELD_NAMES) == SPEC_FIELDS
    assert set(document["required"]) == set(REQUIRED_FIELD_NAMES)
    assert "uom" in REQUIRED_FIELD_NAMES
    for optional in ("pack_size", "catch_weight_lbs", "deviated_price"):
        assert optional not in REQUIRED_FIELD_NAMES, optional


def test_the_model_refuses_an_unknown_field() -> None:
    with pytest.raises(ValueError, match="surprise"):
        CanonicalInvoiceLine.model_validate(
            {
                "invoice_no": "HP1",
                "invoice_date": "2026-01-01",
                "customer_id": "200013",
                "customer_name": "Cedar Grove",
                "dist_item_id": "100700",
                "mfr_item_code": "CPC-1",
                "brand_manufacturer": "Crosspoint",
                "invoice_vendor": "Crosspoint",
                "qty": "9",
                "uom": "CS",
                "unit_price": "18.41",
                "ext_amount": "165.69",
                "is_deviated": False,
                "surprise": 1,
            }
        )


def test_the_model_refuses_a_uom_outside_the_closed_set() -> None:
    payload = {
        "invoice_no": "HP1",
        "invoice_date": "2026-01-01",
        "customer_id": "200013",
        "customer_name": "Cedar Grove",
        "dist_item_id": "100700",
        "mfr_item_code": "CPC-1",
        "brand_manufacturer": "Crosspoint",
        "invoice_vendor": "Crosspoint",
        "qty": "9",
        "uom": "DRUM",
        "unit_price": "18.41",
        "ext_amount": "165.69",
        "is_deviated": False,
    }
    with pytest.raises(ValueError, match="uom"):
        CanonicalInvoiceLine.model_validate(payload)


def test_a_money_value_stays_a_decimal_and_never_becomes_a_float() -> None:
    from decimal import Decimal

    line = CanonicalInvoiceLine.model_validate(
        {
            "invoice_no": "HP1",
            "invoice_date": "2026-01-01",
            "customer_id": "200013",
            "customer_name": "Cedar Grove",
            "dist_item_id": "100700",
            "mfr_item_code": "CPC-1",
            "brand_manufacturer": "Crosspoint",
            "invoice_vendor": "Crosspoint",
            "qty": "9",
            "uom": "CS",
            "unit_price": "18.41",
            "ext_amount": "165.69",
            "is_deviated": False,
        }
    )
    assert isinstance(line.ext_amount, Decimal)
    assert not isinstance(line.ext_amount, float)
    assert line.ext_amount == Decimal("165.69")


# --------------------------------------------------------------------------------------
# FORMAT.md
# --------------------------------------------------------------------------------------


def test_the_format_doc_states_everything_a_consumer_needs() -> None:
    text = FORMAT_DOC.read_text(encoding="utf-8")
    assert CANONICAL_SCHEMA_VERSION in text, "the version it describes"
    for needed in (
        "UTF-8",
        "(LF)",
        "Header row",
        "Field separator",
        "Quote character",
        "Null representation",
        "RFC 4180",
    ):
        assert needed in text, needed


def test_the_format_doc_lists_the_columns_in_the_committed_order() -> None:
    text = FORMAT_DOC.read_text(encoding="utf-8")
    positions = [text.index(f"`{name}`") for name in SPEC_FIELDS]
    assert positions == sorted(positions), (
        "FORMAT.md's column table is out of order against x-csvColumnOrder"
    )


def test_the_format_docs_example_header_matches_the_committed_column_order() -> None:
    """The example is the thing someone copies, so it is the thing most worth pinning."""
    text = FORMAT_DOC.read_text(encoding="utf-8")
    header = ",".join(SPEC_FIELDS)
    assert header in text, "the example CSV's header row has drifted from the schema"


def test_the_format_doc_says_it_stands_in_for_speedys_unpublished_format() -> None:
    """A reader must not mistake this for Speedy's real intake contract."""
    text = FORMAT_DOC.read_text(encoding="utf-8").lower()
    assert "not public" in text or "unpublished" in text
    assert "integration point" in text


# --------------------------------------------------------------------------------------
# The service reads the committed file
# --------------------------------------------------------------------------------------


def test_the_service_returns_the_version_and_fields_in_csv_order() -> None:
    data = service.canonical_schema()
    assert data["version"] == CANONICAL_SCHEMA_VERSION
    assert tuple(field["name"] for field in data["fields"]) == SPEC_FIELDS
    uom = next(field for field in data["fields"] if field["name"] == "uom")
    assert uom["enum"] == ["CS", "EA", "LB"]
    assert uom["required"] is True
    pack = next(field for field in data["fields"] if field["name"] == "pack_size")
    assert pack["type"] == "integer"
    assert pack["required"] is False


def test_editing_the_schema_file_changes_what_the_service_returns(tmp_path: Path) -> None:
    """5.1's own criterion, and the point of serving from the file: the committed
    artifact is the contract, not a decorative copy of the model."""
    document = _committed()
    document["x-canonicalSchemaVersion"] = "9.9.9"
    document["x-csvColumnOrder"] = ["invoice_no", "uom"]
    fixture = tmp_path / "invoice_line.schema.json"
    fixture.write_text(json.dumps(document), encoding="utf-8")

    service.clear_cache()
    try:
        data = service.canonical_schema(fixture)
    finally:
        service.clear_cache()

    assert data["version"] == "9.9.9"
    assert [field["name"] for field in data["fields"]] == ["invoice_no", "uom"]
    # And the real file is untouched by the experiment.
    assert service.canonical_schema()["version"] == CANONICAL_SCHEMA_VERSION


# --------------------------------------------------------------------------------------
# GET /v1/canonical-schema, against the running stack
# --------------------------------------------------------------------------------------

live_api = live.live_api


def test_the_route_serves_the_committed_version_and_fields(live_api: None) -> None:
    del live_api
    response = httpx.get(f"{live.API_BASE}/v1/canonical-schema", timeout=10)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["version"] == CANONICAL_SCHEMA_VERSION
    assert [field["name"] for field in data["fields"]] == list(SPEC_FIELDS)

    uom = next(field for field in data["fields"] if field["name"] == "uom")
    assert uom["enum"] == ["CS", "EA", "LB"]
    ext = next(field for field in data["fields"] if field["name"] == "ext_amount")
    assert ext["type"] == "string" and ext["pattern"]
    assert all(field["description"] for field in data["fields"])


def test_the_route_needs_no_sign_in_and_sets_the_security_headers(live_api: None) -> None:
    """A published contract is the one thing in this API that is public on purpose: a
    customer's IT team needs it before they have anything else."""
    del live_api
    response = httpx.get(f"{live.API_BASE}/v1/canonical-schema", timeout=10)

    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("content-security-policy") is not None
    body = response.text.lower()
    for leak in ("select", "traceback", "/app", "psycopg", "pydantic"):
        assert leak not in body, leak
