"""Turning model output into a mapping, or refusing to.

Strict Structured Outputs makes the *shape* trustworthy. It says nothing about whether a
named column exists, whether a canonical field is real, or whether a transform's
parameters are in range - so this module checks all three against things that already
exist, and it is not made redundant by strict mode. BACKEND.md §4's citation rule, applied
to column names: a reference the retrieved set does not contain is dropped.

**Drop, do not fail.** One unusable field must not lose fifteen good ones. Every drop is
recorded with a reason, returned to the caller, stored on the proposal row and logged at
`warn` - the user is told the model said something unusable rather than shielded from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Final, cast

from src.features.canonical.model import CANONICAL_FIELD_NAMES
from src.features.mapping.proposal_schema import MappingProposal, ProposedTransform
from src.features.mapping.transforms import parse as parse_transform
from src.features.mapping.transforms import serialise as serialise_transform
from src.lib import logger
from src.lib.errors import InvalidTransform

log = logger.get_logger(__name__)


@dataclass(frozen=True)
class DroppedField:
    canonical_field: str
    source_column: str
    reason: str


@dataclass
class GuardedProposal:
    """What survived, and what did not."""

    #: canonical field -> the mapping entry, in the shape `mapping_drafts` stores.
    entries: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    dropped: list[DroppedField] = field(default_factory=list[DroppedField])
    #: Ambiguities the model reported, passed through for the UI to show.
    ambiguities: list[str] = field(default_factory=list[str])


def _clamp_confidence(value: Decimal) -> tuple[Decimal, bool]:
    """To `[0, 1]`. Returns whether it had to be clamped.

    Arrives as a `Decimal` already: the wire type is a JSON number, but the model field
    is declared `Decimal` so nothing in `src/` handles a float at all.
    """
    if not value.is_finite():
        # A NaN confidence is not a number and not an opinion; treat it as none.
        return Decimal(0), True
    if value < 0:
        return Decimal(0), True
    if value > 1:
        return Decimal(1), True
    return value, False


#: Which parameter slot each kind actually uses. Everything else on that kind should be
#: null, and a non-null value there means the model filled in a slot its own `kind` has no
#: use for - which is worth a warning, because it is usually a sign it meant a different
#: kind.
_RELEVANT_SLOT: Final[dict[str, str | None]] = {
    "none": None,
    "trim": None,
    "upper": None,
    "cyymmdd_to_date": None,
    "strip_leading_zeros": None,
    "date_format": "date_format",
    "implied_decimal": "scale",
    "trailing_sign_decimal": "scale",
    "map_values": "value_map",
    "constant": "constant",
    "multiply": "factor",
}
_ALL_SLOTS: Final = ("scale", "date_format", "value_map", "factor", "constant")


def irrelevant_slots(proposed: ProposedTransform) -> list[str]:
    """Slots that are non-null but have nothing to do with this transform's kind."""
    relevant = _RELEVANT_SLOT.get(proposed.kind)
    return [
        slot
        for slot in _ALL_SLOTS
        if slot != relevant and getattr(proposed, slot, None) is not None
    ]


def transform_document(proposed: ProposedTransform) -> dict[str, Any]:
    """Flatten strict mode's every-slot-present shape into the registry's document.

    Strict mode forces all six parameter slots onto every transform with the unused ones
    null; the registry wants only the slot its kind actually uses, and refuses unexpected
    ones. This is that translation, and it is the only place it happens.

    A slot the kind has no use for is **ignored rather than refused**. Under strict mode
    every slot is present by construction, so a stray non-null value cannot change what
    the kind means - and dropping an unambiguous `trim` because the model also wrote a
    `scale` would lose a good mapping for nothing. It is logged, though: filling a slot
    your own kind does not use usually means you meant a different kind, and if the model
    really did mean `implied_decimal` then P5's deterministic validators will say so when
    the column comes back as text where a decimal was expected.
    """
    kind = proposed.kind
    if kind in ("implied_decimal", "trailing_sign_decimal"):
        return {"kind": kind, "scale": proposed.scale}
    if kind == "date_format":
        return {"kind": kind, "fmt": proposed.date_format}
    if kind == "map_values":
        pairs = [
            {"from": pair.from_value, "to": pair.to_value} for pair in (proposed.value_map or [])
        ]
        return {"kind": kind, "table": pairs}
    if kind == "constant":
        return {"kind": kind, "value": proposed.constant}
    if kind == "multiply":
        return {"kind": kind, "factor": proposed.factor}
    return {"kind": kind}


