"""Writers for the two export formats, and the decoders that prove they agree.

The distributor exports the same logical rows two ways, and both carry the ERP's own
column identity — the cryptic names and the non-canonical order from `source_layout`.
Neither writer may emit friendly names: a readable CSV header would make the CSV path a
different and far easier problem than the fixed-width one, and would hide the thing P6
exists to solve.

Where the two formats genuinely differ is how they represent values, which is realistic
and gives P3 and P4 distinct work:

- **Fixed-width** carries the AS/400 conventions: CYYMMDD packed dates, implied decimals
  with no decimal point, text right-padded to the field, and a **trailing sign** on
  negatives. These are the quirks P4 has to detect.
- **CSV** carries the same packed dates but writes money with an explicit decimal point
  and no padding, the way an ERP's CSV extract usually formats numerics.

Both decode back to identical canonical tuples, which is the property that proves they
describe the same file rather than two different ones.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from random import Random
from typing import Final

from src.generator.invoices import InvoiceLine
from src.generator.seed import SEED
from src.generator.source_layout import LAYOUT, SourceField, offsets, record_width
from src.lib.timeutil import decode_cyymmdd, encode_cyymmdd

#: The trailer record's marker, and its field widths. Same total width as a detail
#: record, because "every record is exactly the declared width" has to mean every record.
TRAILER_MARKER: Final = "TRLR"
TRAILER_COUNT_WIDTH: Final = 10
TRAILER_TOTAL_WIDTH: Final = 15


class WriterError(ValueError):
    """A value cannot be represented in its field without corrupting the record."""


def _ascii_only(text: str, field: str) -> str:
    """Refuse non-ASCII rather than emit a mis-aligned record.

    A multi-byte character makes a byte-counted record longer than its declared width,
    which silently shifts every field after it. Raising is the only safe answer: a
    mis-aligned fixed-width file parses into plausible nonsense.
    """
    if not text.isascii():
        offenders = sorted({c for c in text if not c.isascii()})
        raise WriterError(f"{field}: non-ASCII character(s) {offenders!r} in {text!r}")
    return text


def _pad_text(value: str, field: SourceField) -> str:
    """Left-justified, right-padded with spaces. Overflow raises, never truncates."""
    text = _ascii_only(value, field.name)
    if len(text) > field.width:
        raise WriterError(
            f"{field.name}: {len(text)} characters does not fit {field.width} ({text!r})"
        )
    return text.ljust(field.width)


def _implied(value: Decimal, places: int, width: int, field_name: str) -> str:
    """Implied-decimal numeric with a trailing sign.

    `12.50` at two implied places in a 9-wide field becomes `00001250 `. The sign
    occupies the final column: a blank for positive, `-` for negative. That is the
    convention the spec calls out as a trailing sign character, and the reason a naive
    reader that strips spaces turns a credit into a charge.
    """
    scaled = int((value * (10**places)).to_integral_value())
    digits = str(abs(scaled))
    room = width - 1
    if len(digits) > room:
        raise WriterError(f"{field_name}: {value} needs {len(digits)} digits, field has {room}")
    return digits.rjust(room, "0") + ("-" if scaled < 0 else " ")


def _filler(field: SourceField, rng: Random) -> str:
    """Plausible junk, by shape.

    Never an all-blank column: a column that is empty everywhere is trivially ignorable,
    which is not the test we want for mapping. Each filler carries the kind of residue a
    twenty-year-old record layout actually accumulates.
    """
    if field.kind == "filler_num":
        return str(rng.randrange(10**field.width)).rjust(field.width, "0")
    if field.kind == "filler_code":
        return ("RSV" + str(rng.randrange(1000)).rjust(3, "0"))[: field.width].ljust(field.width)
    # filler_blank: mostly blank, with occasional residue, so it is not an empty column.
    if rng.randrange(100) < 15:
        return ("X" + str(rng.randrange(100)).rjust(2, "0"))[: field.width].ljust(field.width)
    return " " * field.width


def _value_for(field: SourceField, line: InvoiceLine, rng: Random) -> str:
    """One field of one detail record, encoded to exactly `field.width` characters."""
    if field.filler:
        return _filler(field, rng)

    if field.canonical is None:
        # The CUSTNO decoy: a real ship-to account, which is why it looks like the
        # customer id a name matcher is hunting for.
        return _pad_text(f"{int(line.customer_id) + 500:06d}", field)

    value = getattr(line, field.canonical)

    if field.kind == "cyymmdd":
        assert isinstance(value, date)
        return encode_cyymmdd(value)
    if field.kind == "flag":
        return "Y" if value else "N"
    if field.kind == "int":
        # A null pack size writes as blanks, not zero: zero is a quantity, blank is an
        # absence, and conflating them would hide the mixed-UOM defect entirely.
        return " " * field.width if value is None else str(int(value)).rjust(field.width, "0")
    if field.kind in ("dec2", "dec4"):
        places = 2 if field.kind == "dec2" else 4
        if value is None:
            return " " * field.width
        assert isinstance(value, Decimal)
        return _implied(value, places, field.width, field.name)
    if field.kind == "packtext":
        # The month-2 type change: what was a zero-padded integer now carries a
        # unit-qualified string. A parser that coerces gets nulls; one that does not gets
        # strings where a number belongs. Either way the numbers come out wrong.
        return " " * field.width if value is None else _pad_text(f"{int(value)}/CS", field)
    return _pad_text("" if value is None else str(value), field)


def detail_record(
    line: InvoiceLine, rng: Random, layout: tuple[SourceField, ...] | None = None
) -> str:
    fields = layout if layout is not None else LAYOUT
    record = "".join(_value_for(field, line, rng) for field in fields)
    expected = record_width(fields)
    if len(record) != expected:
        raise WriterError(f"record is {len(record)} characters, expected {expected}")
    return record


def trailer_record(
    line_count: int, total: Decimal, layout: tuple[SourceField, ...] | None = None
) -> str:
    """The footer the file's own total comes from.

    Deliberately disagrees with the detail sum by the seeded amount - the footer-total
    check exists because real ERP extracts disagree with themselves, usually because the
    trailer was computed before a late adjustment.
    """
    body = (
        TRAILER_MARKER
        + str(line_count).rjust(TRAILER_COUNT_WIDTH, "0")
        + _implied(total, 2, TRAILER_TOTAL_WIDTH, "trailer total")
    )
    return body.ljust(record_width(layout))


def write_fixed_width(
    lines: tuple[InvoiceLine, ...],
    footer_total: Decimal,
    seed: int = SEED,
    layout: tuple[SourceField, ...] | None = None,
) -> str:
    """The whole fixed-width export, trailer included, newline-terminated records."""
    rng = Random(seed + 3)
    out = [detail_record(line, rng, layout) for line in lines]
    out.append(trailer_record(len(lines), footer_total, layout))
    return "\n".join(out) + "\n"


def write_csv(
    lines: tuple[InvoiceLine, ...],
    seed: int = SEED,
    layout: tuple[SourceField, ...] | None = None,
) -> str:
    """The CSV export: same columns, same order, same cryptic names."""
    rng = Random(seed + 4)
    fields = layout if layout is not None else LAYOUT
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([field.name for field in fields])
    for line in lines:
        writer.writerow([_csv_value(field, line, rng) for field in fields])
    return buffer.getvalue()


def _csv_value(field: SourceField, line: InvoiceLine, rng: Random) -> str:
    if field.filler:
        return _filler(field, rng).strip()
    if field.canonical is None:
        return f"{int(line.customer_id) + 500:06d}"

    value = getattr(line, field.canonical)
    if field.kind == "cyymmdd":
        assert isinstance(value, date)
        return encode_cyymmdd(value)
    if field.kind == "flag":
        return "Y" if value else "N"
    if value is None:
        return ""
    if field.kind == "int":
        return str(int(value))
    if field.kind == "packtext":
        return f"{int(value)}/CS"
    if field.kind in ("dec2", "dec4"):
        assert isinstance(value, Decimal)
        places = 2 if field.kind == "dec2" else 4
        return f"{value.quantize(Decimal(1).scaleb(-places)):f}"
    return _ascii_only(str(value), field.name)


# --- decoders, for proving the two formats agree ------------------------------------


def decode_fixed_width(
    record: str, layout: tuple[SourceField, ...] | None = None
) -> dict[str, object]:
    """Decode one detail record using the known layout.

    This is the writer's inverse, not boundary inference - P4 has to infer the offsets
    for itself. This exists so a test can prove the two formats carry the same rows.
    """
    out: dict[str, object] = {}
    for field, start, end in offsets(layout):
        raw = record[start - 1 : end]
        if field.filler or field.canonical is None:
            continue
        out[field.canonical] = _decode_value(field, raw)
    return out


def _decode_value(field: SourceField, raw: str) -> object:
    if field.kind == "cyymmdd":
        return decode_cyymmdd(raw)
    if field.kind == "flag":
        return raw == "Y"
    text = raw.strip()
    if field.kind == "int":
        return None if text == "" else int(text)
    if field.kind in ("dec2", "dec4"):
        if text == "":
            return None
        places = 2 if field.kind == "dec2" else 4
        negative = raw.endswith("-")
        magnitude = Decimal(raw[:-1].strip() or "0").scaleb(-places)
        return -magnitude if negative else magnitude
    # An all-blank text field is an absence, not an empty string. Decoding it as `""`
    # here while the CSV decoder returns `None` would make the two formats disagree on
    # rows that are in fact identical.
    return text if text != "" else None


def decode_csv_row(
    row: dict[str, str], layout: tuple[SourceField, ...] | None = None
) -> dict[str, object]:
    out: dict[str, object] = {}
    for field in layout if layout is not None else LAYOUT:
        if field.filler or field.canonical is None:
            continue
        raw = row[field.name]
        if field.kind == "cyymmdd":
            out[field.canonical] = decode_cyymmdd(raw)
        elif field.kind == "flag":
            out[field.canonical] = raw == "Y"
        elif raw == "":
            out[field.canonical] = None
        elif field.kind == "int":
            out[field.canonical] = int(raw)
        elif field.kind in ("dec2", "dec4"):
            out[field.canonical] = Decimal(raw)
        else:
            out[field.canonical] = raw
    return out
