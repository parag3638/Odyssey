"""The distributor's own export layout — deliberately hostile to name matching.

This is the premise of the entire tool, so it is defined explicitly rather than
improvised. The spec's distributor runs "a custom in-house AS/400 ERP (non-standard
layout, cryptic column names, no native parser exists for it)". If the column names were
readable, `difflib` would be the product and the LLM would be decoration.

Three properties make that concrete, and each has a test:

1. **Cryptic names.** Six to ten characters of upper-case IBM i idiom — `NETPRC`,
   `LINAMT`, `CWTLBS` — not `unit_price` or `line_amount`.
2. **Non-canonical order.** The fields appear in the order this ERP happens to write
   them, which is not the canonical order, with reserved/filler columns interleaved
   among the real ones the way a decades-old record layout accumulates them.
3. **One actively misleading name.** `CUSTNO` is the *ship-to* account. The bill-to —
   which is what `customer_id` actually wants — is `BILTNO`. A tool that reads names
   instead of data picks `CUSTNO` and is wrong, and wrong in a way that quietly
   misattributes rebates to the wrong customer.

The fixed-width offsets below are the committed oracle that P4's boundary inference is
scored against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: The 16 canonical invoice-line fields, in the spec's order.
CANONICAL_FIELDS: Final = (
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


@dataclass(frozen=True)
class SourceField:
    """One column in the distributor's export."""

    name: str
    width: int
    #: The canonical field this column really holds, or `None` for filler/decoy columns.
    canonical: str | None
    #: What the column actually contains, in the distributor's terms. Used as the
    #: human-facing description in profiling, and as the prompt material in P6 — a human
    #: mapping `UMCODE` has nothing else to go on but this and the sample values.
    note: str
    #: How the column is represented on disk. `dec2`/`dec4` carry implied decimals with
    #: no decimal point - the AS/400 convention P4 has to detect. `cyymmdd` is the packed
    #: date. `filler_*` columns hold plausible junk of the named shape.
    kind: str = "text"
    #: True for reserved/unused columns that carry no canonical data.
    filler: bool = False


# Order is the ERP's, not the canonical one. Fillers are interleaved, not appended -
# a record layout that grew over twenty years does not keep its dead fields tidily at
# the end.
LAYOUT: Final[tuple[SourceField, ...]] = (
    SourceField("IVHDR01", 10, "invoice_no", "Invoice header number", "text"),
    SourceField("CUSTNO", 8, None, "Ship-to account - NOT the bill-to customer", "text"),
    SourceField("IVPSTDT", 7, "invoice_date", "Invoice posting date, CYYMMDD", "cyymmdd"),
    SourceField("FLD017", 4, None, "Reserved", "filler_num", filler=True),
    SourceField("BILTNO", 8, "customer_id", "Bill-to account - the customer of record", "text"),
    SourceField("ITMNBR", 12, "dist_item_id", "Distributor item number", "text"),
    SourceField("SHPUNT", 9, "qty", "Units shipped, implied 2 decimals", "dec2"),
    SourceField("UMCODE", 3, "uom", "Unit of measure code: CS, EA or LB", "text"),
    SourceField("BILTNM", 30, "customer_name", "Bill-to account name", "text"),
    SourceField("RSRVD1", 6, None, "Reserved", "filler_code", filler=True),
    SourceField("SELPRC", 11, "unit_price", "Selling price per unit, implied 4 decimals", "dec4"),
    SourceField("MFGPRT", 14, "mfr_item_code", "Manufacturer part number", "text"),
    SourceField("LINAMT", 13, "ext_amount", "Extended line amount, implied 2 decimals", "dec2"),
    SourceField("FLD023", 4, None, "Reserved", "filler_blank", filler=True),
    SourceField(
        "CLSCD1", 6, "brand_manufacturer", "Class code 1 - encodes the brand owner", "text"
    ),
    SourceField("SHPWGT", 10, "catch_weight_lbs", "Shipped weight, implied 2 decimals", "dec2"),
    SourceField("REMTNO", 8, "invoice_vendor", "Remit-to vendor number on the invoice", "text"),
    SourceField("CSEMUL", 6, "pack_size", "Case multiplier - eaches per case", "int"),
    SourceField("ALWFLG", 1, "is_deviated", "Off-invoice allowance flag: Y or N", "flag"),
    SourceField("FLD041", 5, None, "Reserved", "filler_num", filler=True),
    SourceField(
        "ALWPRC", 11, "deviated_price", "Allowance (deviated) unit price, implied 4 dec", "dec4"
    ),
)


def offsets(
    layout: tuple[SourceField, ...] | None = None,
) -> tuple[tuple[SourceField, int, int], ...]:
    """Each field with its 1-based inclusive start and end column.

    Takes an optional layout so the month-2 drift file - whose field widths differ -
    can be written and scored with the same code rather than a parallel implementation
    that could disagree about what "shifted" means.
    """
    out: list[tuple[SourceField, int, int]] = []
    cursor = 1
    for field in layout if layout is not None else LAYOUT:
        out.append((field, cursor, cursor + field.width - 1))
        cursor += field.width
    return tuple(out)


def record_width(layout: tuple[SourceField, ...] | None = None) -> int:
    return sum(f.width for f in (layout if layout is not None else LAYOUT))


def canonical_to_source() -> dict[str, str]:
    """The correct mapping. The oracle P5 and P6 are scored against."""
    return {f.canonical: f.name for f in LAYOUT if f.canonical is not None}


def to_oracle() -> list[dict[str, object]]:
    """The committed layout oracle: name, order, offsets, filler flag."""
    return [
        {
            "name": field.name,
            "order": index,
            "start": start,
            "end": end,
            "width": field.width,
            "filler": field.filler,
            "kind": field.kind,
            "canonical": field.canonical,
            "note": field.note,
        }
        for index, (field, start, end) in enumerate(offsets())
    ]
