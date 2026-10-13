"""The demo customer's mappings, emitted as committed fixtures.

A mapping is a *customer's* answer to "which column is which", so the demo needs one the same way
it needs rebate programs: as seeded data, from the same seed as the invoices, protected by
`speedy-generate --verify`.

It lives here rather than in `tests/mappings.py` because **production must be able to read it**. The
demo reset rebuilds a fully-processed run, which means inserting a mapping draft, and production
never imports the generator *or* the test suite. A test asserts these are identical to the suite's
own `CSV_MAPPING` and `FIXED_WIDTH_MAPPING`, so there is one answer rather than two that drift.
"""

from __future__ import annotations

from typing import Any, Final

from src.generator.seed import SEED

#: Brand owner codes as `CLSCD1` carries them (`catalog.py`).
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

#: `ALWFLG` is the off-invoice allowance flag. The canonical schema wants a boolean, and a source
#: convention never reaches the canonical file.
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


#: The month-1 CSV's full ordered header, as the file carries it. **Every** column, not only the
#: mapped ones: P10 compares this list against the next file's header, so a column that appears,
#: disappears or is renamed is only detectable if the unmapped ones are remembered too. Kept here
#: beside the mapping rather than read back out of the generated file, because `--verify` has to be
#: able to catch the generator and this list disagreeing.
CSV_COLUMNS: Final = [
    "IVHDR01",
    "CUSTNO",
    "IVPSTDT",
    "FLD017",
    "BILTNO",
    "ITMNBR",
    "SHPUNT",
    "UMCODE",
    "BILTNM",
    "RSRVD1",
    "SELPRC",
    "MFGPRT",
    "LINAMT",
    "FLD023",
    "CLSCD1",
    "SHPWGT",
    "REMTNO",
    "CSEMUL",
    "ALWFLG",
    "FLD041",
    "ALWPRC",
]


def build_spec_document(canonical_schema_version: str = "1.0.0") -> dict[str, Any]:
    """The approved CSV spec, exactly as a `mapping_specs` row stores it.

    This is the *document*, not a fixture wrapper: `CsvSpecDocument` forbids unknown keys, so there
    is nowhere to put a `seed` or a `note` without the schema refusing it. The seed is recorded in
    `mapping_csv.json` next to it, which carries the same mapping.

    It holds **no per-file fact** - no source file id, no row count, no digest. That is the whole
    point of a spec: it is what the customer's format *is*, which is why the next file from that
    customer can be read without asking anyone again. A per-file fact in here would make the spec
    apply to one upload and quietly mis-describe the next.
    """
    return {
        "canonicalSchemaVersion": canonical_schema_version,
        "kind": "csv",
        "layout": {
            "encoding": "utf-8",
            "delimiter": ",",
            "quoteChar": '"',
            "hasHeader": True,
            "headerRowIndex": 0,
            "footerRowCount": 0,
            "columns": list(CSV_COLUMNS),
        },
        "mapping": CSV_MAPPING,
    }


def build_mapping_document() -> dict[str, Any]:
    """The fixture's whole content, including the seed that produced it."""
    return {
        "seed": SEED,
        "note": (
            "The demo distributor's CSV column mapping, generated from the same seed as the "
            "invoice fixtures. Loaded by the demo seeder so a reset rebuilds a processed run."
        ),
        "csv": CSV_MAPPING,
    }
