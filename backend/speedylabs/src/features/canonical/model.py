"""The canonical invoice line: one Pydantic model, and the contract it generates.

Why the model is the source of truth and the JSON Schema is an artifact
-----------------------------------------------------------------------
mvp.md 5.1 wants `GET /v1/canonical-schema` served "from the committed JSON Schema file,
so there is one source of truth, not two". It is served from the file. The file is
*generated* from this model and committed, and `make check` fails if it has drifted -
the same arrangement `openapi.json` already has, for the same reason: two hand-maintained
descriptions of one shape always diverge, and the divergence is found by a customer.

So there is one source of truth, and it is this file. The JSON Schema is how the rest of
the world reads it.

Money, quantities and prices
----------------------------
Every one of them is a `Decimal` in Python and a **decimal string** on the wire
(BACKEND.md §3). The JSON Schema says `"type": "string"` with a pattern rather than
`"number"`, because a JSON number is a float in every mainstream parser and a rebate
calculated from a float is wrong in a way nobody notices until a customer reconciles it.
`WithJsonSchema` states that explicitly instead of accepting Pydantic's default
`anyOf: [number, string]`, which would invite exactly the number form.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field, WithJsonSchema

#: A one-way door (mvp.md P5): this string is stamped on every stored mapping spec, keys
#: the LLM proposal cache, and names the output CSV's contract. Changing it is a
#: migration, not an edit.
CANONICAL_SCHEMA_VERSION: Final = "1.0.0"

#: The units of measure the canonical schema admits. `CS` case, `EA` each, `LB` pound.
#: A distributor's own codes (`CASE`, `EACH`, ...) are mapped onto these by a
#: `map_values` transform; the canonical set stays closed so every later stage can rely
#: on it.
UOM_VALUES: Final = ("CS", "EA", "LB")

#: A signed decimal string with at most two fractional digits. Anchored, so
#: `12.50; drop table` is not a match.
MONEY_PATTERN: Final = r"^-?\d{1,13}(\.\d{1,2})?$"
#: Quantities allow three decimals: a catch-weight line can ship 0.125 of a case.
QUANTITY_PATTERN: Final = r"^-?\d{1,13}(\.\d{1,3})?$"
#: Unit prices and rates allow four, which is what the source layout stores.
PRICE_PATTERN: Final = r"^-?\d{1,13}(\.\d{1,4})?$"
#: Pounds, to three decimals.
WEIGHT_PATTERN: Final = r"^-?\d{1,9}(\.\d{1,3})?$"


# `Decimal` in Python, a patterned decimal string in the schema and on the wire. Written
# out rather than built by a helper: a type alias returned from a function is a runtime
# value, and pyright will not accept one in an annotation.
Money = Annotated[
    Decimal,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": MONEY_PATTERN,
            "description": "A decimal string with at most 2 fractional digits.",
        }
    ),
]
Quantity = Annotated[
    Decimal,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": QUANTITY_PATTERN,
            "description": "A decimal string with at most 3 fractional digits.",
        }
    ),
]
Price = Annotated[
    Decimal,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": PRICE_PATTERN,
            "description": "A decimal string with at most 4 fractional digits.",
        }
    ),
]
Weight = Annotated[
    Decimal,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": WEIGHT_PATTERN,
            "description": "A decimal string in pounds, at most 3 fractional digits.",
        }
    ),
]


class CanonicalInvoiceLine(BaseModel):
    """One invoice line, in the shape every later stage reads.

    Field order is the committed CSV column order (`canonical/FORMAT.md`), so the model
    and the file cannot disagree about it either.
    """

    invoice_no: str = Field(
        max_length=64, description="The distributor's invoice number, as printed."
    )
    invoice_date: date = Field(description="Invoice posting date, ISO 8601 (YYYY-MM-DD).")
    customer_id: str = Field(
        max_length=64,
        description=(
            "The bill-to account of record. Not the ship-to: a rebate follows the "
            "customer who was billed."
        ),
    )
    customer_name: str = Field(max_length=200, description="Bill-to customer name.")
    dist_item_id: str = Field(max_length=64, description="The distributor's own item number.")
    mfr_item_code: str = Field(max_length=64, description="The manufacturer's item or part code.")
    brand_manufacturer: str = Field(
        max_length=200, description="The manufacturer whose brand the item carries."
    )
    invoice_vendor: str = Field(
        max_length=200,
        description=(
            "Who the distributor bought from on this line. Differs from "
            "brand_manufacturer when a redistributor is in the middle, which is a "
            "common cause of rebate misattribution."
        ),
    )
    qty: Quantity = Field(description="Quantity shipped, in the line's unit of measure.")
    uom: Literal["CS", "EA", "LB"] = Field(description="Unit of measure: CS, EA or LB.")
    pack_size: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Eaches per case. Required to compare a per-case rebate against an "
            "each-priced line; null when the distributor does not supply it."
        ),
    )
    catch_weight_lbs: Weight | None = Field(
        default=None,
        description=(
            "Actual pounds shipped for a variable-weight item. Null for an item that is "
            "not catch-weight; missing on a catch-weight item is a readiness failure."
        ),
    )
    unit_price: Price = Field(description="Price per unit of measure.")
    ext_amount: Money = Field(
        description="Extended line amount. Should equal qty x unit_price within tolerance."
    )
    is_deviated: bool = Field(
        description=("Whether this line was sold at a deviated (contract) price rather than list.")
    )
    deviated_price: Price | None = Field(
        default=None,
        description="The deviated price, when is_deviated is true. Null otherwise.",
    )

    model_config = {"extra": "forbid"}


#: The canonical field names, in CSV column order. Derived from the model so the two
#: cannot disagree - a field added to the model appears here without anybody editing a
#: second list.
CANONICAL_FIELD_NAMES: Final = tuple(CanonicalInvoiceLine.model_fields)

#: Fields a canonical line cannot be without. Derived the same way: a field is required
#: exactly when the model gives it no default.
REQUIRED_FIELD_NAMES: Final = tuple(
    name for name, field in CanonicalInvoiceLine.model_fields.items() if field.is_required()
)


def schema_document() -> dict[str, Any]:
    """The committed JSON Schema, built from the model."""
    document = CanonicalInvoiceLine.model_json_schema(mode="serialization")
    document["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    document["$id"] = (
        f"https://speedy-onboard.example/canonical/invoice_line/{CANONICAL_SCHEMA_VERSION}"
    )
    document["title"] = "Canonical invoice line"
    document["version"] = CANONICAL_SCHEMA_VERSION
    # Stated explicitly, because a JSON object's key order is not meaningful and this
    # file is serialised with sorted keys so unrelated edits do not show up as diffs.
    # The CSV column order is part of the contract, so it cannot live only in the
    # accident of how `properties` happens to be written out.
    document["x-csvColumnOrder"] = list(CANONICAL_FIELD_NAMES)
    document["x-canonicalSchemaVersion"] = CANONICAL_SCHEMA_VERSION
    document["description"] = (
        "One invoice line in Speedy Onboard's canonical shape. Money, quantities and "
        "prices are decimal strings, never JSON numbers. See canonical/FORMAT.md for "
        "the CSV representation."
    )
    return document
