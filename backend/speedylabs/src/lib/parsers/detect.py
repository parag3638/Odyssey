"""Deciding whether an export is CSV or fixed-width, from its first bytes.

Automatic, because asking a customer's IT team "is this comma-separated?" is exactly the
kind of question this tool exists to stop asking. P4 adds a manual override for the
cases the heuristic gets wrong.

The heuristic rests on the one thing that reliably separates the two formats: a
fixed-width file has records of **identical length**, and a CSV almost never does. Field
counts are the tie-breaker rather than the primary signal, because an AS/400 extract can
legitimately contain commas inside a text field.
"""

from __future__ import annotations

from typing import Final

FORMAT_CSV: Final = "csv"
FORMAT_FIXED_WIDTH: Final = "fixed_width"

#: Enough lines to judge by, few enough to come from one 64 KB chunk.
_SAMPLE_LINES: Final = 20


def sample_lines(head: bytes, limit: int = _SAMPLE_LINES) -> list[str]:
    """Complete lines from the head, dropping any trailing partial one.

    The last line of a chunk is almost always cut mid-record, and judging record-length
    consistency on a truncated line would make every file look like a CSV.
    """
    text = head.decode("ascii", errors="replace")
    lines = text.split("\n")
    if len(lines) > 1:
        lines = lines[:-1]
    return [line.rstrip("\r") for line in lines[:limit] if line.strip()]


def detect_format(head: bytes) -> str:
    """`csv` or `fixed_width`. Never raises - an undecidable file is treated as CSV."""
    lines = sample_lines(head)
    if len(lines) < 2:
        # One line tells us nothing about record-length consistency; fall back to
        # whether it looks delimited at all.
        return FORMAT_CSV if (lines and "," in lines[0]) else FORMAT_FIXED_WIDTH

    lengths = {len(line) for line in lines}
    comma_counts = {line.count(",") for line in lines}

    # A consistent, non-zero delimiter count is the **stronger** signal and is checked
    # first. A CSV always has the same number of delimiters per row; a fixed-width
    # extract has commas only where a text field happens to contain one, so a constant
    # non-zero count across twenty records effectively never happens by accident.
    #
    # Checking record length first - the obvious order - misclassifies a small CSV whose
    # rows happen to be equally long, which is common in narrow exports and was how this
    # was first written.
    constant_commas = len(comma_counts) == 1 and 0 not in comma_counts
    uniform_length = len(lengths) == 1

    # A constant comma count is normally decisive - but not when it is *low* and the
    # records are all the same width. A fixed-width extract with one "LASTNAME, FIRST"
    # name field, or one thousands-separated amount column, carries exactly one comma on
    # every record by construction, and would otherwise be read as a two-column CSV.
    # Identical record lengths across every sampled line is far stronger evidence than a
    # single recurring comma, so it wins that specific tie.
    if constant_commas and uniform_length and max(comma_counts) <= 2:
        # Known cost of this tie-break: a genuine two-column CSV whose every sampled row
        # happens to be exactly the same length also lands here. Real CSVs vary in
        # length within twenty rows because their values do, so this bites artificially
        # uniform files rather than real exports - and P4 gives a human the override.
        return FORMAT_FIXED_WIDTH

    if constant_commas:
        return FORMAT_CSV

    # Identical record lengths with no consistent delimiter is the fixed-width
    # signature. A trailer is shorter, but it is one line in twenty and the sample comes
    # from the head, so it does not appear here.
    if uniform_length:
        return FORMAT_FIXED_WIDTH

    # Mixed: lean on whether commas appear at all. A ragged file with no commas is far
    # more likely a fixed-width extract with an odd trailer than a one-column CSV.
    return FORMAT_CSV if any("," in line for line in lines) else FORMAT_FIXED_WIDTH
