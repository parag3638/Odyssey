"""mvp.md 4.4 - layout validation rules. Pure, so these run without a database."""

from __future__ import annotations

import pytest

from src.features.layouts.validate import (
    MAX_FIELDS,
    MAX_NAME_LENGTH,
    ProposedField,
    validate_fields,
)
from src.lib.errors import InvalidLayout

WIDTH = 100


def _field(
    name: str,
    start: int,
    end: int,
    implied_scale: int | None = None,
    quirks: tuple[str, ...] = (),
) -> ProposedField:
    return ProposedField(
        name=name, start=start, end=end, implied_scale=implied_scale, quirks=quirks
    )


def _check(fields: list[ProposedField], *, width: int = WIDTH) -> None:
    validate_fields(fields, record_width=width, footer_row_count=0)


def test_a_sane_layout_passes() -> None:
    _check([_field("A", 1, 10), _field("B", 11, 50), _field("C", 51, 100)])


def test_a_single_character_field_is_legal() -> None:
    """mvp.md 4.4 lists `start >= end` as invalid, but bounds are inclusive and the
    committed layout has a one-character allowance flag at position 170. Rejecting
    start == end would make a real field unrepresentable."""
    _check([_field("FLAG", 1, 1), _field("REST", 2, 100)])


def test_gaps_between_fields_are_allowed() -> None:
    """A human who deletes a filler field leaves those characters unread on purpose."""
    _check([_field("A", 1, 10), _field("C", 51, 100)])


def test_a_field_ending_past_the_record_width_names_the_width() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("A", 1, 10), _field("TOOFAR", 11, 140)])
    fields = caught.value.fields or {}
    assert "1" in fields
    assert "140" in fields["1"]
    assert "100" in fields["1"], "the record width belongs in the message"


def test_start_after_end_is_refused() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("BACKWARDS", 50, 20)])
    fields = caught.value.fields or {}
    assert "0" in fields
    assert "50" in fields["0"] and "20" in fields["0"]


def test_overlapping_fields_name_both_indexes_and_the_characters() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("A", 1, 20), _field("B", 15, 40)])
    fields = caught.value.fields or {}
    assert set(fields) == {"0", "1"}, fields
    for message in fields.values():
        assert "characters 15-20" in message, message
        assert "A" in message and "B" in message


def test_an_overlap_of_exactly_one_character_says_character_not_characters() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("A", 1, 20), _field("B", 20, 40)])
    message = (caught.value.fields or {})["0"]
    assert "character 20" in message and "characters" not in message


def test_a_field_fully_inside_another_is_an_overlap() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("OUTER", 1, 50), _field("INNER", 10, 20)])
    assert set(caught.value.fields or {}) == {"0", "1"}


def test_duplicate_names_name_both_fields() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("AMOUNT", 1, 10), _field("AMOUNT", 11, 20)])
    fields = caught.value.fields or {}
    assert set(fields) == {"0", "1"}
    assert "unique" in fields["0"].lower()


def test_an_empty_field_list_is_refused() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([])
    assert "fields" in (caught.value.fields or {})


def test_more_fields_than_the_cap_is_refused_before_any_per_field_work() -> None:
    too_many = [_field(f"F{index}", 1, 1) for index in range(MAX_FIELDS + 1)]
    with pytest.raises(InvalidLayout) as caught:
        _check(too_many, width=MAX_FIELDS + 10)
    fields = caught.value.fields or {}
    # One message about the count, not 201 messages about overlap.
    assert set(fields) == {"fields"}
    assert str(MAX_FIELDS) in fields["fields"]


def test_an_implied_scale_outside_the_range_names_the_range() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("AMT", 1, 10, implied_scale=99)])
    message = (caught.value.fields or {})["0"]
    assert "0-6" in message


def test_a_negative_implied_scale_is_refused() -> None:
    with pytest.raises(InvalidLayout):
        _check([_field("AMT", 1, 10, implied_scale=-1)])


def test_scale_zero_is_allowed() -> None:
    _check([_field("QTY", 1, 10, implied_scale=0)])


@pytest.mark.parametrize(
    "name",
    [
        "has space",
        "has-dash",
        "9STARTS_WITH_DIGIT",
        "semi;colon",
        "quote'd",
        "../../etc/passwd",
        "drop_table;--",
        "name\nnewline",
    ],
)
def test_a_name_that_is_not_an_identifier_is_refused(name: str) -> None:
    """Field names title table columns and name output columns downstream. They are
    never interpolated into SQL, and this is the second line of that defence."""
    with pytest.raises(InvalidLayout) as caught:
        _check([_field(name, 1, 10)])
    assert "0" in (caught.value.fields or {})


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(InvalidLayout):
        _check([_field("", 1, 10)])


def test_an_over_long_name_is_refused() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("A" * (MAX_NAME_LENGTH + 1), 1, 20)])
    assert str(MAX_NAME_LENGTH) in (caught.value.fields or {})["0"]


def test_an_unknown_quirk_code_is_refused_and_lists_the_known_ones() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("A", 1, 10, quirks=("ebcdic",))])
    message = (caught.value.fields or {})["0"]
    assert "ebcdic" in message
    assert "cyymmdd" in message, "say what is allowed, not just what is wrong"


def test_every_problem_is_reported_at_once_not_one_per_round_trip() -> None:
    with pytest.raises(InvalidLayout) as caught:
        _check(
            [
                _field("bad name", 1, 10),
                _field("B", 11, 30),
                _field("C", 25, 40),
                _field("D", 41, 500),
            ]
        )
    fields = caught.value.fields or {}
    assert set(fields) == {"0", "1", "2", "3"}, fields


def test_a_negative_footer_row_count_is_refused() -> None:
    with pytest.raises(InvalidLayout) as caught:
        validate_fields([_field("A", 1, 10)], record_width=WIDTH, footer_row_count=-1)
    assert "footerRowCount" in (caught.value.fields or {})


def test_the_error_message_is_safe_to_show_a_user() -> None:
    """§3: a message a user reads must carry no SQL, no path, no internal name."""
    with pytest.raises(InvalidLayout) as caught:
        _check([_field("A", 1, 20), _field("B", 15, 40)])
    blob = caught.value.message + " ".join((caught.value.fields or {}).values())
    for leak in ("select", "file_layouts", "jsonb", "/app", "traceback", "psycopg"):
        assert leak not in blob.lower(), leak
