"""Seeded readiness defects, and the rule that makes them scoreable.

Seven defects from the spec's readiness checklist are injected into the clean lines.
The rule that makes the whole plan work is **one seeded defect per line**: a line
carries at most one, enforced by a claimed-rows set rather than by hope. Without it,
P8's per-check counts and P13's per-cause dollar attribution would both double-count
the same line and no score would mean anything.

Three of the seven are not line-level at all, and they are handled honestly rather than
forced into the same shape:

- **date_gap** is an *absence*. Its affected rows are the ones removed, so the oracle
  records the missing business days instead of row numbers that no longer exist.
- **footer_total_mismatch** is a file-level defect. The oracle records the dollar delta.
- **duplicate_line** adds rows. Both copies are affected, and the duplicate is inserted
  next to its original because that is where a re-keyed line actually lands.

Row numbers are assigned **after** all of this, so they describe the file as shipped.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from random import Random
from typing import Final

from src.generator.catalog import manufacturer_by_code, redistributor_numbers
from src.generator.invoices import InvoiceLine, build_lines
from src.generator.seed import SEED

CHECK_MIXED_UOM: Final = "mixed_uom_without_pack_size"
CHECK_CATCH_WEIGHT: Final = "catch_weight_missing"
CHECK_EXTENSION: Final = "extension_mismatch"
CHECK_DUPLICATE: Final = "duplicate_line"
CHECK_DATE_GAP: Final = "date_gap"
CHECK_FOOTER: Final = "footer_total_mismatch"
CHECK_VENDOR: Final = "redistributor_vendor_mismatch"

#: All seven, in the order the oracle lists them.
CHECK_CODES: Final = (
    CHECK_MIXED_UOM,
    CHECK_CATCH_WEIGHT,
    CHECK_EXTENSION,
    CHECK_DUPLICATE,
    CHECK_DATE_GAP,
    CHECK_FOOTER,
    CHECK_VENDOR,
)

#: How many lines each line-level defect claims.
SEED_COUNTS: Final = {
    CHECK_MIXED_UOM: 180,
    CHECK_CATCH_WEIGHT: 140,
    CHECK_EXTENSION: 95,
    CHECK_DUPLICATE: 60,
    CHECK_VENDOR: 120,
}

#: The date gap: five consecutive business days removed from month 3.
GAP_START: Final = date(2026, 3, 16)
GAP_DAYS: Final = 5

#: The footer total understates the detail sum by this much.
FOOTER_DELTA_USD: Final = Decimal("1842.57")

#: `ext_amount` is wrong by this much on an extension_mismatch line - comfortably past
#: the $0.01 tolerance P8 applies, so the check cannot be accused of firing on rounding.
EXTENSION_ERROR_USD: Final = Decimal("3.50")


@dataclass(frozen=True)
class SeededFile:
    """The lines as shipped, plus everything needed to write the oracle."""

    lines: tuple[InvoiceLine, ...]
    #: check code -> affected row numbers, in the final numbering.
    affected_rows: dict[str, tuple[int, ...]]
    #: Business days removed to create the gap.
    gap_dates: tuple[date, ...]
    #: Footer total as written, and the true detail sum.
    footer_total: Decimal
    detail_total: Decimal
    #: row_number -> the catch weight that was stripped from the export.
    #: The legacy system still had these and paid on them, so the variance explainer
    #: needs them to know what the new calculation could not compute.
    removed_weights: dict[int, Decimal]


def _business_days(start: date, count: int) -> list[date]:
    out: list[date] = []
    current = start
    while len(out) < count:
        if current.weekday() < 5:
            out.append(current)
        current += timedelta(days=1)
    return out


def build_seeded_file(seed: int = SEED) -> SeededFile:
    """Clean lines in, defective-but-scoreable file out."""
    rng = Random(seed + 2)
    clean = build_lines(seed)

    # --- 1. the date gap, first: it removes lines, so it must precede any claim ---
    gap_dates = tuple(_business_days(GAP_START, GAP_DAYS))
    kept = [line for line in clean if line.invoice_date not in gap_dates]

    # --- 2. claim disjoint line sets for each line-level defect ---
    claimed: set[int] = set()

    def claim(candidates: list[int], count: int) -> list[int]:
        """Take `count` unclaimed indices, deterministically, lowest-index first.

        Sorted before sampling because a set's iteration order is not stable across
        processes; the whole generator is worthless if it is not reproducible.
        """
        free = sorted(i for i in candidates if i not in claimed)
        chosen = sorted(rng.sample(free, min(count, len(free))))
        claimed.update(chosen)
        return chosen

    case_idx = [i for i, line in enumerate(kept) if line.uom == "CS"]
    lb_idx = [i for i, line in enumerate(kept) if line.uom == "LB"]
    # Only redistributor-only brands can carry this defect: for any other brand a direct
    # vendor number is legitimate, so a seeded "wrong vendor" there would be
    # indistinguishable from clean data and therefore undetectable.
    redistributor_set = redistributor_numbers()
    redistributor_idx = [
        i
        for i, line in enumerate(kept)
        if line.invoice_vendor in redistributor_set
        and (mfr := manufacturer_by_code(line.brand_manufacturer)) is not None
        and mfr.redistributor_only
    ]
    all_idx = list(range(len(kept)))

    mixed = claim(case_idx, SEED_COUNTS[CHECK_MIXED_UOM])
    catch = claim(lb_idx, SEED_COUNTS[CHECK_CATCH_WEIGHT])
    vendor = claim(redistributor_idx, SEED_COUNTS[CHECK_VENDOR])
    extension = claim(all_idx, SEED_COUNTS[CHECK_EXTENSION])
    duplicate = claim(all_idx, SEED_COUNTS[CHECK_DUPLICATE])

    # --- 3. mutate ---
    mutated = list(kept)

    for i in mixed:
        # An each-priced line with no pack size: nothing can convert it to cases, which
        # is what makes a mixed-UOM file uncomputable rather than merely untidy.
        mutated[i] = replace(mutated[i], uom="EA", pack_size=None)

    removed_weights_by_index: dict[int, Decimal] = {}
    for i in catch:
        original = mutated[i].catch_weight_lbs
        assert original is not None
        removed_weights_by_index[i] = original
        mutated[i] = replace(mutated[i], catch_weight_lbs=None)

    for i in vendor:
        # Billed direct under the manufacturer's own vendor number when a redistributor
        # actually invoiced it. For a redistributor-only brand that is impossible, so
        # the line is provably misattributed from the file alone.
        mfr = manufacturer_by_code(mutated[i].brand_manufacturer)
        assert mfr is not None
        mutated[i] = replace(mutated[i], invoice_vendor=mfr.vendor_number)

    for i in extension:
        mutated[i] = replace(mutated[i], ext_amount=mutated[i].ext_amount + EXTENSION_ERROR_USD)

    # --- 4. duplicates, inserted next to their originals ---
    duplicate_set = set(duplicate)
    with_dupes: list[InvoiceLine] = []
    duplicate_pairs: list[tuple[int, int]] = []  # positions in with_dupes
    for i, line in enumerate(mutated):
        with_dupes.append(line)
        if i in duplicate_set:
            first = len(with_dupes) - 1
            with_dupes.append(line)
            duplicate_pairs.append((first, len(with_dupes) - 1))

    # --- 5. renumber: row numbers describe the file as shipped ---
    position_to_row = {pos: pos + 1 for pos in range(len(with_dupes))}
    final = tuple(
        replace(line, row_number=position_to_row[pos]) for pos, line in enumerate(with_dupes)
    )

    # Map the pre-duplication indices onto final row numbers.
    shift: list[int] = []
    offset = 0
    for i in range(len(mutated)):
        shift.append(i + offset)
        if i in duplicate_set:
            offset += 1

    def rows(indices: list[int]) -> tuple[int, ...]:
        return tuple(position_to_row[shift[i]] for i in indices)

    duplicate_rows = tuple(sorted(position_to_row[p] for pair in duplicate_pairs for p in pair))

    detail_total = sum((line.ext_amount for line in final), Decimal("0"))
    return SeededFile(
        lines=final,
        affected_rows={
            CHECK_MIXED_UOM: rows(mixed),
            CHECK_CATCH_WEIGHT: rows(catch),
            CHECK_EXTENSION: rows(extension),
            CHECK_DUPLICATE: duplicate_rows,
            CHECK_DATE_GAP: (),
            CHECK_FOOTER: (),
            CHECK_VENDOR: rows(vendor),
        },
        gap_dates=gap_dates,
        footer_total=detail_total - FOOTER_DELTA_USD,
        detail_total=detail_total,
        removed_weights={
            position_to_row[shift[i]]: weight
            for i, weight in sorted(removed_weights_by_index.items())
        },
    )
