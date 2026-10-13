"""UTC-only time handling, ISO-8601 serialisation, and the AS/400 CYYMMDD codec.

BACKEND.md §3: times are ISO 8601 UTC. §5: every timestamp column is `timestamptz`.
The rule this module enforces is that a naive datetime never enters the system — AS/400
exports carry dates with no zone, and guessing one silently shifts every invoice by up to
a day, which lands rebate lines in the wrong period.

**CYYMMDD** is the IBM i packed-date convention: one century digit, then YY, MM, DD.
`C=0` means 19xx, `C=1` means 20xx. It exists because a 7-digit numeric field was cheaper
than 8, and it is why `0991231` and `1000101` are eight days apart despite looking a
century apart as integers. Decoding it wrong is the single most likely way to silently
mis-date a whole file, so this refuses anything it cannot decode exactly rather than
falling back to a guess.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

#: CYYMMDD is always 7 digits once zero-padded.
_CYYMMDD_WIDTH = 7


def now() -> datetime:
    """The only clock the application reads. Always timezone-aware, always UTC."""
    return datetime.now(UTC)


def today_utc() -> date:
    """Today, in UTC. The day a ledger cap and a checklist date both mean.

    Here rather than in `features/mapping/spend.py`, where it started: P8's checklist is the
    second caller, and two features agreeing on "what day is it" through two functions is how
    a cap and a report start disagreeing across a midnight boundary.
    """
    return now().date()


def to_iso(value: datetime) -> str:
    """Serialise to ISO 8601 with a `Z` suffix, e.g. `2026-10-04T09:30:00Z`.

    Refuses a naive datetime rather than assuming UTC: assuming is how a timezone bug
    becomes invisible.
    """
    if value.tzinfo is None:
        raise ValueError("refusing to serialise a naive datetime; attach a timezone")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def decode_cyymmdd(value: str | int) -> date:
    """Decode an AS/400 CYYMMDD date.

    `1261003` -> 2026-10-03; `0991231` -> 1999-12-31.

    Accepts an int because these arrive from numeric columns where a leading zero is
    already lost — `991231` is zero-padded back to `0991231`. Anything that is not
    exactly a valid date raises, with the offending value in the message; a file whose
    dates cannot be decoded is a mapping problem for a human, not something to paper over
    with a default.
    """
    if isinstance(value, bool):
        raise TypeError(f"not a CYYMMDD date: {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"not a CYYMMDD date: {value!r}")
        text = str(value).zfill(_CYYMMDD_WIDTH)
    elif isinstance(value, str):
        text = value.strip().zfill(_CYYMMDD_WIDTH)
    else:
        raise TypeError(f"CYYMMDD must be a string or int, got {type(value).__name__}")

    if len(text) != _CYYMMDD_WIDTH or not text.isdigit():
        raise ValueError(f"not a CYYMMDD date: {value!r} (expected 7 digits)")

    century, yy, mm, dd = int(text[0]), int(text[1:3]), int(text[3:5]), int(text[5:7])
    year = 1900 + century * 100 + yy
    try:
        return date(year, mm, dd)
    except ValueError as exc:
        # Carries the original value, not just the decoded parts: the operator is looking
        # at a column of 7-digit numbers, not at a year/month/day triple.
        raise ValueError(
            f"not a valid CYYMMDD date: {value!r} (decoded {year}-{mm:02d}-{dd:02d})"
        ) from exc


def encode_cyymmdd(value: date) -> str:
    """Encode a date back to CYYMMDD. Round-trips `decode_cyymmdd`."""
    if not isinstance(value, date):
        raise TypeError(f"expected a date, got {type(value).__name__}")
    if not 1900 <= value.year <= 2899:
        raise ValueError(f"{value.isoformat()} is outside CYYMMDD's range (1900-2899)")
    century, yy = divmod(value.year - 1900, 100)
    return f"{century}{yy:02d}{value.month:02d}{value.day:02d}"
