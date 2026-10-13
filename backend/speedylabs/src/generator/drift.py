"""The month-2 export, with drift seeded on purpose.

Five changes, chosen so each exercises a different detector in P10, and each recorded
exactly in `expected_drift.json` so "every seeded format change is caught before ingest"
is a comparison rather than a judgement:

**Structural** — these break a saved mapping spec outright:
1. A **renamed column**: `SHPUNT` becomes `SHPQTY`. The data is unchanged, so only the
   name moved — the cheapest drift to miss and the one that silently maps nothing.
2. A **shifted field**: `BILTNM` widens from 30 to 32, which moves the start offset of
   every field after it. Catching the rename but not the shift would still corrupt
   eleven columns.
3. A **type change**: `CSEMUL` stops being a zero-padded integer and starts carrying
   text like `12/CS`. A parser that coerces will produce nulls; one that does not will
   produce strings where a number is expected.

**Distributional** — these pass every structural check and still make the numbers wrong:
4. The **UOM mix** shifts toward `EA`, which is what happens when a customer starts
   ordering broken cases and is exactly when `pack_size` starts mattering.
5. The **average catch weight** jumps, which moves every pound-priced rebate dollar
   without changing a single column.

Months 1 and 2 share the same catalog and seed, so any difference a detector reports is
one of these five or a bug.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from random import Random
from typing import Any, Final

from src.generator.catalog import Catalog, build_catalog, manufacturer_by_code
from src.generator.fixtures import ORACLE_DIR, write_json
from src.generator.invoices import InvoiceLine, duplicate_key
from src.generator.seed import SEED
from src.generator.source_layout import LAYOUT, SourceField
from src.lib.money import extend, quantize_amount, quantize_unit_price

#: Month 2 of the onboarding, which is the month after the six-month history file.
MONTH2 = date(2026, 7, 1)

RENAMED_FROM: Final = "SHPUNT"
RENAMED_TO: Final = "SHPQTY"
WIDENED_FIELD: Final = "BILTNM"
WIDENED_FROM: Final = 30
WIDENED_TO: Final = 32
RETYPED_FIELD: Final = "CSEMUL"

#: Month 1 runs about 18% EA (from the seeded mixed-UOM defect); month 2 runs far higher.
MONTH2_EACH_RATE: Final = 45
#: Catch weights run this much heavier - a real seasonal or spec change.
CATCH_WEIGHT_MULTIPLIER: Final = Decimal("1.35")


@dataclass(frozen=True)
class Month2File:
    lines: tuple[InvoiceLine, ...]
    layout: tuple[SourceField, ...]
    footer_total: Decimal


def drifted_layout() -> tuple[SourceField, ...]:
    """Month 1's layout with the three structural changes applied."""
    out: list[SourceField] = []
    for field in LAYOUT:
        if field.name == RENAMED_FROM:
            out.append(replace(field, name=RENAMED_TO))
        elif field.name == WIDENED_FIELD:
            out.append(replace(field, width=WIDENED_TO))
        elif field.name == RETYPED_FIELD:
            out.append(replace(field, kind="packtext", note="Case multiplier, now text like 12/CS"))
        else:
            out.append(field)
    return tuple(out)


def _month2_dates() -> list[date]:
    last = monthrange(MONTH2.year, MONTH2.month)[1]
    return [
        d
        for d in (date(MONTH2.year, MONTH2.month, day) for day in range(1, last + 1))
        if d.weekday() < 5
    ]


