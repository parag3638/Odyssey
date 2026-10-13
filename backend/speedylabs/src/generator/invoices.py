"""Six months of clean invoice lines, ~50k rows, deterministic from the seed.

"Clean" means internally consistent: every extension equals quantity times price,
every required field is populated, every catch-weight line carries a weight. The
seeded defects arrive in `defects.py`, deliberately as a second pass over this output,
so that the one-defect-per-line rule can be enforced in exactly one place.

Row numbers are assigned here and never change afterwards. They are the join key
between the data, the readiness oracle and the variance oracle — every "example row"
a reader clicks through to in the UI resolves through this number.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from random import Random
from typing import Final

from src.generator.catalog import Catalog, Sku, build_catalog, manufacturer_by_code
from src.generator.seed import SEED
from src.lib.money import extend, quantize_amount, quantize_unit_price

#: Six consecutive months. The window is fixed so every hand-calculated expectation
#: downstream can name real dates.
FIRST_MONTH: Final = date(2026, 1, 1)
MONTH_COUNT: Final = 6

#: Tuned to land within 2% of 50,000 lines across the window.
INVOICES_PER_DAY: Final = (49, 63)
LINES_PER_INVOICE: Final = (5, 10)

#: Roughly one line in twelve carries an off-invoice allowance.
DEVIATED_RATE: Final = 12

#: Roughly one case line in seventy is a credit - a return or a billing correction.
#: Credits are why the fixed-width format needs a trailing sign at all, and they are
#: what P4's sign-character quirk detection has to find. A file with no negatives would
#: make that detection untestable.
CREDIT_RATE: Final = 70

#: Canonical fields that must never be null on any line.
ALWAYS_REQUIRED: Final = (
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
    "unit_price",
    "ext_amount",
    "is_deviated",
)


@dataclass(frozen=True)
class InvoiceLine:
    """One canonical invoice line, before any defect is seeded into it."""

    row_number: int
    invoice_no: str
    invoice_date: date
    customer_id: str
    customer_name: str
    dist_item_id: str
    mfr_item_code: str
    brand_manufacturer: str
    invoice_vendor: str
    qty: Decimal
    uom: str
    pack_size: int | None
    catch_weight_lbs: Decimal | None
    unit_price: Decimal
    ext_amount: Decimal
    is_deviated: bool
    deviated_price: Decimal | None

    def missing_required(self) -> list[str]:
        """Required fields that are null on this line. Empty for clean data.

        Three fields are conditionally required, which is the shape of the real data
        rather than a convenience: a case line has no catch weight, a pound line has no
        pack size, and an undeviated line has no deviated price. Treating those as
        unconditionally required would make the clean file look defective.
        """
        missing = [f for f in ALWAYS_REQUIRED if getattr(self, f) is None]
        if self.uom in ("CS", "EA") and self.pack_size is None:
            missing.append("pack_size")
        if self.uom == "LB" and self.catch_weight_lbs is None:
            missing.append("catch_weight_lbs")
        if self.is_deviated and self.deviated_price is None:
            missing.append("deviated_price")
        return missing


def duplicate_key(line: InvoiceLine) -> tuple[str, str, str, Decimal, Decimal]:
    """What makes two lines the same line.

    Invoice number, item, unit of measure, quantity and price - the fields a human
    re-keying a line would reproduce. `invoice_date` is implied by `invoice_no`, and
    `ext_amount` is derived, so neither adds anything.

    Defined here, in the generator, because the generator has to guarantee the clean
    file contains **no** collisions: if two genuinely distinct lines happened to share
    this key, P8's duplicate check would flag a row the readiness oracle does not list
    and the one-defect-per-line property would be false through no fault of P8.
    """
    return (line.invoice_no, line.dist_item_id, line.uom, line.qty, line.unit_price)


def months() -> list[tuple[int, int]]:
    """The six (year, month) pairs in the window."""
    out: list[tuple[int, int]] = []
    year, month = FIRST_MONTH.year, FIRST_MONTH.month
    for _ in range(MONTH_COUNT):
        out.append((year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def invoice_dates() -> list[date]:
    """Every delivery date in the window: weekdays only, as a distributor ships."""
    days: list[date] = []
    for year, month in months():
        for day in range(1, monthrange(year, month)[1] + 1):
            current = date(year, month, day)
            if current.weekday() < 5:
                days.append(current)
    return days


def _vendor_for(sku: Sku, catalog: Catalog, rng: Random) -> str:
    """Who invoiced the line, as a vendor number.

    Always numeric in clean data. A redistributor-only brand always bills through a
    redistributor; everything else is mostly direct with some redistributed volume,
    which is the attribution the variance explainer has to get right later.
    """
    mfr = manufacturer_by_code(sku.brand_code)
    redistributed = mfr is not None and mfr.redistributor_only
    if redistributed or rng.randrange(100) < 22:
        return catalog.redistributors[rng.randrange(len(catalog.redistributors))].number
    assert mfr is not None
    return mfr.vendor_number


def _line(
    rng: Random,
    catalog: Catalog,
    row_number: int,
    invoice_no: str,
    invoice_date: date,
    customer_index: int,
    sku: Sku,
) -> InvoiceLine:
    customer = catalog.customers[customer_index]

    if sku.catch_weight:
        # Priced by the pound: the invoiced quantity is a case count, but the money is
        # driven by the actual weight shipped.
        qty = Decimal(rng.randrange(1, 9))
        assert sku.weight_min_lbs is not None and sku.weight_max_lbs is not None
        span = int((sku.weight_max_lbs - sku.weight_min_lbs) * 100)
        weight_per_case = sku.weight_min_lbs + Decimal(rng.randrange(0, max(span, 1))).scaleb(-2)
        catch_weight = quantize_amount(weight_per_case * qty)
        unit_price = quantize_unit_price(sku.list_price)
        ext = extend(catch_weight, unit_price)
        pack_size = None
    else:
        qty = Decimal(rng.randrange(1, 25))
        if rng.randrange(CREDIT_RATE) == 0:
            qty = -qty
        catch_weight = None
        unit_price = quantize_unit_price(sku.list_price)
        ext = extend(qty, unit_price)
        pack_size = sku.pack_size

    deviated = rng.randrange(DEVIATED_RATE) == 0
    deviated_price = quantize_unit_price(unit_price * Decimal("0.92")) if deviated else None

    return InvoiceLine(
        row_number=row_number,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        customer_id=customer.number,
        customer_name=customer.name,
        dist_item_id=sku.dist_item_id,
        mfr_item_code=sku.mfr_item_code,
        brand_manufacturer=sku.brand_code,
        invoice_vendor=_vendor_for(sku, catalog, rng),
        qty=qty,
        uom=sku.uom,
        pack_size=pack_size,
        catch_weight_lbs=catch_weight,
        unit_price=unit_price,
        ext_amount=ext,
        is_deviated=deviated,
        deviated_price=deviated_price,
    )


def build_lines(seed: int = SEED, catalog: Catalog | None = None) -> tuple[InvoiceLine, ...]:
    """Six months of clean invoice lines, in a stable order."""
    rng = Random(seed + 1)  # offset so lines do not reuse the catalog's draw sequence
    cat = catalog if catalog is not None else build_catalog(seed)

    lines: list[InvoiceLine] = []
    seen: set[tuple[str, str, str, Decimal, Decimal]] = set()
    row_number = 1
    invoice_seq = 0

    for day in invoice_dates():
        for _ in range(rng.randrange(*INVOICES_PER_DAY)):
            invoice_seq += 1
            # 10 characters exactly, to fit the ERP's IVHDR01 field. The writer refuses
            # to truncate, so this width is load-bearing rather than cosmetic.
            invoice_no = f"HP{day.year % 100:02d}{invoice_seq:06d}"
            customer_index = rng.randrange(len(cat.customers))
            for _ in range(rng.randrange(*LINES_PER_INVOICE)):
                sku = cat.skus[rng.randrange(len(cat.skus))]
                line = _line(rng, cat, row_number, invoice_no, day, customer_index, sku)
                # Nudge the quantity until the line is distinguishable. One extra unit
                # changes nothing about the data's realism and keeps the duplicate count
                # exactly equal to the number seeded in defects.py.
                while duplicate_key(line) in seen:
                    line = _bump_qty(line)
                seen.add(duplicate_key(line))
                lines.append(line)
                row_number += 1

    return tuple(lines)


def _bump_qty(line: InvoiceLine) -> InvoiceLine:
    """Add one unit, keeping the extension consistent.

    A pound-priced line's extension comes from its catch weight, not its quantity, so
    only case lines need their extension recomputed.
    """
    qty = line.qty + 1
    if line.uom == "LB":
        return replace(line, qty=qty)
    return replace(line, qty=qty, ext_amount=extend(qty, line.unit_price))
