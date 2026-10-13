"""The five cause rules. Pure: a line and its context in, a claim or nothing out.

No database, no HTTP, no session. Every rule reads fields `canonical_lines` and `rebate_calc_lines`
actually store, plus reference data loaded once - so attribution is reproducible from production
data alone and never from the generator, which production must not import.

**Order, and what it is really worth.** Rules run in a fixed order and the first match wins, so the
answer is deterministic and explainable. Measured on the seeded data, though, the order is *inert*:
the oracle's five row sets hold 521 rows and 521 **distinct** rows, so nothing is claimed twice.
The order is kept for the data nobody has yet; the disjointness is what gets the test.

**Two rules need facts this application cannot derive**, and both are seeded rather than guessed or
hardcoded - `rebate_programs.redistributor_rate` (migration 0027) and
`customers.legacy_period_cutoff_day` (migration 0029). Each is nullable, and a missing one costs
that cause's dollars honestly: the rule claims nothing, the money lands in residue, and the
explained percentage falls rather than resting on an invention.

**One trap worth naming.** `generator/catalog.py` says of `REDISTRIBUTOR_RATE` that it is "lower
than every brand rate, so a misattributed line is always underpaid". That is **false for the only
program this cause touches**: every misattributed line belongs to a per-pound billback at $0.18/lb,
and 0.65 > 0.18, so legacy paid *more*. The rule implements the arithmetic, not the sentence - and
the generator stores a magnitude while the variance carries a sign, which is why a sign error here
would survive an eyeball check and fail conservation by $2,171.62.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from src.features.variance.codes import (
    CASE_EACH_CONVERSION,
    DATE_CUTOFF,
    DUPLICATE_LINE,
    MISSING_CATCH_WEIGHT,
    REDISTRIBUTOR_MISATTRIBUTION,
)
from src.lib.manufacturers import ManufacturerLookup
from src.lib.money import quantize_amount

#: P11's skipped reasons, read rather than re-derived. 125 `missing_pack_size` and 70
#: `missing_catch_weight` already equal the oracle's line counts by construction.
SKIPPED_MISSING_PACK: Final = "missing_pack_size"
SKIPPED_MISSING_WEIGHT: Final = "missing_catch_weight"


@dataclass(frozen=True)
class ProgramTerms:
    program_id: UUID
    rate: Decimal
    uom_basis: str
    #: Seeded commercial data (0027). None means the customer has not told us, and the
    #: redistributor rule then claims the cause with no dollars.
    redistributor_rate: Decimal | None


@dataclass(frozen=True)
class WeightEstimate:
    """A SKU's mean catch weight **per unit**, with the pool it came from."""

    per_unit: Decimal
    sample_lines: int


@dataclass(frozen=True)
class AttributionLine:
    """One canonical line with the provenance P11 stored for it."""

    canonical_line_id: UUID
    source_row_number: int
    invoice_no: str
    invoice_date: date
    dist_item_id: str
    brand_manufacturer: str
    invoice_vendor: str
    qty: Decimal
    uom: str
    pack_size: int | None
    catch_weight_lbs: Decimal | None
    ext_amount: Decimal
    program_id: UUID
    line_status: str
    skipped_reason: str | None
    basis_contribution: Decimal
    amount_contribution: Decimal


@dataclass(frozen=True)
class RuleContext:
    programs: dict[UUID, ProgramTerms]
    lookup: ManufacturerLookup
    #: Canonical line ids that are the *second* copy of a duplicated pair, computed once over the
    #: run rather than per line - a rule cannot see its neighbours.
    duplicate_copies: frozenset[UUID]
    #: `dist_item_id` -> the SKU's mean weight per unit, over lines that still have a weight.
    weights: dict[str, WeightEstimate]
    #: Seeded (0029). None means no `date_cutoff` claim is made at all.
    cutoff_day: date | None


@dataclass(frozen=True)
class Claim:
    root_cause: str
    #: Signed, in the direction of the variance: `legacy - calculated`. Positive means the legacy
    #: system paid more than we do on account of this cause.
    contribution: Decimal
    evidence: dict[str, Any]
    #: True only for `date_cutoff`, which **relocates** money rather than creating it: the job
    #: writes this amount negative on the line's own period and positive on the next one.
    relocates: bool = False