def build_month2(seed: int = SEED, catalog: Catalog | None = None) -> Month2File:
    """One month of lines carrying the distributional drift."""
    rng = Random(seed + 7)
    cat = catalog if catalog is not None else build_catalog(seed)
    lines: list[InvoiceLine] = []
    seen: set[tuple[str, str, str, Decimal, Decimal]] = set()
    row_number = 1
    invoice_seq = 900_000

    for day in _month2_dates():
        for _ in range(rng.randrange(48, 62)):
            invoice_seq += 1
            invoice_no = f"HP{day.year % 100:02d}{invoice_seq:06d}"
            customer = cat.customers[rng.randrange(len(cat.customers))]
            for _ in range(rng.randrange(5, 10)):
                sku = cat.skus[rng.randrange(len(cat.skus))]
                mfr = manufacturer_by_code(sku.brand_code)
                redistributed = mfr is not None and mfr.redistributor_only
                vendor = (
                    cat.redistributors[rng.randrange(len(cat.redistributors))].number
                    if redistributed or rng.randrange(100) < 22
                    else (mfr.vendor_number if mfr is not None else sku.brand_code)
                )

                if sku.catch_weight:
                    qty = Decimal(rng.randrange(1, 9))
                    assert sku.weight_min_lbs is not None and sku.weight_max_lbs is not None
                    span = int((sku.weight_max_lbs - sku.weight_min_lbs) * 100)
                    per_case = sku.weight_min_lbs + Decimal(rng.randrange(0, max(span, 1))).scaleb(
                        -2
                    )
                    # Drift 5: every catch weight runs heavier.
                    weight = quantize_amount(per_case * qty * CATCH_WEIGHT_MULTIPLIER)
                    price = quantize_unit_price(sku.list_price)
                    line = InvoiceLine(
                        row_number=row_number,
                        invoice_no=invoice_no,
                        invoice_date=day,
                        customer_id=customer.number,
                        customer_name=customer.name,
                        dist_item_id=sku.dist_item_id,
                        mfr_item_code=sku.mfr_item_code,
                        brand_manufacturer=sku.brand_code,
                        invoice_vendor=vendor,
                        qty=qty,
                        uom="LB",
                        pack_size=None,
                        catch_weight_lbs=weight,
                        unit_price=price,
                        ext_amount=extend(weight, price),
                        is_deviated=False,
                        deviated_price=None,
                    )
                else:
                    # Drift 4: far more lines invoiced as eaches.
                    each = rng.randrange(100) < MONTH2_EACH_RATE
                    qty = Decimal(rng.randrange(1, 25))
                    price = quantize_unit_price(sku.list_price)
                    line = InvoiceLine(
                        row_number=row_number,
                        invoice_no=invoice_no,
                        invoice_date=day,
                        customer_id=customer.number,
                        customer_name=customer.name,
                        dist_item_id=sku.dist_item_id,
                        mfr_item_code=sku.mfr_item_code,
                        brand_manufacturer=sku.brand_code,
                        invoice_vendor=vendor,
                        qty=qty,
                        uom="EA" if each else "CS",
                        pack_size=sku.pack_size,
                        catch_weight_lbs=None,
                        unit_price=price,
                        ext_amount=extend(qty, price),
                        is_deviated=False,
                        deviated_price=None,
                    )

                while duplicate_key(line) in seen:
                    bumped = line.qty + 1
                    line = replace(
                        line,
                        qty=bumped,
                        ext_amount=line.ext_amount
                        if line.uom == "LB"
                        else extend(bumped, line.unit_price),
                    )
                seen.add(duplicate_key(line))
                lines.append(line)
                row_number += 1

    total = sum((line.ext_amount for line in lines), Decimal("0"))
    return Month2File(lines=tuple(lines), layout=drifted_layout(), footer_total=total)


def _uom_mix(lines: tuple[InvoiceLine, ...]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for line in lines:
        counts[line.uom] = counts.get(line.uom, 0) + 1
    total = len(lines)
    return {
        k: f"{Decimal(v * 100).scaleb(-2) / Decimal(total) * 100:.2f}"
        for k, v in sorted(counts.items())
    }


def _avg_catch_weight(lines: tuple[InvoiceLine, ...]) -> str:
    weights = [line.catch_weight_lbs for line in lines if line.catch_weight_lbs is not None]
    if not weights:
        return "0.00"
    return f"{quantize_amount(sum(weights, Decimal('0')) / Decimal(len(weights))):f}"


def build_drift_oracle(month1: tuple[InvoiceLine, ...], month2: Month2File) -> dict[str, Any]:
    """Exactly the drift that is present, and nothing else."""
    return {
        "seed": SEED,
        "month2Period": MONTH2.strftime("%Y-%m"),
        "structural": {
            "renamedColumns": [{"from": RENAMED_FROM, "to": RENAMED_TO}],
            "widthChanges": [
                {"name": WIDENED_FIELD, "fromWidth": WIDENED_FROM, "toWidth": WIDENED_TO}
            ],
            "typeChanges": [{"name": RETYPED_FIELD, "from": "int", "to": "text"}],
            "addedColumns": [],
            "removedColumns": [],
        },
        "distribution": {
            "uomMixPercent": {"month1": _uom_mix(month1), "month2": _uom_mix(month2.lines)},
            "avgCatchWeightLbs": {
                "month1": _avg_catch_weight(month1),
                "month2": _avg_catch_weight(month2.lines),
                "multiplier": f"{CATCH_WEIGHT_MULTIPLIER:f}",
            },
        },
        "month2RowCount": len(month2.lines),
    }


def write_drift_oracle(month1: tuple[InvoiceLine, ...], month2: Month2File) -> Path:
    return write_json(ORACLE_DIR / "expected_drift.json", build_drift_oracle(month1, month2))