def guard(
    proposal: MappingProposal,
    *,
    known_columns: set[str],
    source_file_id: str,
) -> GuardedProposal:
    """Validate every field of a proposal against reality, dropping what fails."""
    result = GuardedProposal(ambiguities=list(proposal.ambiguities))
    canonical = set(CANONICAL_FIELD_NAMES)
    seen: set[str] = set()

    for proposed in proposal.fields:
        name = proposed.canonical_field.strip()
        column = proposed.source_column.strip()

        if name not in canonical:
            _drop(result, name, column, "not a field in the canonical schema", source_file_id)
            continue
        if column not in known_columns:
            _drop(result, name, column, "not a column in this file", source_file_id)
            continue
        if name in seen:
            # Two source columns claiming one canonical field. The first wins rather than
            # the last, so the result does not depend on list order.
            _drop(
                result,
                name,
                column,
                "another column was already proposed for this field",
                source_file_id,
            )
            continue

        stray = irrelevant_slots(proposed.transform)
        if stray:
            log.warning(
                "llm_transform_slot_ignored",
                source_file_id=source_file_id,
                canonical_field=name,
                kind=proposed.transform.kind,
                ignored_slots=stray,
            )

        try:
            transform = parse_transform(transform_document(proposed.transform))
        except InvalidTransform as error:
            _drop(result, name, column, f"unusable transform: {error.message}", source_file_id)
            continue

        confidence, clamped = _clamp_confidence(proposed.confidence)
        if clamped:
            log.warning(
                "llm_confidence_clamped",
                source_file_id=source_file_id,
                canonical_field=name,
                reported=str(proposed.confidence),
            )

        seen.add(name)
        result.entries[name] = {
            "sourceColumn": column,
            "transform": serialise_transform(transform),
            "origin": "llm",
            "confidence": f"{confidence:f}",
            "reason": proposed.reason.strip()[:2_000] or None,
        }

    return result


def _drop(
    result: GuardedProposal,
    canonical_field: str,
    source_column: str,
    reason: str,
    source_file_id: str,
) -> None:
    result.dropped.append(
        DroppedField(canonical_field=canonical_field, source_column=source_column, reason=reason)
    )
    log.warning(
        "llm_field_dropped",
        source_file_id=source_file_id,
        canonical_field=canonical_field,
        source_column=source_column,
        reason=reason,
    )


def dropped_as_json(dropped: list[DroppedField]) -> list[dict[str, str]]:
    return [
        {
            "canonicalField": item.canonical_field,
            "sourceColumn": item.source_column,
            "reason": item.reason,
        }
        for item in dropped
    ]


# ---------------------------------------------------------------------------------------------
# Look-alike columns on the fields where a wrong pick is silent
# ---------------------------------------------------------------------------------------------

#: Fields where the wrong column of a look-alike pair reconciles perfectly against the wrong
#: party - the silent misattribution of the 2026-10-06 decisions entry. Not items or invoice
#: numbers: a wrong one of those fails to match a rebate program and is loud.
PARTY_FIELDS: Final = frozenset({"customer_id", "invoice_vendor"})

_HUNDREDTH: Final = Decimal("0.01")