def _duplicate(line: AttributionLine, context: RuleContext) -> Claim | None:
    """The legacy system paid both copies of a re-keyed line; we pay one.

    Claims the *second* copy, which is the one the legacy system paid twice. A copy our own
    calculation could not value contributes nothing, so it is left alone - the generator skips
    those too.
    """
    if line.canonical_line_id not in context.duplicate_copies:
        return None
    if line.amount_contribution == 0:
        return None
    return Claim(
        root_cause=DUPLICATE_LINE,
        contribution=line.amount_contribution,
        evidence={
            "invoiceNo": line.invoice_no,
            "distItemId": line.dist_item_id,
            "sourceRowNumber": line.source_row_number,
            "note": "This line is a duplicate of an earlier one and the legacy system paid both.",
        },
    )


def _case_each(line: AttributionLine, context: RuleContext) -> Claim | None:
    """The legacy system counted eaches as whole cases; we cannot value the line at all.

    Our calculation skipped it for want of a pack size, so our side is zero and the whole of the
    legacy system's payment is the variance: `rate x qty`, the quantity it mistook for cases.
    """
    if line.skipped_reason != SKIPPED_MISSING_PACK:
        return None
    program = context.programs.get(line.program_id)
    if program is None:
        return None
    return Claim(
        root_cause=CASE_EACH_CONVERSION,
        contribution=quantize_amount(program.rate * line.qty),
        evidence={
            "qty": f"{line.qty:f}",
            "uom": line.uom,
            "rate": f"{program.rate:f}",
            "sourceRowNumber": line.source_row_number,
            "note": (
                "Priced by the each with no pack size. The legacy system treated the quantity as "
                "cases; we could not work out a case count at all."
            ),
        },
    )


def _catch_weight(line: AttributionLine, context: RuleContext) -> Claim | None:
    """The weight was stripped from the export, so the amount has to be estimated.

    **The SKU's mean weight *per unit* times this line's qty**, not the SKU's mean weight. A catch
    weight scales with the quantity on the line, so a mean weight mixes a 2-case line with a 40-case
    one and is the wrong shape; measured against the oracle, per-unit is 15x more accurate (-0.78%
    against -11.60%). The evidence carries the estimate, the pool size and the SKU, so a reader can
    judge the estimate rather than take it.

    An estimate is the honest answer here: the true weight is **gone** from the file. With no pool
    for the SKU the rule claims nothing and the dollars fall to residue.
    """
    if line.skipped_reason != SKIPPED_MISSING_WEIGHT:
        return None
    program = context.programs.get(line.program_id)
    estimate = context.weights.get(line.dist_item_id)
    if program is None or estimate is None or estimate.sample_lines == 0:
        return None
    weight = estimate.per_unit * line.qty
    return Claim(
        root_cause=MISSING_CATCH_WEIGHT,
        contribution=quantize_amount(program.rate * weight),
        evidence={
            "estimatedWeightLbs": f"{weight:f}",
            "meanWeightPerUnitLbs": f"{estimate.per_unit:f}",
            "sampleLines": estimate.sample_lines,
            "distItemId": line.dist_item_id,
            "estimated": True,
            "note": (
                "The shipped weight is missing from the export, so this is an estimate from this "
                "item's other lines."
            ),
        },
    )


