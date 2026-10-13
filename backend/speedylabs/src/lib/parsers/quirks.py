"""AS/400 field conventions: detect them per field, decode them per value.

A quirk is a *convention the writer used*, not a property of one value, so detection is
a vote over a field's values and decoding is a separate step a caller asks for once the
convention is settled. Detection never mutates data and decoding never guesses: a value
that does not fit the convention it was told to decode raises rather than being coerced
to something nearby, because a date quietly moved by a day and a price quietly moved by
a factor of 100 are the two errors this whole tool exists to prevent.

Why `implied_decimal` is never claimed from the data alone
----------------------------------------------------------
A zero-padded all-digit field of fixed width *is* a packed number - that much is
observable. Whether it is a *scaled decimal*, and at what scale, is not. On the committed
layout the all-digit fields include `LINAMT` (money at scale 2), `SELPRC` (a rate at
scale 4), `CSEMUL` (a plain integer) and `ITMNBR` (an item number that is not a quantity
at all). Nothing in the characters separates them: `100700` is item 100700 or 1007.00
depending entirely on what the field means.

So detection reports a *candidate* - `proposed_scale`, defaulting to `DEFAULT_SCALE`
because money at two decimals is the common case - and claims nothing. A caller that
knows the answer passes `scale_hint`, and only then is `implied_decimal` claimed, with
`scale_source="confirmed"`. Two callers know: the human in the field editor, and P5's
validators, which check a scale against arithmetic the file itself contains
(`ext_amount` close to `qty` times `unit_price`). That is the only real proof a scale is
right.

An earlier version of this module claimed the quirk on any all-digit field at the default
scale. On the committed file that silently proposed turning every item number into a
price with two decimals - precisely the class of error this tool exists to catch, and off
by one decimal place is off by 10x on every rebate dollar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from src.lib.errors import Unparseable

#: Share of non-null values that must fit a convention before it is claimed. Below this
#: the convention is reported as evidence and left unclaimed - a half-matching quirk
#: applied to a whole field corrupts the half that did not match.
CLAIM_THRESHOLD: Final = Decimal("0.95")

#: The scale proposed when a field looks like a packed number and no caller has said
#: what it means. Proposed, never detected. See the module docstring.
DEFAULT_SCALE: Final = 2

#: `impliedScale` is bounded 0-6 by P4's API contract; repeated here because decoding
#: must refuse an out-of-range scale even if a caller skipped that validation.
MIN_SCALE: Final = 0
MAX_SCALE: Final = 6

QUIRK_CYYMMDD: Final = "cyymmdd"
QUIRK_IMPLIED_DECIMAL: Final = "implied_decimal"
QUIRK_RIGHT_PADDED: Final = "right_padded"
QUIRK_TRAILING_SIGN: Final = "trailing_sign"
QUIRK_LEADING_ZEROS: Final = "leading_zeros"

#: The stored vocabulary. P4's one-way doors name this: it goes into `file_layouts.fields`
#: jsonb and is read by mapping transforms and by drift, so a code may be added but never
#: renamed or repurposed.
QUIRK_CODES: Final = (
    QUIRK_CYYMMDD,
    QUIRK_IMPLIED_DECIMAL,
    QUIRK_RIGHT_PADDED,
    QUIRK_TRAILING_SIGN,
    QUIRK_LEADING_ZEROS,
)

_CYYMMDD = re.compile(r"\A[01]\d{6}\Z")
#: Digits with an optional trailing sign. A packed numeric field that carries its sign in
#: the last character still matches, which is what lets `implied_decimal` and
#: `trailing_sign` be claimed on the same field.
_PACKED_NUMERIC = re.compile(r"\A\d+[+-]?\Z")
_SIGNED = re.compile(r"\A\d+[+-]\Z")
_LEADING_ZERO = re.compile(r"\A0\d+\Z")


@dataclass(frozen=True)
class QuirkEvidence:
    """Why a convention was or was not claimed, in numbers a user can check."""

    code: str
    matched: int
    considered: int
    match_fraction: Decimal
    claimed: bool


@dataclass(frozen=True)
class FieldQuirks:
    claimed: tuple[str, ...]
    #: Set only when a caller confirmed the scale via `scale_hint`.
    implied_scale: int | None
    #: What to offer the human when the field looks like a packed number. Never applied.
    proposed_scale: int | None
    #: "confirmed" when a caller supplied the scale, "proposed" when this is a candidate
    #: awaiting one, `None` when the field is not a packed number at all.
    scale_source: str | None
    evidence: tuple[QuirkEvidence, ...]


def _is_null(value: str) -> bool:
    """A fixed-width field holding only padding is absent, not empty-string-valued."""
    return value.strip() == ""


def _fraction(matched: int, considered: int) -> Decimal:
    if considered == 0:
        return Decimal(0)
    return (Decimal(matched) / Decimal(considered)).quantize(Decimal("0.0001"))


def _is_valid_cyymmdd(value: str) -> bool:
    if not _CYYMMDD.fullmatch(value):
        return False
    try:
        decode_cyymmdd(value)
    except Unparseable:
        return False
    return True


def detect_quirks(values: list[str], *, scale_hint: int | None = None) -> FieldQuirks:
    """Classify one field's raw slices. `values` are exactly as sliced - not stripped.

    `scale_hint` is the implied scale a caller has established by means other than
    looking at the characters - a human in the field editor, or P5's arithmetic
    validators. Supplying it is what claims `implied_decimal`; without it the field is
    reported as a candidate only. See the module docstring for why.
    """
    if scale_hint is not None and not MIN_SCALE <= scale_hint <= MAX_SCALE:
        raise Unparseable(
            f"An implied scale of {scale_hint} is outside the allowed range "
            f"{MIN_SCALE}-{MAX_SCALE}."
        )
    present = [value for value in values if not _is_null(value)]
    considered = len(present)
    stripped = [value.strip() for value in present]

    counts: dict[str, int] = {
        QUIRK_CYYMMDD: sum(1 for value in stripped if _is_valid_cyymmdd(value)),
        QUIRK_IMPLIED_DECIMAL: sum(1 for value in stripped if _PACKED_NUMERIC.fullmatch(value)),
        QUIRK_RIGHT_PADDED: sum(
            1 for value in present if value != value.rstrip(" ") and value.strip() != ""
        ),
        # The *form* is what is voted on; see below for the "someone actually used it"
        # requirement that stops every unsigned numeric field claiming this.
        QUIRK_TRAILING_SIGN: sum(1 for value in stripped if _PACKED_NUMERIC.fullmatch(value)),
        QUIRK_LEADING_ZEROS: sum(1 for value in stripped if _LEADING_ZERO.fullmatch(value)),
    }

    claimed: list[str] = []
    evidence: list[QuirkEvidence] = []
    for code in QUIRK_CODES:
        matched = counts[code]
        fraction = _fraction(matched, considered)
        is_claimed = considered > 0 and fraction >= CLAIM_THRESHOLD
        if code == QUIRK_IMPLIED_DECIMAL:
            # The form is observable; the meaning is not. Only a caller who knows the
            # scale can claim this.
            is_claimed = is_claimed and scale_hint is not None
        if code == QUIRK_TRAILING_SIGN:
            # Every value fits "digits with an optional sign" in any unsigned numeric
            # field, so the form alone would claim this everywhere. The convention is
            # only in use if at least one value actually carries the sign - and then it
            # must be applied to the whole field, because the unsigned values are the
            # positive ones.
            is_claimed = is_claimed and any(_SIGNED.fullmatch(value) for value in stripped)
        if is_claimed:
            claimed.append(code)
        evidence.append(
            QuirkEvidence(
                code=code,
                matched=matched,
                considered=considered,
                match_fraction=fraction,
                claimed=is_claimed,
            )
        )

    # A field of dates is all digits, so it matches the packed-numeric form too. The date
    # reading is the specific one and wins; decoding it as a scaled integer would turn
    # 1261003 into 12610.03.
    packed_form = _fraction(counts[QUIRK_IMPLIED_DECIMAL], considered) >= CLAIM_THRESHOLD
    reads_as_date = QUIRK_CYYMMDD in claimed
    if reads_as_date:
        packed_form = False
        if QUIRK_IMPLIED_DECIMAL in claimed:
            claimed.remove(QUIRK_IMPLIED_DECIMAL)
            evidence = [
                QuirkEvidence(
                    code=item.code,
                    matched=item.matched,
                    considered=item.considered,
                    match_fraction=item.match_fraction,
                    claimed=False if item.code == QUIRK_IMPLIED_DECIMAL else item.claimed,
                )
                for item in evidence
            ]

    is_packed_candidate = considered > 0 and packed_form
    implied_decimal_claimed = QUIRK_IMPLIED_DECIMAL in claimed
    scale_source: str | None = None
    if implied_decimal_claimed:
        scale_source = "confirmed"
    elif is_packed_candidate:
        scale_source = "proposed"

    return FieldQuirks(
        claimed=tuple(claimed),
        implied_scale=scale_hint if implied_decimal_claimed else None,
        proposed_scale=DEFAULT_SCALE if is_packed_candidate else None,
        scale_source=scale_source,
        evidence=tuple(evidence),
    )


# --------------------------------------------------------------------------------------
# Decoders. Each takes one raw value and the convention it was told to apply.
# --------------------------------------------------------------------------------------


def decode_cyymmdd(value: str) -> date:
    """`C` + `YYMMDD`, where `C=0` means 19xx and `C=1` means 20xx.

    Raises `Unparseable` naming the value when the digits are not a real date. An
    invalid date is never nudged to a nearby one: 1130229 is somebody's data problem, and
    silently returning 2013-02-28 would hide it through every downstream total.
    """
    text = value.strip()
    if not _CYYMMDD.fullmatch(text):
        raise Unparseable(
            f"{text!r} is not a CYYMMDD date. Expected 7 digits starting with 0 or 1, "
            "where the first digit is the century (0 for 19xx, 1 for 20xx)."
        )
    century = 1900 if text[0] == "0" else 2000
    year = century + int(text[1:3])
    month = int(text[3:5])
    day = int(text[5:7])
    try:
        return date(year, month, day)
    except ValueError as error:
        raise Unparseable(
            f"{text!r} is not a real date: it reads as {year:04d}-{month:02d}-{day:02d}, "
            "which does not exist. Fix it at the source rather than guessing which date "
            "was meant."
        ) from error


def decode_implied_decimal(value: str, *, scale: int) -> Decimal:
    """Read a packed integer as a decimal with `scale` implied digits.

    `0001250` at scale 2 is `Decimal("12.50")`. A trailing `-` or `+` is honoured, so
    `000125-` at scale 2 is `Decimal("-1.25")`.
    """
    if not MIN_SCALE <= scale <= MAX_SCALE:
        raise Unparseable(
            f"An implied scale of {scale} is outside the allowed range {MIN_SCALE}-{MAX_SCALE}."
        )
    text = value.strip()
    if not _PACKED_NUMERIC.fullmatch(text):
        raise Unparseable(
            f"{text!r} is not a packed number. Expected digits, optionally followed by "
            "a single + or - sign."
        )
    sign = -1 if text.endswith("-") else 1
    digits = text[:-1] if text[-1] in "+-" else text
    # Scaling by a power of ten is exact in Decimal, so this is a shift of the point and
    # not a division: no rounding happens and none can.
    return (Decimal(sign) * Decimal(digits)).scaleb(-scale)


def decode_right_padded(value: str) -> str:
    """Strip the padding and nothing else.

    Only the right side, and only spaces: an interior double space is part of the value
    (`"ACME  INC"` is a real company name), and a leading space is data the writer chose
    to put there.
    """
    return value.rstrip(" ")


def decode_trailing_sign(value: str) -> Decimal:
    """A signed packed integer with no implied scale."""
    return decode_implied_decimal(value, scale=0)
