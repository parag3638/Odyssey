"""Deterministic validation of a mapping against the real data.

This is the half of the product the LLM does not touch. A proposal - from a model, a
saved spec or a human - is only ever a claim; these functions check the claim against the
file and return a verdict per canonical field. Nothing here consults a model, and nothing
here is heuristic: every verdict is a count against a rule the canonical schema already
states.

The warn/fail policy, stated once
---------------------------------
A verdict answers "is this mapping right?", not "is this data clean?" - the second is
P8's readiness checklist. That distinction is what sets the threshold:

* **fail** - the mapping is probably wrong. More than `FAIL_SHARE` of checked rows could
  not be read as the canonical field's type, or broke its enum, or the column is entirely
  empty. A fifth of a real column does not fail to parse because of data entry; it fails
  because it is the wrong column.
* **warn** - the mapping looks right and the data has gaps. A required field null in some
  rows, a handful of unparseable values, extended amounts that do not tie out. A human
  decides whether that is tolerable, which is why it is not an error: the spec's own
  example is "3% of rows null while the schema marks it required", and refusing to
  proceed there would be the tool overruling the person who knows the business.
* **pass** - every checked row satisfied every rule.

`checked_rows` is always the number of rows sampled, so a share is interpretable without
knowing the sample size.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from src.features.canonical.model import REQUIRED_FIELD_NAMES, UOM_VALUES
from src.features.mapping.apply import AppliedRow
from src.lib.errors import MappingIncomplete

Verdict = Literal["pass", "warn", "fail"]

#: Above this share of bad rows, the mapping itself is the likely fault rather than the
#: data. One fifth: large enough that ordinary dirty data does not trip it, small enough
#: that a genuinely wrong column cannot hide under it.
FAIL_SHARE: Final = Decimal("0.20")

#: How many offending rows to name. Five is what the plan asks for and what a person can
#: actually go and look at.
MAX_EXAMPLES: Final = 5

#: `ext_amount` is allowed to differ from `qty x unit_price` by a cent, or by a tenth of
#: a percent on larger lines - the first for rounding, the second because a distributor
#: may compute the extension at a different intermediate precision. Beyond that the
#: numbers genuinely disagree.
IDENTITY_ABSOLUTE_TOLERANCE: Final = Decimal("0.01")
IDENTITY_RELATIVE_TOLERANCE: Final = Decimal("0.001")

#: Fields the canonical schema types as decimals, with the scale it publishes.
DECIMAL_SCALES: Final = {
    "qty": 3,
    "unit_price": 4,
    "ext_amount": 2,
    "catch_weight_lbs": 3,
    "deviated_price": 4,
}


@dataclass(frozen=True)
class FieldVerdict:
    canonical_field: str
    verdict: Verdict
    checked_rows: int
    failed_rows: int
    detail: dict[str, Any] = field(default_factory=dict[str, Any])
    example_row_numbers: tuple[int, ...] = ()


def as_canonical_decimal(value: object) -> Decimal | None:
    """Read a transformed value as the decimal the canonical schema promises.

    A transform normalises *representation* - `implied_decimal` shifts a point,
    `trailing_sign_decimal` moves a sign - but a CSV column that already reads "18.41"
    needs no transform at all, and `trim` quite correctly hands back a string. Coercing
    to the canonical type is therefore the validator's job, and it is exactly what the
    plan means by checking "parseability": can this value be read as what the field
    claims to be?

    `None` means it cannot. NaN and Infinity are `None` too - they parse as Decimals and
    are not numbers anybody can invoice.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    return None


def is_absent(value: object) -> bool:
    """An empty field means absent (`canonical/FORMAT.md`), and so does whitespace.

    Without this, a `trim` of an empty source column yields `""` - not `None` - and every
    nullability rule silently passes on a column that is entirely blank.
    """
    if value is None:
        return True
    return isinstance(value, str) and value.strip() == ""


