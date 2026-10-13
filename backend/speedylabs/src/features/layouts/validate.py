"""Validate a human's proposed field layout. Pure - no HTTP, no SQL, no settings.

Every rule reports against the *field index* it belongs to, because the editor renders
one row per field and an error that cannot be placed under its own input is an error the
user has to hunt for. All rules run before anything raises: a layout with three problems
should come back with three messages, not force three round trips.

`start` and `end` are 1-based and inclusive, matching the committed oracle and the
inference output.

Deviation from mvp.md 4.4, which lists `start >= end` among the invalid cases: with
inclusive bounds a single-character field has `start == end`, and the committed layout
contains one (`ALWFLG`, an allowance flag, width 1 at position 170). Rejecting
`start == end` would make a real field unrepresentable, so the rule implemented is
`start > end`. Logged in docs/decisions.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from src.lib.errors import InvalidLayout
from src.lib.parsers.quirks import MAX_SCALE, MIN_SCALE, QUIRK_CODES

#: Matches the `file_layouts_fields_count_check` constraint.
MAX_FIELDS: Final = 200

#: Field names are column identifiers downstream - they title table columns, key mapping
#: proposals and name output columns. Restricted to an identifier charset so they cannot
#: become a path segment, a template injection or a surprise in a generated header. They
#: are never interpolated into SQL (BACKEND.md §4), and this is the second line.
MAX_NAME_LENGTH: Final = 64
_VALID_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class ProposedField:
    name: str
    start: int
    end: int
    implied_scale: int | None = None
    quirks: tuple[str, ...] = ()


def validate_fields(
    proposed: list[ProposedField],
    *,
    record_width: int,
    footer_row_count: int,
) -> None:
    """Raise `InvalidLayout` with per-index messages, or return having found nothing."""
    errors: dict[str, str] = {}

    if not proposed:
        raise InvalidLayout(
            "A layout needs at least one field. Add a field before saving.",
            fields={"fields": "At least one field is required."},
        )
    if len(proposed) > MAX_FIELDS:
        raise InvalidLayout(
            f"That layout has {len(proposed):,} fields, past the limit of {MAX_FIELDS}. "
            "A record layout this wide is usually a sign the boundaries are wrong.",
            fields={"fields": f"At most {MAX_FIELDS} fields are allowed."},
        )
    if footer_row_count < 0:
        errors["footerRowCount"] = "The footer row count cannot be negative."

    for index, field in enumerate(proposed):
        key = str(index)
        if not field.name:
            errors[key] = "A field needs a name."
        elif len(field.name) > MAX_NAME_LENGTH:
            errors[key] = (
                f"{field.name[:MAX_NAME_LENGTH]}... is longer than {MAX_NAME_LENGTH} characters."
            )
        elif not _VALID_NAME.fullmatch(field.name):
            errors[key] = (
                f"{field.name!r} is not a usable field name. Use letters, digits and "
                "underscores, starting with a letter or underscore."
            )
        elif field.start < 1:
            errors[key] = f"{field.name} starts at {field.start}; the first character is 1."
        elif field.start > field.end:
            errors[key] = (
                f"{field.name} starts at {field.start} and ends at {field.end}. "
                "A field must end at or after it starts."
            )
        elif field.end > record_width:
            errors[key] = (
                f"{field.name} ends at character {field.end}, past the end of the "
                f"record, which is {record_width} characters wide."
            )
        elif field.implied_scale is not None and not (
            MIN_SCALE <= field.implied_scale <= MAX_SCALE
        ):
            errors[key] = (
                f"An implied scale of {field.implied_scale} is outside the allowed "
                f"range {MIN_SCALE}-{MAX_SCALE}."
            )
        else:
            unknown = [quirk for quirk in field.quirks if quirk not in QUIRK_CODES]
            if unknown:
                errors[key] = (
                    f"{', '.join(unknown)} is not a quirk this tool knows. "
                    f"Known quirks: {', '.join(QUIRK_CODES)}."
                )

    _collect_duplicate_names(proposed, errors)
    _collect_overlaps(proposed, errors)

    if errors:
        raise InvalidLayout(
            "That layout cannot be used as written. Fix the fields marked below.",
            fields=errors,
        )


def _collect_duplicate_names(proposed: list[ProposedField], errors: dict[str, str]) -> None:
    """Duplicate names are refused because a name is how every later stage refers to a
    field - two `AMOUNT`s make a mapping proposal ambiguous and an output column
    unrepeatable."""
    seen: dict[str, int] = {}
    for index, field in enumerate(proposed):
        first = seen.get(field.name)
        if first is None:
            seen[field.name] = index
            continue
        for key in (str(first), str(index)):
            errors.setdefault(
                key,
                f"{field.name} is used by both field {first + 1} and field {index + 1}. "
                "Field names must be unique.",
            )


def _collect_overlaps(proposed: list[ProposedField], errors: dict[str, str]) -> None:
    """Overlapping ranges are refused, and the overlapping characters are named.

    Gaps are *not* an error: a human who deletes a filler field leaves the characters it
    covered unread, which is a legitimate thing to want. Overlap is different - the same
    characters feeding two fields means at least one of them is wrong, and which one the
    tool cannot know.
    """
    # Sorted by an explicit key: `ProposedField` is a frozen dataclass with no
    # ordering, so sorting tuples that begin with one would compare two fields and
    # raise TypeError the first time two shared a start position.
    ordered = sorted(
        ((index, field) for index, field in enumerate(proposed) if field.start <= field.end),
        key=lambda pair: (pair[1].start, pair[1].end),
    )
    for (earlier_index, earlier), (later_index, later) in pairwise(ordered):
        if later.start > earlier.end:
            continue
        overlap_start = later.start
        overlap_end = min(earlier.end, later.end)
        characters = (
            f"character {overlap_start}"
            if overlap_start == overlap_end
            else f"characters {overlap_start}-{overlap_end}"
        )
        message = (
            f"{earlier.name} ({earlier.start}-{earlier.end}) and {later.name} "
            f"({later.start}-{later.end}) both cover {characters}."
        )
        errors.setdefault(str(earlier_index), message)
        errors.setdefault(str(later_index), message)
