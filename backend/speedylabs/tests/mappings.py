"""Hand-written mappings for the committed fixtures, with no LLM involved.

These are what a human with the distributor's record layout would enter. They exist as a
module because three things need the identical mapping and must not each invent one: the
speed test (5.3), the both-formats equivalence proof (5.6), and later the demo seed.

The two formats need different transforms for the same canonical field, which is the
whole reason 5.6 is worth proving:

* The **CSV** writer expanded the implied decimals, so `SHPUNT` reads `"9.00"` and needs
  no transform beyond `trim` - the validator coerces the string to a Decimal.
* The **fixed-width** file is the ERP's own output, so the same quantity is `"00000900"`
  and needs `implied_decimal(scale=2)`.

Both must land on `Decimal("9.00")`. If they do not, every number downstream depends on
which file a customer happened to send.

Two mappings here are deliberately *not* the obvious one:

* `customer_id` maps `BILTNO`, not `CUSTNO`. `CUSTNO` is the ship-to account and is the
  generator's planted decoy - a tool that reads column names picks it and misattributes
  every rebate to the wrong customer.
* `invoice_vendor` maps `REMTNO`, the remit-to vendor number, through a value map onto
  names. It is not `brand_manufacturer`: when a redistributor is in the middle the two
  genuinely differ, which is a seeded cause of variance in P13.
"""

from __future__ import annotations

from typing import Any, Final

#: Brand owner codes as `CLSCD1` carries them (`src/generator/catalog.py`).
BRAND_NAMES: Final = [
    {"from": "NVF", "to": "Northvale Foods"},
    {"from": "CPC", "to": "Cascade Provision Co"},
    {"from": "BLC", "to": "Bayline Creamery"},
]

#: Remit-to vendor numbers: three manufacturers and two redistributors.
VENDOR_NAMES: Final = [
    {"from": "300145", "to": "Northvale Foods"},
    {"from": "300292", "to": "Cascade Provision Co"},
    {"from": "300388", "to": "Bayline Creamery"},
    {"from": "400771", "to": "Midland Redistribution"},
    {"from": "400882", "to": "Keystone Supply Partners"},
]

#: `ALWFLG` is the off-invoice allowance flag. The canonical schema wants a boolean, and
#: a source convention never reaches the canonical file.
DEVIATED_FLAG: Final = [{"from": "Y", "to": "true"}, {"from": "N", "to": "false"}]

_SHARED: Final[dict[str, dict[str, Any]]] = {
    "invoice_no": {"sourceColumn": "IVHDR01", "transform": {"kind": "trim"}},
    "invoice_date": {"sourceColumn": "IVPSTDT", "transform": {"kind": "cyymmdd_to_date"}},
    "customer_id": {"sourceColumn": "BILTNO", "transform": {"kind": "trim"}},
    "customer_name": {"sourceColumn": "BILTNM", "transform": {"kind": "trim"}},
    "dist_item_id": {"sourceColumn": "ITMNBR", "transform": {"kind": "trim"}},
    "mfr_item_code": {"sourceColumn": "MFGPRT", "transform": {"kind": "trim"}},
    "brand_manufacturer": {
        "sourceColumn": "CLSCD1",
        "transform": {"kind": "map_values", "table": BRAND_NAMES},
    },
    "invoice_vendor": {
        "sourceColumn": "REMTNO",
        "transform": {"kind": "map_values", "table": VENDOR_NAMES},
    },
    "uom": {"sourceColumn": "UMCODE", "transform": {"kind": "upper"}},
    "is_deviated": {
        "sourceColumn": "ALWFLG",
        "transform": {"kind": "map_values", "table": DEVIATED_FLAG},
    },
}

#: The CSV's numbers are already written with their decimal points.
CSV_MAPPING: Final[dict[str, dict[str, Any]]] = {
    **_SHARED,
    "qty": {"sourceColumn": "SHPUNT", "transform": {"kind": "trim"}},
    "pack_size": {"sourceColumn": "CSEMUL", "transform": {"kind": "trim"}},
    "catch_weight_lbs": {"sourceColumn": "SHPWGT", "transform": {"kind": "trim"}},
    "unit_price": {"sourceColumn": "SELPRC", "transform": {"kind": "trim"}},
    "ext_amount": {"sourceColumn": "LINAMT", "transform": {"kind": "trim"}},
    "deviated_price": {"sourceColumn": "ALWPRC", "transform": {"kind": "trim"}},
}

#: The fixed-width file is the ERP's own output: packed numerics with implied decimals,
#: at the scale the record layout declares.
FIXED_WIDTH_MAPPING: Final[dict[str, dict[str, Any]]] = {
    **_SHARED,
    # `trailing_sign_decimal`, not `implied_decimal`: the file carries credit lines
    # (returns) as a negative quantity with the sign in the last character - "00000300-".
    # `implied_decimal` refuses those, which silently loses three of the sample's 500
    # lines, and in a rebate context a dropped credit is an overpaid rebate.
    "qty": {
        "sourceColumn": "SHPUNT",
        "transform": {"kind": "trailing_sign_decimal", "scale": 2},
    },
    "pack_size": {"sourceColumn": "CSEMUL", "transform": {"kind": "strip_leading_zeros"}},
    "catch_weight_lbs": {
        "sourceColumn": "SHPWGT",
        "transform": {"kind": "implied_decimal", "scale": 2},
    },
    "unit_price": {
        "sourceColumn": "SELPRC",
        "transform": {"kind": "implied_decimal", "scale": 4},
    },
    "ext_amount": {
        "sourceColumn": "LINAMT",
        "transform": {"kind": "trailing_sign_decimal", "scale": 2},
    },
    "deviated_price": {
        "sourceColumn": "ALWPRC",
        "transform": {"kind": "implied_decimal", "scale": 4},
    },
}