def _amount_str(value: Decimal) -> str:
    """A money difference, written the way a person would write it.

    `Decimal` keeps the scale arithmetic gives it, so `169.20 - 2.00 * 5.6400` is
    `157.920000`. Normalising makes that `157.92` while leaving a genuine sub-cent
    difference like `0.005` intact - which matters, because the sub-cent case is exactly
    what the tolerance is about. `normalize()` alone can return `1E+2` for round
    hundreds, so an integral result is re-expanded.
    """
    normalised = value.normalize()
    if normalised == normalised.to_integral_value():
        return f"{normalised.quantize(Decimal(1)):f}"
    return f"{normalised:f}"


def _share(count: int, total: int) -> Decimal:
    if total == 0:
        return Decimal(0)
    return (Decimal(count) / Decimal(total)).quantize(Decimal("0.0001"))


def _verdict_for(bad: int, total: int) -> Verdict:
    if bad == 0:
        return "pass"
    if total and _share(bad, total) > FAIL_SHARE:
        return "fail"
    return "warn"


def require_complete(mapped_fields: Sequence[str]) -> None:
    """Raise `422 mapping_incomplete` naming every required field still unmapped.

    Checked before validation rather than reported as a verdict: a validator cannot say
    anything useful about a field that has no column, and a caller who asked to validate
    an incomplete mapping has made a different mistake from one whose mapping is wrong.
    """
    missing = [name for name in REQUIRED_FIELD_NAMES if name not in set(mapped_fields)]
    if not missing:
        return
    raise MappingIncomplete(
        f"{len(missing)} required field{'s' if len(missing) != 1 else ''} "
        f"{'are' if len(missing) != 1 else 'is'} not mapped yet: "
        f"{', '.join(missing)}. Map {'them' if len(missing) != 1 else 'it'} and validate "
        "again.",
        fields=dict.fromkeys(missing, "This field is required and has no source column."),
    )


def validate(rows: Sequence[AppliedRow], mapped_fields: Sequence[str]) -> list[FieldVerdict]:
    """One verdict per mapped canonical field, plus the cross-field identity check."""
    verdicts = [_validate_field(name, rows) for name in mapped_fields]
    identity = _validate_extension_identity(rows, mapped_fields)
    if identity is not None:
        verdicts.append(identity)
    return verdicts


def _validate_field(name: str, rows: Sequence[AppliedRow]) -> FieldVerdict:
    total = len(rows)
    unreadable = [row.row_number for row in rows if name in row.failures]
    present = [row for row in rows if name not in row.failures]
    nulls = [row.row_number for row in present if is_absent(row.values.get(name))]
    values = [row.values[name] for row in present if not is_absent(row.values.get(name))]

    detail: dict[str, Any] = {
        "nullRows": len(nulls),
        "nullShare": f"{_share(len(nulls), total):f}",
    }

    # 1. Values the transform refused outright.
    if unreadable:
        detail["unreadableRows"] = len(unreadable)
        detail["unreadableShare"] = f"{_share(len(unreadable), total):f}"
        detail["firstMessage"] = next(row.failures[name] for row in rows if name in row.failures)
        return FieldVerdict(
            canonical_field=name,
            verdict=_verdict_for(len(unreadable), total),
            checked_rows=total,
            failed_rows=len(unreadable),
            detail=detail,
            example_row_numbers=tuple(unreadable[:MAX_EXAMPLES]),
        )

    # 2. A column with nothing in it. For a required field that is the wrong column,
    #    whatever its name says. For an optional one it may be the truth - a case-only
    #    file has no catch weights - so it warns rather than fails: worth a human glance,
    #    not worth blocking.
    if total and not values:
        required = name in REQUIRED_FIELD_NAMES
        detail["reason"] = (
            "every sampled row is empty"
            if required
            else "every sampled row is empty, and this field is optional"
        )
        return FieldVerdict(
            canonical_field=name,
            verdict="fail" if required else "warn",
            checked_rows=total,
            failed_rows=len(nulls),
            detail=detail,
            example_row_numbers=tuple(nulls[:MAX_EXAMPLES]),
        )

    # 3. Rules the canonical schema states about this specific field.
    rule = _rule_failures(name, present)
    if rule is not None:
        failing_rows, rule_detail = rule
        detail.update(rule_detail)
        return FieldVerdict(
            canonical_field=name,
            verdict=_verdict_for(len(failing_rows), total),
            checked_rows=total,
            failed_rows=len(failing_rows),
            detail=detail,
            example_row_numbers=tuple(failing_rows[:MAX_EXAMPLES]),
        )

    # 4. Required, readable, but absent in some rows: a human's call, not an error.
    if name in REQUIRED_FIELD_NAMES and nulls:
        detail["reason"] = "required by the canonical schema but empty in some rows"
        return FieldVerdict(
            canonical_field=name,
            verdict=_verdict_for(len(nulls), total),
            checked_rows=total,
            failed_rows=len(nulls),
            detail=detail,
            example_row_numbers=tuple(nulls[:MAX_EXAMPLES]),
        )

    return FieldVerdict(
        canonical_field=name,
        verdict="pass",
        checked_rows=total,
        failed_rows=0,
        detail=detail,
    )