def flag_indistinguishable(
    entries: dict[str, dict[str, Any]],
    columns: list[dict[str, Any]],
    model_ambiguities: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Cap and name a party-field proposal whose column the file cannot tell from another.

    The 2026-10-12 live call proposed `customer_id -> BILTNO` at 0.9 with no ambiguity,
    although the prompt asks for both: a prompt instruction is not a guarantee, so this is
    the guarantee. Two columns are look-alikes when the profile cannot separate them - same
    type, same number of distinct values, same share of blanks and the same dominant value
    shape - and neither is blank. Measured on all 12 committed fixtures, that fires on
    exactly one pair, `CUSTNO`/`BILTNO`, and nothing else (docs/plans/proposal-ambiguity-guard.md).

    For `n` look-alikes the file supports at most a 1-in-`n` chance for any one of them, so
    the confidence becomes `min(reported, 1/n)` rounded down to hundredths - never raised.
    The reason gains a sentence saying why, and the ambiguities gain a line the UI shows,
    unless the model already named the field and every column itself.

    Pure: no I/O, and neither argument is mutated. An entry whose column is not in the
    profile (a replay after a layout change) is returned as stored.
    """
    cleaned = clean_ambiguities(model_ambiguities)
    by_name = {str(column["column_name"]): column for column in columns}
    finished = {name: dict(entry) for name, entry in entries.items()}
    added: list[str] = []

    for field_name in sorted(PARTY_FIELDS & finished.keys()):
        entry = finished[field_name]
        chosen = str(entry.get("sourceColumn") or "")
        mark = _fingerprint(by_name.get(chosen))
        if mark is None:
            continue
        group = [str(column["column_name"]) for column in columns if _fingerprint(column) == mark]
        if len(group) < 2:
            continue

        cap = (Decimal(1) / len(group)).quantize(_HUNDREDTH, rounding=ROUND_DOWN)
        reported = _as_decimal(entry.get("confidence"))
        if reported is None or reported > cap:
            entry["confidence"] = f"{cap:f}"
        others = [name for name in group if name != chosen]
        verb = "looks" if len(others) == 1 else "look"
        sentence = (
            f"Lowered because {_join(others)} {verb} the same as {chosen} in this file; only "
            "the distributor's record layout can decide."
        )
        reason = str(entry.get("reason") or "").strip()
        entry["reason"] = f"{reason} {sentence}" if reason else sentence

        if not _already_named(cleaned, field_name, group):
            added.append(
                f"{field_name}: {_join(group)} look the same in this file - same type, value "
                "shape, number of distinct values and share of blanks - so the file cannot say "
                "which is right. Check the distributor's record layout and confirm this field "
                "by hand."
            )
        log.warning(
            "llm_confidence_capped",
            canonical_field=field_name,
            source_column=chosen,
            look_alikes=others,
            reported=str(reported) if reported is not None else None,
            capped=entry["confidence"],
        )

    return finished, cleaned + added


def _fingerprint(column: dict[str, Any] | None) -> tuple[str, int, Decimal, str] | None:
    """What the profile knows about a column; `None` when there is nothing to compare."""
    if column is None:
        return None
    distinct = column.get("distinct_count")
    patterns = cast("list[Any]", column.get("patterns") or [])
    top = (
        cast("dict[str, Any]", patterns[0]) if patterns and isinstance(patterns[0], dict) else None
    )
    if not distinct or top is None:
        return None  # a blank column is "the same" as every other blank one - meaningless
    signature = str(top.get("signature") or "")
    null_pct = _as_decimal(column.get("null_pct"))
    if not signature or null_pct is None:
        return None
    return (str(column.get("type_guess")), int(distinct), null_pct, signature)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def clean_ambiguities(lines: list[str]) -> list[str]:
    """Stripped, blanks dropped, exact duplicates removed - first kept, order kept."""
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        text = str(line).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _already_named(lines: list[str], field_name: str, group: list[str]) -> bool:
    """Whether the model already said this: one of its lines names the field and every column."""
    words = [field_name, *group]
    return any(all(re.search(rf"\b{re.escape(word)}\b", line) for word in words) for line in lines)


def _join(names: list[str]) -> str:
    """`A`, `A and B`, `A, B and C`."""
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"
