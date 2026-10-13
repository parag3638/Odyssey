"""The customer's manufacturer reference list, emitted as a committed fixture.

Two readiness checks need knowledge no export contains: which brands this distributor only
buys through a redistributor, and which brands it knows at all. That is **customer reference
data**, and in a real deployment it arrives from the customer's own vendor master.

For the demo it has to come from the same seed as the invoices, or the checks would be scored
against a different world than the one the files describe. So the generator writes it as a
fixture - exactly the arrangement the oracle files already use, including `--verify` - and the
seeder loads the JSON. Production code reads the database and never imports `src.generator`;
a test over the import graph pins that.

**Two spellings per value, deliberately.** A canonical row carries whatever the customer's
mapping produced: the ERP's class code (`BLC`) or the display name (`Bayline Creamery`),
because `map_values` is the customer's choice and not ours. Listing both is what lets one
reference list serve two customers who mapped the same file differently.
"""

from __future__ import annotations

from typing import Any

from src.generator.catalog import Catalog, build_catalog
from src.generator.seed import SEED


def build_customer_manufacturers(catalog: Catalog | None = None) -> list[dict[str, Any]]:
    """One entry per manufacturer, sorted by code so the file is byte-stable."""
    source = catalog if catalog is not None else build_catalog(SEED)
    return [
        {
            "name": manufacturer.name,
            "brandValues": [manufacturer.code, manufacturer.name],
            "directVendorValues": [manufacturer.vendor_number, manufacturer.name],
            "redistributorOnly": manufacturer.redistributor_only,
        }
        for manufacturer in sorted(source.manufacturers, key=lambda item: item.code)
    ]


def build_reference_document(catalog: Catalog | None = None) -> dict[str, Any]:
    """The fixture's whole content, including the seed that produced it."""
    entries = build_customer_manufacturers(catalog)
    return {
        "seed": SEED,
        "note": (
            "Fictional reference data for the demo distributor, generated from the same seed "
            "as the invoice fixtures. Loaded by scripts/seed_demo.py into "
            "customer_manufacturers."
        ),
        "manufacturers": entries,
    }