def _rule_failures(
    name: str, present: Sequence[AppliedRow]
) -> tuple[list[int], dict[str, Any]] | None:
    """Field-specific rules. Returns the offending row numbers and detail, or None."""
    if name == "uom":
        unmapped: dict[str, int] = {}
        offending: list[int] = []
        for row in present:
            value = row.values.get(name)
            if is_absent(value):
                continue
            text = str(value).strip()
            if text not in UOM_VALUES:
                unmapped[text] = unmapped.get(text, 0) + 1
                offending.append(row.row_number)
        if not offending:
            return None
        return offending, {
            "reason": "values outside the canonical unit-of-measure set",
            "allowed": list(UOM_VALUES),
            # Named explicitly, because the fix is to add them to a `map_values`
            # transform and the user needs to know which ones.
            "unmappedValues": sorted(unmapped),
            "unmappedCounts": {value: unmapped[value] for value in sorted(unmapped)},
        }

    if name == "invoice_date":
        offending = [
            row.row_number
            for row in present
            if not is_absent(row.values.get(name)) and not isinstance(row.values.get(name), date)
        ]
        if not offending:
            return None
        return offending, {"reason": "values that are not dates"}

    if name == "is_deviated":
        offending = []
        for row in present:
            value = row.values.get(name)
            if is_absent(value):
                continue
            if not isinstance(value, bool) and str(value).strip().lower() not in (
                "true",
                "false",
            ):
                offending.append(row.row_number)
        if not offending:
            return None
        return offending, {
            "reason": "values that are not true/false",
            "hint": "Map the source flag onto true/false with a map_values transform.",
        }

    if name == "pack_size":
        offending = []
        for row in present:
            value = row.values.get(name)
            if is_absent(value):
                continue
            if not _is_whole_number(value):
                offending.append(row.row_number)
        if not offending:
            return None
        return offending, {"reason": "values that are not whole numbers"}

    if name in DECIMAL_SCALES:
        scale = DECIMAL_SCALES[name]
        offending = []
        for row in present:
            value = row.values.get(name)
            if is_absent(value):
                continue
            amount = as_canonical_decimal(value)
            if amount is None:
                # Unreadable as a number at all - NaN and Infinity included, whose
                # `exponent` is a letter rather than an int, so asking about decimal
                # places would be the wrong question as well as a type error.
                offending.append(row.row_number)
                continue
            exponent = amount.as_tuple().exponent
            if isinstance(exponent, int) and -exponent > scale:
                offending.append(row.row_number)
        if not offending:
            return None
        return offending, {
            "reason": f"values that are not decimals with at most {scale} decimal places",
            "allowedScale": scale,
        }

    return None


def _is_whole_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, Decimal):
        return value == value.to_integral_value()
    if isinstance(value, str):
        return value.strip().isdigit()
    return False