def _redistributor(line: AttributionLine, context: RuleContext) -> Claim | None:
    """The legacy system looked the program up by vendor and paid a redistributor's rate.

    The predicate is P8's readiness check, reused rather than copied: the brand is one this
    distributor only buys through a redistributor, and the line is billed under the brand's own
    direct vendor - so it is provably misattributed from the file alone.

    `-(program.rate - redistributor_rate) x basis`. With `redistributor_rate` null the cause is
    still named, with no dollars, and the money lands in residue.
    """
    entry = context.lookup.find(line.brand_manufacturer)
    if entry is None or not entry.redistributor_only:
        return None
    if not entry.bills_direct_as(line.invoice_vendor):
        return None
    program = context.programs.get(line.program_id)
    if program is None or line.line_status != "contributed":
        return None
    if program.redistributor_rate is None:
        return Claim(
            root_cause=REDISTRIBUTOR_MISATTRIBUTION,
            contribution=Decimal("0.00"),
            evidence={
                "invoiceVendor": line.invoice_vendor,
                "manufacturer": entry.name,
                "redistributorRateKnown": False,
                "note": (
                    "This line is billed under the brand's own vendor for a brand bought only "
                    "through a redistributor. We do not have the redistributor's rate, so the "
                    "amount cannot be worked out."
                ),
            },
        )
    difference = program.rate - program.redistributor_rate
    return Claim(
        root_cause=REDISTRIBUTOR_MISATTRIBUTION,
        # Negated: the variance is `legacy - calculated`, and the legacy system applied the
        # redistributor rate where we apply the program's. Where the redistributor rate is the
        # higher of the two - which is the case for every line this touches on the seeded data -
        # the legacy system paid MORE, and the contribution is positive.
        contribution=-quantize_amount(difference * line.basis_contribution),
        evidence={
            "invoiceVendor": line.invoice_vendor,
            "manufacturer": entry.name,
            "programRate": f"{program.rate:f}",
            "redistributorRate": f"{program.redistributor_rate:f}",
            "basis": f"{line.basis_contribution:f}",
            "redistributorRateKnown": True,
            "note": (
                "Billed under the brand's own vendor for a brand bought only through a "
                "redistributor, so the legacy system priced it at the redistributor's rate."
            ),
        },
    )


def _date_cutoff(line: AttributionLine, context: RuleContext) -> Claim | None:
    """The legacy system posted this day's lines into the following period.

    **Relocation, not creation**: the same dollars leave one month and arrive in the next, which is
    why the claim is marked `relocates` and the job writes two links naming the same line. It is
    also why this cause cancels in any whole-file total, and why conservation can only be asserted
    per variance row.

    The day is seeded (`customers.legacy_period_cutoff_day`), never inferred. "The last day of any
    month" would invent a relocation in every month, and reading it off the variance signs would let
    the rule claim whatever makes the arithmetic work.
    """
    if context.cutoff_day is None or line.invoice_date != context.cutoff_day:
        return None
    if line.amount_contribution == 0:
        return None
    return Claim(
        root_cause=DATE_CUTOFF,
        contribution=line.amount_contribution,
        relocates=True,
        evidence={
            "invoiceDate": line.invoice_date.isoformat(),
            "sourceRowNumber": line.source_row_number,
            "note": (
                "Invoiced on the legacy system's period cutoff, so it was paid in the following "
                "period instead of this one."
            ),
        },
    )


#: Fixed order, first match wins. Most mechanically certain first: a duplicate is a fact about two
#: rows, a skipped reason is a fact P11 already recorded, a vendor mismatch is a fact about the
#: reference list, and a cutoff date is the most inferential of the five.
RULES: Final[tuple[Callable[[AttributionLine, RuleContext], Claim | None], ...]] = (
    _duplicate,
    _case_each,
    _catch_weight,
    _redistributor,
    _date_cutoff,
)


def classify(
    line: AttributionLine, context: RuleContext, failures: dict[str, int] | None = None
) -> Claim | None:
    """The first rule that claims this line, or `None`.

    **A rule that raises is isolated, not fatal.** One unexpected exception - a `None` where a
    number was assumed, an empty weight pool - would otherwise lose the entire report for every
    cause, which is the opposite of what a degraded run should do. The failing rule is skipped for
    this line, the next rule gets its chance, and the failure is counted so the summary can say the
    run is incomplete. A silently lower percentage that *looks* like a clean answer is the failure
    mode this phase exists to avoid.
    """
    for rule in RULES:
        try:
            claim = rule(line, context)
        except Exception:
            if failures is not None:
                failures[rule.__name__.lstrip("_")] = failures.get(rule.__name__.lstrip("_"), 0) + 1
            continue
        if claim is not None:
            return claim
    return None
