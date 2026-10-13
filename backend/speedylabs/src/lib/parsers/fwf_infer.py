"""Infer fixed-width field boundaries from column alignment.

The method, and why it is this one
----------------------------------
AS/400 exports are space-padded: a text field shorter than its column leaves blanks on
the right, and a numeric field is zero-padded with no blanks at all. So a character
position that is blank in nearly every record is the *tail of one field's padding*, and
the position after it starts the next field. Sample the records, count blanks per
position, and cut at the end of every run of near-always-blank positions.

What this method cannot do, measured rather than assumed
--------------------------------------------------------
Fields in a real layout abut with no gap - the committed oracle has `end 10` followed by
`start 11`. So the only signal is padding *inside* a field, and two fields that are both
full-width in every record have nothing between them to find. Against the committed
500-record sample this returns 14 cuts: every one of them real (no false cut at any
threshold tried), and 7 of the oracle's 21 boundaries invisible. The field list is
therefore a **lower bound on the number of fields**, which is why `InferenceEvidence`
says so in as many words and why the editor lets a human split a field.

Two schemes for proposing the invisible cuts were measured and both rejected:

* Runs of characters constant across every record, cutting at each end: recovered 0 of
  the 7 and proposed 15 boundaries that do not exist.
* Positions blank in 50-99% of records ("weak padding"), cutting before such a run:
  recovered 1 of the 7 and proposed 5 that do not exist.

A field editor that offers five wrong boundaries to earn one right one is worse than one
that offers none, because the human has no way to tell them apart and will trust the
tool. So nothing is guessed: `fields` carries only evidence-backed cuts, and the full
per-position blank histogram goes out in the evidence so the ruler can show the human
exactly where the evidence is and where it runs out.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

from src.lib.errors import Unparseable

#: Records sampled for the histogram. The signal saturates long before this - every
#: boundary in the committed sample is already visible in the first few hundred records -
#: so this is a ceiling on work, not a tuning knob.
SAMPLE_RECORDS: Final = 5_000

#: A position must be blank in at least this share of sampled records to end a field.
#: Deliberately strict: at 0.99 the rule produced zero false cuts on the committed
#: sample, and a false cut is far more damaging than a missed one - it silently splits a
#: real field in two and every downstream value is then wrong.
BLANK_THRESHOLD: Final = Decimal("0.99")

#: Below this many records the histogram is not evidence, it is coincidence: one short
#: value in a 10-record file is 10% of the column. A layout is still returned - a human
#: with a ruler is better served by a draft than by an error - but marked low confidence.
MIN_RECORDS_FOR_CONFIDENCE: Final = 20

#: A field at least this wide with no interior blank evidence at all is where a hidden
#: boundary most often hides, so it is flagged rather than presented as settled. On the
#: committed sample this flags three of the four fields that really are several fields,
#: and flags none of the ten that are genuinely single.
AMBIGUOUS_MIN_WIDTH: Final = 16

#: Any blank evidence inside a field, however weak, is enough to stop flagging it: it
#: means the padding profile has structure a human can read off the ruler.
INTERIOR_EVIDENCE_FLOOR: Final = Decimal("0.10")

#: Matches `src.lib.parsers.fwf_reader`. A trailer is excluded from the histogram: it is
#: one record of a different shape, and at 500 records it would shift every position's
#: blank share by 0.2% - under the threshold's slack today, but not by design.
TRAILER_MARKERS: Final = ("TRLR", "TRAILER", "FOOTER", "END")

#: A fixed-width record wider than this is not an export, and the histogram is one
#: Decimal per position. Bounds the work a hostile file can ask for.
MAX_RECORD_WIDTH: Final = 32_768

#: Matches the layout `fields` cap in P4.4, so inference cannot produce a layout the
#: PATCH route would reject.
MAX_FIELDS: Final = 200

Confidence = Literal["high", "low"]


@dataclass(frozen=True)
class InferredField:
    """One field. `start`/`end` are 1-based and inclusive, matching the oracle."""

    index: int
    name: str
    start: int
    end: int
    confidence: Confidence
    #: Why confidence is low, in a sentence fit to show a user. `None` when high.
    note: str | None = None

    @property
    def width(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class AmbiguousRegion:
    """A field the inference thinks may really be several."""

    field_index: int
    start: int
    end: int
    reason: str


#: Real records carried with the evidence so the editor's ruler can show a human the
#: characters the boundaries were drawn through. Three, because the plan's sliced preview
#: shows three - enough to see that a field is consistent, few enough that the payload
#: stays small and the records shown are the ones the histogram was built from.
SAMPLE_RECORD_COUNT: Final = 3


@dataclass(frozen=True)
class InferenceEvidence:
    """Everything the UI needs to show *why* the boundaries are where they are."""

    records_sampled: int
    record_width: int
    threshold: Decimal
    #: Blank share per character position, index 0 = position 1. Quantized to 4dp.
    blank_fractions: tuple[Decimal, ...]
    ambiguous_regions: tuple[AmbiguousRegion, ...]
    confidence: Confidence
    notes: tuple[str, ...]
    #: Verbatim detail records, padding included. A ruler over stripped values would
    #: show the user different characters from the ones the cuts actually fall between.
    sample_records: tuple[str, ...] = ()


@dataclass(frozen=True)
class InferredLayout:
    record_width: int
    has_trailer: bool
    fields: tuple[InferredField, ...]
    evidence: InferenceEvidence


def _read_records(path: Path, *, sample_records: int) -> list[tuple[int, str]]:
    """Return `(line_number, record)` pairs, blank lines skipped but still counted."""
    records: list[tuple[int, str]] = []
    with path.open("r", encoding="ascii", newline="") as handle:
        for line_number, raw in enumerate(handle, start=1):
            record = raw.rstrip("\n").rstrip("\r")
            if not record.strip():
                continue
            if len(record) > MAX_RECORD_WIDTH:
                raise Unparseable(
                    f"Line {line_number} is {len(record):,} characters long. A "
                    f"fixed-width record wider than {MAX_RECORD_WIDTH:,} characters "
                    "cannot be a record layout - check the file is not one long line."
                )
            records.append((line_number, record))
            # +1 so a trailer cannot displace a sampled detail record. The extra line is
            # dropped again below when it turns out *not* to be a trailer, which is the
            # case on any file longer than the window - otherwise every real-scale file
            # reports one more sampled record than the cap allows.
            if len(records) >= sample_records + 1:
                break
    if not records:
        raise Unparseable("That file has no records in it.")
    return records


def _looks_like_trailer(record: str) -> bool:
    upper = record.upper()
    return any(upper.startswith(marker) for marker in TRAILER_MARKERS)


def _modal_width(records: list[tuple[int, str]]) -> int:
    counts: dict[int, int] = {}
    for _, record in records:
        counts[len(record)] = counts.get(len(record), 0) + 1
    # Widest wins a tie, so a file split evenly between two widths does not depend on
    # dict ordering for its answer.
    return max(counts, key=lambda width: (counts[width], width))


def _blank_fractions(records: list[str], width: int) -> tuple[Decimal, ...]:
    total = Decimal(len(records))
    counts = [0] * width
    for record in records:
        for position, character in enumerate(record):
            if character == " ":
                counts[position] += 1
    return tuple((Decimal(count) / total).quantize(Decimal("0.0001")) for count in counts)


def _cut_positions(blanks: tuple[Decimal, ...], threshold: Decimal) -> list[int]:
    """1-based positions after which a field ends.

    A cut is the *end* of a run of near-always-blank positions, not every blank position:
    inside a 10-character padding run every position is blank, and only the last one is a
    boundary. The record's final position always ends a field.
    """
    width = len(blanks)
    cuts = [
        position + 1
        for position in range(width)
        if blanks[position] >= threshold
        and (position + 1 >= width or blanks[position + 1] < threshold)
    ]
    if width not in cuts:
        cuts.append(width)
    return sorted(cuts)


def _trailing_padding_width(
    blanks: tuple[Decimal, ...], start: int, end: int, threshold: Decimal
) -> int:
    run = 0
    position = end - 1
    while position >= start - 1 and blanks[position] >= threshold:
        run += 1
        position -= 1
    return run


def infer_layout(
    path: Path,
    *,
    sample_records: int = SAMPLE_RECORDS,
    threshold: Decimal = BLANK_THRESHOLD,
) -> InferredLayout:
    """Infer field boundaries for a fixed-width file.

    Raises `Unparseable` naming the first offending line when records are ragged: a
    ragged fixed-width file has no boundaries to find, and guessing from the modal width
    would silently truncate or merge every short record.
    """
    records = _read_records(path, sample_records=sample_records)
    width = _modal_width(records)

    last_line, last_record = records[-1]
    has_trailer = len(records) > 1 and (
        _looks_like_trailer(last_record) or len(last_record) != width
    )
    detail = records[:-1] if has_trailer else records

    if len(detail) > sample_records:
        # The window read one spare line in case the file ended with a trailer. It did
        # not, so that line is just the next detail record and is not part of the sample.
        detail = detail[:sample_records]

    for line_number, record in detail:
        if len(record) != width:
            raise Unparseable(
                f"Line {line_number} is {len(record):,} characters wide but most "
                f"records are {width:,}. A fixed-width file with ragged records has no "
                "reliable field boundaries - re-export it."
            )
    if not detail:
        # A file of one trailer-shaped record. Treat it as the only record there is
        # rather than claiming the file is empty.
        detail = [(last_line, last_record)]
        has_trailer = False

    texts = [record for _, record in detail]
    blanks = _blank_fractions(texts, width)
    cuts = _cut_positions(blanks, threshold)

    if len(cuts) > MAX_FIELDS:
        raise Unparseable(
            f"This file's padding implies {len(cuts):,} fields, past the limit of "
            f"{MAX_FIELDS}. That usually means the records are not fixed-width."
        )

    low_sample = len(texts) < MIN_RECORDS_FOR_CONFIDENCE
    fields: list[InferredField] = []
    ambiguous: list[AmbiguousRegion] = []
    start = 1
    for index, cut in enumerate(cuts):
        padding = _trailing_padding_width(blanks, start, cut, threshold)
        interior = blanks[start - 1 : cut - padding]
        interior_evidence = max(interior) if interior else Decimal(0)
        field_width = cut - start + 1
        wide_and_featureless = (
            field_width >= AMBIGUOUS_MIN_WIDTH and interior_evidence < INTERIOR_EVIDENCE_FLOOR
        )

        note: str | None = None
        confidence: Confidence = "high"
        if wide_and_featureless:
            confidence = "low"
            note = (
                f"{field_width} characters wide with no blank padding anywhere inside "
                "it, so this may be several fields that abut with nothing between them. "
                "Split it if you know the layout."
            )
            ambiguous.append(
                AmbiguousRegion(
                    field_index=index,
                    start=start,
                    end=cut,
                    reason=note,
                )
            )
        elif low_sample:
            confidence = "low"
            note = (
                f"Only {len(texts)} records were available, so this boundary rests on "
                "too few examples to be relied on."
            )

        fields.append(
            InferredField(
                index=index,
                name=f"FIELD_{index + 1:02d}",
                start=start,
                end=cut,
                confidence=confidence,
                note=note,
            )
        )
        start = cut + 1

    notes = [
        "Every boundary shown is backed by a character position blank in at least "
        f"{threshold:%} of sampled records. Boundaries are never guessed, so this is a "
        "lower bound on the number of fields: any field may be several that abut with "
        "no blank between them.",
    ]
    if low_sample:
        notes.append(
            f"Only {len(texts)} records were sampled; at least "
            f"{MIN_RECORDS_FOR_CONFIDENCE} are needed before the padding profile is "
            "evidence rather than coincidence."
        )
    if ambiguous:
        notes.append(
            f"{len(ambiguous)} field(s) are wide with no internal padding and are "
            "flagged as likely to hide a boundary."
        )

    return InferredLayout(
        record_width=width,
        has_trailer=has_trailer,
        fields=tuple(fields),
        evidence=InferenceEvidence(
            records_sampled=len(texts),
            record_width=width,
            threshold=threshold,
            blank_fractions=blanks,
            ambiguous_regions=tuple(ambiguous),
            confidence="low" if (low_sample or ambiguous) else "high",
            notes=tuple(notes),
            sample_records=tuple(texts[:SAMPLE_RECORD_COUNT]),
        ),
    )
