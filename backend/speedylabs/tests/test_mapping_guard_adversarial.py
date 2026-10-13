"""Adversarial tester pass on `proposal_guard.guard()` (mvp.md 6.4), the BACKEND.md §4
"model output is untrusted" boundary for this phase. Each test tries to get something
through it that should not get through: a near-miss name, a column that is real but not
*here*, a lookalike character, an oversized table, a non-finite confidence, an oversized
reason, and a flood of fields.

The guarantee under test throughout: a bad field is *dropped*, never silently accepted
and never allowed to fail the whole proposal, and nothing dropped-and-rejected ever
reaches `entries` (which is what gets written into the mapping draft and, eventually,
applied to real files).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.features.canonical.model import CANONICAL_FIELD_NAMES
from src.features.mapping.proposal_guard import (
    _clamp_confidence,  # pyright: ignore[reportPrivateUsage]
    guard,
)
from src.features.mapping.proposal_schema import MappingProposal, ProposedField, ProposedTransform

FILE_ID = "test-file"
KNOWN_COLUMNS = {"IVHDR01", "CUSTNO", "BILTNO", "UMCODE"}


def _transform(kind: str = "trim", **slots: Any) -> ProposedTransform:
    base = {"scale": None, "date_format": None, "value_map": None, "factor": None, "constant": None}
    base.update(slots)
    return ProposedTransform(kind=kind, **base)  # type: ignore[arg-type]


def _field(
    canonical_field: str,
    source_column: str,
    *,
    confidence: Decimal = Decimal("0.9"),
    reason: str = "because",
    transform: ProposedTransform | None = None,
) -> ProposedField:
    return ProposedField(
        canonical_field=canonical_field,
        source_column=source_column,
        transform=transform or _transform(),
        confidence=confidence,
        reason=reason,
    )


def _guard(fields: list[ProposedField], known_columns: set[str] = KNOWN_COLUMNS) -> Any:
    proposal = MappingProposal(fields=fields, ambiguities=[])
    return guard(proposal, known_columns=known_columns, source_file_id=FILE_ID)


# ----------------------------------------------------------------------------------------
# Near-miss canonical field and source column names
# ----------------------------------------------------------------------------------------


def test_a_canonical_field_differing_only_by_case_is_dropped_not_fuzzy_matched() -> None:
    """`Invoice_No` is not `invoice_no`. Case-insensitive matching would be a quiet
    widening of the trusted set - exactly what the citation rule forbids."""
    result = _guard([_field("Invoice_No", "IVHDR01")])
    assert result.entries == {}
    assert result.dropped[0].reason == "not a field in the canonical schema"


def test_a_canonical_field_with_only_whitespace_difference_is_accepted_after_strip() -> None:
    """Surrounding whitespace is noise the model might add; it is stripped, not treated
    as a different name - this is the one case where leniency is correct because the
    *stripped* value still has to pass the real canonical-set check."""
    result = _guard([_field("  invoice_no  ", "IVHDR01")])
    assert "invoice_no" in result.entries


def test_a_canonical_field_with_an_embedded_nbsp_still_strips_cleanly() -> None:
    """Python's `str.strip()` treats U+00A0 (NBSP) as whitespace - verified here rather
    than assumed, since a model emitting a non-breaking space instead of a regular one
    is exactly the kind of near-miss this boundary exists for."""
    result = _guard([_field(" invoice_no ", "IVHDR01")])  # noqa: RUF001
    assert "invoice_no" in result.entries


def test_a_canonical_field_with_a_zero_width_space_inside_it_is_dropped() -> None:
    """A zero-width space is *not* whitespace by Python's reckoning, so it survives
    `.strip()` and must fail the exact-match check - dropped, not silently accepted as
    a fuzzy match and not crashing the guard."""
    poisoned = "invoice​_no"  # zero-width space is not in canonical, is not .isspace()
    assert not poisoned.isspace()
    result = _guard([_field(poisoned, "IVHDR01")])
    assert result.entries == {}
    assert result.dropped[0].canonical_field == poisoned


def test_a_cyrillic_lookalike_column_name_is_dropped_not_treated_as_the_latin_original() -> None:
    """`CUSTNO` with its first letter swapped for the Cyrillic capital letter ES
    (U+0421, which renders identically to Latin capital C) is a different string
    byte-for-byte and must not match the real `CUSTNO` - a homoglyph bypassing the known-
    column check would mean the model's exact-match claim ('copied exactly') cannot be
    trusted even when it looks right at a glance."""
    lookalike = "СUSTNO"  # U+0421 CYRILLIC CAPITAL LETTER ES  # noqa: RUF001
    assert lookalike != "CUSTNO"
    result = _guard([_field("customer_id", lookalike)])
    assert result.entries == {}
    assert result.dropped[0].reason == "not a column in this file"


def test_a_column_that_is_real_in_a_different_known_set_is_still_dropped_here() -> None:
    """Simulates a column that is genuine for a *different* layout version (or a
    different file) but not this one: `guard()` must trust only the `known_columns` it
    was explicitly given, never anything wider."""
    other_version_columns = {"FLD017", "RSRVD1"}  # disjoint from KNOWN_COLUMNS
    result = _guard([_field("qty", "FLD017")], known_columns=KNOWN_COLUMNS)
    assert result.entries == {}
    assert result.dropped[0].source_column == "FLD017"
    # Sanity: the same field against the *other* set is accepted - proving the drop above
    # was really about scoping, not some unrelated typo in the test.
    accepted = _guard([_field("qty", "FLD017")], known_columns=other_version_columns)
    assert "qty" in accepted.entries


def test_a_canonical_field_not_in_the_committed_schema_at_all_is_dropped() -> None:
    assert "shipping_cost" not in CANONICAL_FIELD_NAMES
    result = _guard([_field("shipping_cost", "IVHDR01")])
    assert result.entries == {}


# ----------------------------------------------------------------------------------------
# Oversized value_map
# ----------------------------------------------------------------------------------------


def test_a_value_map_of_exactly_the_cap_is_accepted() -> None:
    from src.features.mapping.proposal_schema import ValuePair

    pairs = [ValuePair(**{"from": f"v{i}", "to": f"c{i}"}) for i in range(200)]
    result = _guard([_field("uom", "UMCODE", transform=_transform("map_values", value_map=pairs))])
    assert "uom" in result.entries
    assert len(result.entries["uom"]["transform"]["table"]) == 200


def test_a_value_map_over_the_cap_drops_only_that_field_not_the_whole_proposal() -> None:
    from src.features.mapping.proposal_schema import ValuePair

    pairs = [ValuePair(**{"from": f"v{i}", "to": f"c{i}"}) for i in range(201)]
    result = _guard(
        [
            _field("uom", "UMCODE", transform=_transform("map_values", value_map=pairs)),
            _field("customer_id", "CUSTNO"),
        ]
    )
    assert "uom" not in result.entries
    assert "customer_id" in result.entries, "one bad field must not lose a good one"
    assert any(
        "unusable transform" in d.reason for d in result.dropped if d.canonical_field == "uom"
    )


def test_a_200_entry_value_map_with_a_duplicate_from_is_still_rejected() -> None:
    """The size cap and the duplicate-key check are independent guards; hitting the size
    boundary must not short-circuit the duplicate check."""
    from src.features.mapping.proposal_schema import ValuePair

    pairs = [ValuePair(**{"from": "DUP", "to": "x"}) for _ in range(2)] + [
        ValuePair(**{"from": f"v{i}", "to": f"c{i}"}) for i in range(198)
    ]
    result = _guard([_field("uom", "UMCODE", transform=_transform("map_values", value_map=pairs))])
    assert "uom" not in result.entries


# ----------------------------------------------------------------------------------------
# Non-finite confidence
# ----------------------------------------------------------------------------------------


def test_nan_confidence_clamps_to_zero_not_left_as_nan() -> None:
    confidence, clamped = _clamp_confidence(Decimal("NaN"))
    assert confidence == Decimal(0)
    assert clamped is True


def test_positive_infinity_confidence_clamps_to_zero_not_one() -> None:
    """An infinite confidence carries no real information either way; `is_finite()` is
    checked before the sign, so +inf does not fall through to the `> 1` branch and does
    not get treated as "maximally confident"."""
    confidence, clamped = _clamp_confidence(Decimal("Infinity"))
    assert confidence == Decimal(0)
    assert clamped is True


def test_negative_infinity_confidence_clamps_to_zero() -> None:
    confidence, clamped = _clamp_confidence(Decimal("-Infinity"))
    assert confidence == Decimal(0)
    assert clamped is True


def test_pydantic_itself_refuses_a_nan_confidence_before_the_guard_ever_sees_it() -> None:
    """A second line of defence the plan does not mention: strict Structured Outputs
    constrains `confidence` to a JSON `number` (no NaN/Infinity token exists in JSON's
    own grammar), and *independently* `ProposedField`'s `Decimal` field rejects a
    non-finite value at construction - `allow_inf_nan` defaults to `False` for
    `Decimal`, unlike `float`. So `_clamp_confidence`'s NaN/Infinity branch (tested
    directly above) is unreachable from a real `output_parsed` result; it is pure
    defence-in-depth, not a path the production pipeline actually exercises - worth
    recording so nobody "simplifies it away" later believing it dead code without
    knowing it is there in case either defence is ever removed."""
    with pytest.raises(ValidationError, match="finite_number"):
        _field("customer_id", "CUSTNO", confidence=Decimal("NaN"))


# ----------------------------------------------------------------------------------------
# Oversized reason and a flood of fields
# ----------------------------------------------------------------------------------------


def test_a_100kb_reason_is_truncated_not_stored_whole() -> None:
    huge = "x" * 100_000
    result = _guard([_field("customer_id", "CUSTNO", reason=huge)])
    stored = result.entries["customer_id"]["reason"]
    assert stored is not None
    assert len(stored) <= 2_000


def test_500_fields_mostly_garbage_does_not_crash_and_only_valid_ones_survive() -> None:
    """A model response this large is implausible (it would truncate per 6.4's own
    criteria long before 500 fields), but the guard must not assume a well-behaved
    input size - it is the thing standing between arbitrary output and the draft."""
    fields = [_field(f"not_a_real_field_{i}", "CUSTNO") for i in range(480)]
    fields += [_field(name, "CUSTNO") for name in CANONICAL_FIELD_NAMES]  # 16 genuine ones
    result = _guard(fields)
    # The guard only dedups a *repeated canonical field name*; it does not (and is not
    # asked to) enforce "one canonical field per source column" - that is a cross-field
    # plausibility question for P5's validators, not this citation check. So all 16
    # distinct, genuine canonical fields survive even though they share one column.
    assert len(result.entries) == 16, "16 distinct canonical fields, each individually valid"
    assert len(result.dropped) == 480
    assert all(d.reason == "not a field in the canonical schema" for d in result.dropped)


def test_every_canonical_field_each_with_its_own_real_column_all_survive() -> None:
    """The full-size happy case: all 16 canonical fields genuinely present, each citing
    a column that exists and only it claims. (Not "500 fields" - the schema only has
    16; the two tests above cover what happens when a response claims far more than
    that many, genuine or not.)"""
    columns = {f"COL{i}" for i in range(16)}
    fields = [_field(name, f"COL{i}") for i, name in enumerate(CANONICAL_FIELD_NAMES)]
    result = _guard(fields, known_columns=columns)
    assert len(result.entries) == 16
    assert result.dropped == []


# ----------------------------------------------------------------------------------------
# Nothing from the model reaches SQL, a file path, or a log as executable content
# ----------------------------------------------------------------------------------------


def test_a_sql_injection_attempt_in_the_column_name_is_just_a_string_that_fails_to_match() -> None:
    payload = "CUSTNO'; DROP TABLE mapping_drafts; --"
    result = _guard([_field("customer_id", payload)])
    assert result.entries == {}
    assert result.dropped[0].source_column == payload  # stored as inert data, never executed


def test_a_path_traversal_attempt_in_the_reason_is_stored_as_inert_text() -> None:
    payload = "../../../etc/passwd"
    result = _guard([_field("customer_id", "CUSTNO", reason=payload)])
    assert result.entries["customer_id"]["reason"] == payload
