"""mvp.md 5.2 - the transform library as a closed registry.

The security property under test is negative: there is no input to `parse` that causes
a string to be evaluated, compiled, or looked up as an attribute. Several tests below
assert that by trying.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from src.features.mapping.transforms import (
    DATE_FORMATS,
    MAX_MAP_ENTRIES,
    MAX_MULTIPLY_FACTOR,
    TRANSFORM_KINDS,
    Constant,
    DateFormat,
    ImpliedDecimal,
    MapValues,
    Multiply,
    TrailingSignDecimal,
    apply,
    parse,
    serialise,
)
from src.lib.errors import InvalidTransform, RuleBroken


def _apply(document: object, value: str) -> object:
    return apply(parse(document), value)


# --------------------------------------------------------------------------------------
# The plan's worked examples
# --------------------------------------------------------------------------------------


def test_implied_decimal_reads_a_packed_number() -> None:
    assert _apply({"kind": "implied_decimal", "scale": 2}, "0001250") == Decimal("12.50")


def test_cyymmdd_to_date_reads_the_2000s_century_digit() -> None:
    assert _apply({"kind": "cyymmdd_to_date"}, "1261003") == date(2026, 10, 3)


def test_trailing_sign_decimal_reads_a_negative() -> None:
    assert _apply({"kind": "trailing_sign_decimal", "scale": 2}, "000125-") == Decimal("-1.25")


def test_map_values_translates_a_known_value() -> None:
    document = {"kind": "map_values", "table": {"CASE": "CS", "EACH": "EA"}}
    assert _apply(document, "CASE") == "CS"
    assert _apply(document, "EACH") == "EA"


def test_map_values_raises_naming_the_unmapped_value() -> None:
    document = {"kind": "map_values", "table": {"CASE": "CS", "EACH": "EA"}}
    with pytest.raises(RuleBroken) as caught:
        _apply(document, "DRUM")
    assert "DRUM" in str(caught.value)


# --------------------------------------------------------------------------------------
# Denials: the registry is a lookup, never an evaluator
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "lambda x: os.system(x)",
        "__import__('os').system('id')",
        "eval",
        "exec",
        "compile",
        "os.system",
        "upper()",
        "UPPER",
        "trim ",
        "",
        "../../etc/passwd",
        "none; drop table mapping_drafts",
    ],
)
def test_a_kind_that_is_not_in_the_closed_set_is_refused(kind: str) -> None:
    """Including names that are real Python callables (`eval`, `exec`) and a real
    attribute path (`os.system`): the point is that `kind` is only ever a dict key, so
    what it spells has no bearing on what can happen."""
    with pytest.raises(InvalidTransform) as caught:
        parse({"kind": kind})
    assert "transform" in (caught.value.fields or {})


def test_no_transform_name_resolves_to_a_python_attribute() -> None:
    """A belt-and-braces check on the mechanism rather than the outcome: every accepted
    name must be a literal member of the published tuple, so there is nothing to look
    up dynamically."""
    from src.features.mapping import transforms

    for kind in TRANSFORM_KINDS:
        assert not hasattr(transforms, kind), (
            f"{kind} is also a module attribute; a future refactor could turn a name "
            "into an attribute lookup"
        )


def test_the_published_kinds_are_the_eleven_the_plan_names() -> None:
    """That the published names and the parser registry agree is enforced at import time
    in the module itself, so it cannot be true-in-tests-only; this pins the list."""
    assert TRANSFORM_KINDS == (
        "none",
        "trim",
        "upper",
        "cyymmdd_to_date",
        "date_format",
        "implied_decimal",
        "trailing_sign_decimal",
        "strip_leading_zeros",
        "map_values",
        "constant",
        "multiply",
    )
    assert len(TRANSFORM_KINDS) == 11
    # Every published name parses, which is the half a reader most wants to know.
    for kind in TRANSFORM_KINDS:
        if kind in (
            "date_format",
            "implied_decimal",
            "trailing_sign_decimal",
            "map_values",
            "constant",
            "multiply",
        ):
            continue  # these need parameters, covered by their own tests
        assert parse({"kind": kind}).kind == kind


def test_a_transform_that_is_not_an_object_is_refused() -> None:
    for document in ("trim", 1, None, [], ["trim"], True):
        with pytest.raises(InvalidTransform):
            parse(document)


def test_an_unexpected_parameter_is_refused_by_name() -> None:
    with pytest.raises(InvalidTransform) as caught:
        parse({"kind": "trim", "scale": 2})
    assert "scale" in str(caught.value)


def test_an_implied_scale_outside_the_range_names_the_range() -> None:
    for scale in (-1, 7, 99, 1000):
        with pytest.raises(InvalidTransform) as caught:
            parse({"kind": "implied_decimal", "scale": scale})
        assert "0-6" in str((caught.value.fields or {}).get("transform", ""))


def test_a_scale_that_is_not_an_integer_is_refused() -> None:
    for scale in ("2", 2.5, None, True):
        with pytest.raises(InvalidTransform):
            parse({"kind": "implied_decimal", "scale": scale})


def test_an_unknown_date_format_is_refused_and_lists_the_known_ones() -> None:
    with pytest.raises(InvalidTransform) as caught:
        parse({"kind": "date_format", "fmt": "%Y/%m/%d"})
    message = str((caught.value.fields or {}).get("transform", ""))
    assert "YYYYMMDD" in message, "say what is allowed"


def test_a_strftime_string_cannot_be_smuggled_in_as_a_format() -> None:
    """`fmt` is a key into a closed table, not a format string. If it were passed
    through, `%` directives would be a small language inside the mapping."""
    for fmt in ("%Y%m%d", "%c", "%", "YYYYMMDD; rm -rf /"):
        with pytest.raises(InvalidTransform):
            parse({"kind": "date_format", "fmt": fmt})


# --------------------------------------------------------------------------------------
# Failure: one bad value costs one row
# --------------------------------------------------------------------------------------


def test_a_value_that_is_not_a_date_raises_naming_the_value() -> None:
    with pytest.raises(RuleBroken) as caught:
        _apply({"kind": "cyymmdd_to_date"}, "notadate")
    assert "notadate" in str(caught.value)


def test_a_date_that_does_not_exist_raises_rather_than_being_nudged() -> None:
    with pytest.raises(RuleBroken):
        _apply({"kind": "cyymmdd_to_date"}, "1130229")


def test_a_non_numeric_value_for_implied_decimal_raises_naming_it() -> None:
    with pytest.raises(RuleBroken) as caught:
        _apply({"kind": "implied_decimal", "scale": 2}, "12.5")
    assert "12.5" in str(caught.value)


def test_the_error_is_a_rule_broken_so_a_caller_can_record_a_failed_row() -> None:
    """5.3 and 5.6 both depend on this being catchable per value rather than fatal."""
    transform = parse({"kind": "cyymmdd_to_date"})
    results: list[object] = []
    failures: list[str] = []
    for value in ("1261003", "notadate", "1260115"):
        try:
            results.append(apply(transform, value))
        except RuleBroken as error:
            failures.append(str(error))
    assert results == [date(2026, 10, 3), date(2026, 1, 15)]
    assert len(failures) == 1


# --------------------------------------------------------------------------------------
# Blanks, and the rest of the set
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        {"kind": "cyymmdd_to_date"},
        {"kind": "implied_decimal", "scale": 2},
        {"kind": "trailing_sign_decimal", "scale": 2},
        {"kind": "map_values", "table": {"CASE": "CS"}},
        {"kind": "multiply", "factor": "2"},
        {"kind": "date_format", "fmt": "YYYYMMDD"},
    ],
)
def test_a_blank_value_becomes_none_rather_than_an_error(document: dict[str, object]) -> None:
    """A fixed-width field of padding is absent. Absent is not a parse failure, and
    counting it as one would make every optional column fail validation."""
    assert _apply(document, "   ") is None
    assert _apply(document, "") is None


def test_trim_and_upper_and_none_do_what_they_say() -> None:
    assert _apply({"kind": "none"}, "  ACME  ") == "  ACME  "
    assert _apply({"kind": "trim"}, "  ACME  ") == "ACME"
    assert _apply({"kind": "upper"}, "  acme  ") == "ACME"


def test_strip_leading_zeros_keeps_a_lone_zero() -> None:
    assert _apply({"kind": "strip_leading_zeros"}, "000100700") == "100700"
    assert _apply({"kind": "strip_leading_zeros"}, "00000") == "0", "all zeros is zero"
    assert _apply({"kind": "strip_leading_zeros"}, "  00042 ") == "42"


def test_constant_ignores_the_source_value() -> None:
    assert _apply({"kind": "constant", "value": "USD"}, "anything") == "USD"
    assert _apply({"kind": "constant", "value": "USD"}, "") == "USD"


def test_multiply_scales_exactly_and_refuses_a_float() -> None:
    assert _apply({"kind": "multiply", "factor": "1.5"}, "10") == Decimal("15.0")
    assert _apply({"kind": "multiply", "factor": 6}, "2") == Decimal("12")
    with pytest.raises(InvalidTransform) as caught:
        parse({"kind": "multiply", "factor": 1.5})
    assert "decimal string" in str(caught.value).lower()


def test_multiply_refuses_zero_and_an_absurd_factor() -> None:
    with pytest.raises(InvalidTransform):
        parse({"kind": "multiply", "factor": "0"})
    with pytest.raises(InvalidTransform):
        parse({"kind": "multiply", "factor": str(MAX_MULTIPLY_FACTOR * 10)})
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(InvalidTransform):
            parse({"kind": "multiply", "factor": bad})


# --------------------------------------------------------------------------------------
# map_values: the wire form P6's model must emit
# --------------------------------------------------------------------------------------


def test_map_values_accepts_the_list_of_pairs_wire_form() -> None:
    """P6's strict Structured Outputs cannot emit an object with dynamic keys, so pairs
    are the shape the model must produce."""
    document = {
        "kind": "map_values",
        "table": [{"from": "CASE", "to": "CS"}, {"from": "EACH", "to": "EA"}],
    }
    transform = parse(document)
    assert isinstance(transform, MapValues)
    assert transform.table_dict() == {"CASE": "CS", "EACH": "EA"}
    assert apply(transform, "EACH") == "EA"


def test_map_values_rejects_a_duplicate_from_key() -> None:
    document = {
        "kind": "map_values",
        "table": [{"from": "CASE", "to": "CS"}, {"from": "CASE", "to": "EA"}],
    }
    with pytest.raises(InvalidTransform) as caught:
        parse(document)
    assert "twice" in str(caught.value)


def test_map_values_rejects_a_malformed_entry() -> None:
    malformed: list[object] = [
        [{"from": "CASE"}],
        [{"to": "CS"}],
        [{"from": "CASE", "to": "CS", "surprise": 1}],
        ["CASE"],
        [],
        {},
        "CASE:CS",
        None,
    ]
    for table in malformed:
        with pytest.raises(InvalidTransform):
            parse({"kind": "map_values", "table": table})


def test_map_values_is_bounded() -> None:
    table = [{"from": f"V{index}", "to": "CS"} for index in range(MAX_MAP_ENTRIES + 1)]
    with pytest.raises(InvalidTransform) as caught:
        parse({"kind": "map_values", "table": table})
    assert str(MAX_MAP_ENTRIES) in str(caught.value)


def test_map_values_refuses_an_empty_from_value() -> None:
    with pytest.raises(InvalidTransform):
        parse({"kind": "map_values", "table": [{"from": "", "to": "CS"}]})


# --------------------------------------------------------------------------------------
# Round-tripping, because every transform is stored in a spec
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        {"kind": "none"},
        {"kind": "trim"},
        {"kind": "upper"},
        {"kind": "cyymmdd_to_date"},
        {"kind": "strip_leading_zeros"},
        {"kind": "date_format", "fmt": "YYYY-MM-DD"},
        {"kind": "implied_decimal", "scale": 4},
        {"kind": "trailing_sign_decimal", "scale": 0},
        {"kind": "constant", "value": "USD"},
        {"kind": "multiply", "factor": "1.5"},
        {"kind": "map_values", "table": [{"from": "CASE", "to": "CS"}]},
    ],
)
def test_every_transform_survives_a_serialise_parse_round_trip(
    document: dict[str, object],
) -> None:
    """Every transform is stored in a mapping spec and read back. A kind that cannot
    round-trip is a spec that cannot be reapplied, which is P9's whole feature."""
    once = parse(document)
    twice = parse(serialise(once))
    assert once == twice