def _validate_extension_identity(
    rows: Sequence[AppliedRow], mapped_fields: Sequence[str]
) -> FieldVerdict | None:
    """`ext_amount` should equal `unit_price` times the quantity billed.

    The strongest check available without leaving the file: it uses columns that were
    mapped independently, so it catches the mapping error no single-column rule can - a
    `qty` pointed at a pack size, say, where every value is a perfectly valid number.

    **Which quantity is billed depends on the line.** A catch-weight item is priced per
    pound, so its extension is `catch_weight_lbs` x `unit_price`; a case or each line
    extends on `qty`. Checking every line against `qty` reported 100 of 500 mismatches on
    the committed sample, and 98 of those were catch-weight lines where the identity does
    not hold in that form. A validator that cries wolf on a whole class of line teaches
    people to ignore it.

    **A line passes if *either* basis ties out**, rather than the basis being chosen up
    front by "is there a weight?". That rule was binary and brittle in both directions:
    the tester showed a correct, qty-priced line carrying a 0.001 rounding artefact in its
    weight column flipping to the weight basis and failing outright. Trying both costs one
    extra multiplication and removes the cliff edge - and a line that ties out on either
    reading is a line whose three columns agree, which is what this check is actually
    asking.

    Known limitation, recorded rather than papered over: on a line that ties out on weight,
    `qty` gets no coverage here at all. A file of mostly catch-weight lines can therefore
    carry a completely wrong `qty` mapping past this check - every value well-formed, the
    extension tying out on pounds. Catching that needs a judgement about whether a
    quantity is *plausible*, which belongs with P8's readiness checks (alongside
    "catch-weight items missing weight") and not in an arithmetic identity.
    """
    needed = ("qty", "unit_price", "ext_amount")
    if not all(name in set(mapped_fields) for name in needed):
        return None

    checked = 0
    tied_on_weight = 0
    tied_on_qty = 0
    offending: list[int] = []
    worst = Decimal(0)
    for row in rows:
        if any(name in row.failures for name in needed):
            continue
        qty = as_canonical_decimal(row.values.get("qty"))
        price = as_canonical_decimal(row.values.get("unit_price"))
        extended = as_canonical_decimal(row.values.get("ext_amount"))
        if qty is None or price is None or extended is None:
            # A row where one of the three is absent or unreadable is not evidence about
            # the identity either way; the per-field verdicts already report it.
            continue

        checked += 1
        tolerance = max(IDENTITY_ABSOLUTE_TOLERANCE, abs(extended) * IDENTITY_RELATIVE_TOLERANCE)
        by_qty = abs(extended - qty * price)

        weight = as_canonical_decimal(row.values.get("catch_weight_lbs"))
        by_weight = None if weight is None else abs(extended - weight * price)

        if by_weight is not None and by_weight <= tolerance:
            tied_on_weight += 1
            continue
        if by_qty <= tolerance:
            tied_on_qty += 1
            continue

        offending.append(row.row_number)
        # The smaller of the two is the honest "how far out is this line?": reporting the
        # qty difference on a catch-weight line would describe a reading nobody intended.
        closest = by_qty if by_weight is None else min(by_qty, by_weight)
        worst = max(worst, closest)

    if checked == 0:
        return None

    return FieldVerdict(
        canonical_field="ext_amount_identity",
        verdict=_verdict_for(len(offending), checked),
        checked_rows=checked,
        failed_rows=len(offending),
        detail={
            "reason": (
                "rows where ext_amount equals unit_price times neither the quantity nor "
                "the catch weight"
            ),
            "rowsTiedOnCatchWeight": tied_on_weight,
            "rowsTiedOnQty": tied_on_qty,
            "largestDifference": _amount_str(worst),
            "absoluteTolerance": f"{IDENTITY_ABSOLUTE_TOLERANCE:f}",
            "relativeTolerance": f"{IDENTITY_RELATIVE_TOLERANCE:f}",
            "mismatchShare": f"{_share(len(offending), checked):f}",
        },
        example_row_numbers=tuple(offending[:MAX_EXAMPLES]),
    )