def test_serialise_emits_map_values_as_pairs() -> None:
    transform = parse({"kind": "map_values", "table": {"CASE": "CS"}})
    assert serialise(transform) == {
        "kind": "map_values",
        "table": [{"from": "CASE", "to": "CS"}],
    }


def test_serialise_emits_a_multiply_factor_as_a_string() -> None:
    assert serialise(Multiply(factor=Decimal("1.5"))) == {
        "kind": "multiply",
        "factor": "1.5",
    }


def test_parsed_transforms_are_frozen_and_comparable() -> None:
    """Frozen so a parsed transform cannot be mutated after validation, and comparable
    so a draft can be diffed against a stored spec."""
    assert ImpliedDecimal(scale=2) == ImpliedDecimal(scale=2)
    assert ImpliedDecimal(scale=2) != ImpliedDecimal(scale=3)
    assert TrailingSignDecimal(scale=2) != ImpliedDecimal(scale=2)
    assert Constant(value="USD") == Constant(value="USD")
    assert DateFormat(fmt="YYYYMMDD") in {DateFormat(fmt=key) for key in DATE_FORMATS}
    # `frozen=True` raises `FrozenInstanceError`, which is a `dataclasses` exception
    # rather than a `TypeError` - named exactly so this cannot pass for the wrong reason.
    with pytest.raises(FrozenInstanceError):
        ImpliedDecimal(scale=2).scale = 3  # type: ignore[misc]
