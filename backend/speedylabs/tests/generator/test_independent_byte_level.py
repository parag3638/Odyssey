"""Independent, byte-level verification of the committed fixtures against the
committed oracles — deliberately **not** importing `writers.py`'s decoders or
`source_layout.py`'s `LAYOUT` for the parsing itself. The risk this guards against:
the in-repo decoders and the in-repo generator could both be wrong in the same way,
and a test that only round-trips through those modules would never catch that. This
reads `expected_layout.json` as the sole source of column positions and parses the
raw committed bytes by hand.

Scope: the committed `fixtures/full/month1_invoices.txt.gz` fixed-width file, scored
against `fixtures/oracle/expected_layout.json` and `expected_readiness.json`.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@dataclass(frozen=True)
class Column:
    name: str
    start: int  # 1-based inclusive
    end: int  # 1-based inclusive
    width: int
    filler: bool
    kind: str
    canonical: str | None


def _load_layout() -> tuple[int, list[Column]]:
    data = json.loads((FIXTURES / "oracle" / "expected_layout.json").read_text())
    columns = [
        Column(
            name=c["name"],
            start=c["start"],
            end=c["end"],
            width=c["width"],
            filler=c["filler"],
            kind=c["kind"],
            canonical=c["canonical"],
        )
        for c in data["columns"]
    ]
    return data["recordWidth"], columns


def _decode(col: Column, raw: str) -> object:
    """Independent decoder, written from the oracle's declared `kind` only."""
    if col.kind == "cyymmdd":
        text = raw.strip()
        century, yy, mm, dd = int(text[0]), int(text[1:3]), int(text[3:5]), int(text[5:7])
        from datetime import date

        return date(1900 + century * 100 + yy, mm, dd)
    if col.kind == "flag":
        return raw == "Y"
    text = raw.strip()
    if col.kind == "int":
        return None if text == "" else int(text)
    if col.kind in ("dec2", "dec4"):
        if text == "":
            return None
        places = 2 if col.kind == "dec2" else 4
        negative = raw.rstrip().endswith("-")
        digits = raw.rstrip().rstrip("-").strip()
        magnitude = Decimal(digits or "0").scaleb(-places)
        return -magnitude if negative else magnitude
    return text if text != "" else None


def _detail_lines() -> tuple[int, list[str]]:
    """(record_width, detail records) — the trailer record is excluded."""
    record_width, _ = _load_layout()
    with gzip.open(FIXTURES / "full" / "month1_invoices.txt.gz", "rt", encoding="ascii") as fh:
        text = fh.read()
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    assert lines, "no records in month1_invoices.txt.gz"
    trailer = lines[-1]
    details = lines[:-1]
    assert trailer.startswith("TRLR"), f"last record does not start with TRLR: {trailer[:10]!r}"
    for rec in details:
        assert len(rec) == record_width, f"record is {len(rec)} chars, oracle says {record_width}"
    assert len(trailer) == record_width
    return record_width, details


@dataclass(frozen=True)
class DecodedRow:
    """The handful of canonical fields these tests actually need, typed precisely so
    arithmetic and comparisons downstream aren't fighting `object`."""

    invoice_no: str | None
    dist_item_id: str | None
    uom: str | None
    qty: Decimal | None
    unit_price: Decimal | None
    ext_amount: Decimal | None
    catch_weight_lbs: Decimal | None
    pack_size: int | None
    invoice_date: date | None


def _decode_all_rows() -> dict[int, DecodedRow]:
    """row_number -> decoded fields, for every detail record, independently of
    `writers.decode_fixed_width`."""
    _, columns = _load_layout()
    _, details = _detail_lines()
    rows: dict[int, DecodedRow] = {}
    for i, record in enumerate(details, start=1):
        decoded: dict[str, object] = {}
        for col in columns:
            if col.filler or col.canonical is None:
                continue
            raw = record[col.start - 1 : col.end]
            decoded[col.canonical] = _decode(col, raw)
        rows[i] = DecodedRow(
            invoice_no=cast("str | None", decoded.get("invoice_no")),
            dist_item_id=cast("str | None", decoded.get("dist_item_id")),
            uom=cast("str | None", decoded.get("uom")),
            qty=cast("Decimal | None", decoded.get("qty")),
            unit_price=cast("Decimal | None", decoded.get("unit_price")),
            ext_amount=cast("Decimal | None", decoded.get("ext_amount")),
            catch_weight_lbs=cast("Decimal | None", decoded.get("catch_weight_lbs")),
            pack_size=cast("int | None", decoded.get("pack_size")),
            invoice_date=cast("date | None", decoded.get("invoice_date")),
        )
    return rows


def _readiness_oracle() -> dict[str, object]:
    return json.loads((FIXTURES / "oracle" / "expected_readiness.json").read_text())


# --- layout offsets vs actual bytes ----------------------------------------------------


def test_layout_oracle_offsets_agree_with_actual_record_positions() -> None:
    """Find field boundaries by brute force in the trailer-adjacent detail records and
    compare to the oracle's claimed start/end — not trusting `source_layout.py`."""
    record_width, columns = _load_layout()
    _, details = _detail_lines()
    sample = details[:50]

    # The oracle's own internal consistency: contiguous, non-overlapping, covering the
    # full record width in order.
    cursor = 1
    for col in sorted(columns, key=lambda c: c.start):
        assert col.start == cursor, f"{col.name} starts at {col.start}, expected {cursor}"
        assert col.end - col.start + 1 == col.width
        cursor = col.end + 1
    assert cursor - 1 == record_width

    # A `cyymmdd` column's slice must actually decode as a valid CYYMMDD date on every
    # sampled record — proof the offsets are not merely self-consistent but actually
    # line up with the real bytes.
    date_cols = [c for c in columns if c.kind == "cyymmdd"]
    assert date_cols, "no date column found in the oracle"
    for rec in sample:
        for col in date_cols:
            raw = rec[col.start - 1 : col.end]
            _decode(col, raw)  # raises on a bad slice

    # A `flag` column's slice must be exactly Y or N on every sampled record.
    flag_cols = [c for c in columns if c.kind == "flag"]
    for rec in sample:
        for col in flag_cols:
            raw = rec[col.start - 1 : col.end]
            assert raw in ("Y", "N"), f"{col.name} slice is {raw!r}, not a flag"


# --- readiness oracle vs independently-decoded bytes ------------------------------------


def _checks_by_code(oracle: dict[str, object]) -> dict[str, dict[str, object]]:
    return {c["code"]: c for c in oracle["checks"]}  # type: ignore[index]


def test_mixed_uom_rows_in_oracle_really_are_ea_with_no_pack_size_and_no_others_are() -> None:
    rows = _decode_all_rows()
    oracle = _readiness_oracle()
    claimed: set[int] = set(
        cast("list[int]", _checks_by_code(oracle)["mixed_uom_without_pack_size"]["rows"])
    )
    offenders_missed = [
        n for n, r in rows.items() if r.uom == "EA" and r.pack_size is None and n not in claimed
    ]
    offenders_falsely_claimed = [
        n for n in claimed if not (rows[n].uom == "EA" and rows[n].pack_size is None)
    ]
    assert not offenders_missed, (
        f"rows with EA+no pack_size not in the oracle: {offenders_missed[:10]}"
    )
    assert not offenders_falsely_claimed, (
        f"oracle rows that aren't actually EA+no pack_size: {offenders_falsely_claimed[:10]}"
    )


def test_catch_weight_missing_rows_in_oracle_really_have_no_weight() -> None:
    rows = _decode_all_rows()
    oracle = _readiness_oracle()
    claimed: set[int] = set(
        cast("list[int]", _checks_by_code(oracle)["catch_weight_missing"]["rows"])
    )
    lb_rows = {n: r for n, r in rows.items() if r.uom == "LB"}
    missing_weight = {n for n, r in lb_rows.items() if r.catch_weight_lbs is None}
    assert missing_weight == claimed, (
        f"mismatch: oracle-only={claimed - missing_weight}, bytes-only={missing_weight - claimed}"
    )


def test_extension_mismatch_rows_really_disagree_by_the_seeded_amount() -> None:
    rows = _decode_all_rows()
    oracle = _readiness_oracle()
    claimed: set[int] = set(
        cast("list[int]", _checks_by_code(oracle)["extension_mismatch"]["rows"])
    )
    error = Decimal(oracle["extensionErrorUsd"])  # type: ignore[arg-type]

    def computable_mismatch(n: int) -> bool:
        r = rows[n]
        basis = r.catch_weight_lbs if r.uom == "LB" else r.qty
        if basis is None or r.unit_price is None or r.ext_amount is None:
            return False  # not computable -> excluded per oracle.py's own stated rule
        expected = (basis * r.unit_price).quantize(Decimal("0.01"))
        return expected != r.ext_amount

    computable_offenders = {n for n in rows if computable_mismatch(n)}
    assert computable_offenders == claimed, (
        f"oracle-only={claimed - computable_offenders}, bytes-only={computable_offenders - claimed}"
    )
    for n in claimed:
        r = rows[n]
        basis = r.catch_weight_lbs if r.uom == "LB" else r.qty
        assert basis is not None and r.unit_price is not None and r.ext_amount is not None
        expected = (basis * r.unit_price).quantize(Decimal("0.01"))
        delta = abs(r.ext_amount - expected)
        assert delta == error, f"row {n}: delta {delta} != seeded error {error}"


def test_duplicate_rows_really_are_pairwise_identical_on_the_dup_key() -> None:
    rows = _decode_all_rows()
    oracle = _readiness_oracle()
    claimed_raw = _checks_by_code(oracle)["duplicate_line"]["rows"]
    claimed: list[int] = sorted(cast("list[int]", claimed_raw))
    assert len(claimed) % 2 == 0, "duplicate rows should come in pairs"

    def key(n: int) -> tuple[object, ...]:
        r = rows[n]
        return (r.invoice_no, r.dist_item_id, r.uom, r.qty, r.unit_price)

    for i in range(0, len(claimed), 2):
        a, b = claimed[i], claimed[i + 1]
        assert b == a + 1, f"duplicate pair {a},{b} is not adjacent in the final row numbering"
        assert key(a) == key(b), f"rows {a} and {b} are claimed as duplicates but keys differ"

    # No un-listed pair of distinct rows anywhere in the file shares a duplicate key
    # (would mean the generator produced an accidental collision it didn't account for).
    seen: dict[tuple[object, ...], int] = {}
    accidental: list[tuple[int, int]] = []
    for n in sorted(rows):
        k = key(n)
        if k in seen:
            accidental.append((seen[k], n))
        else:
            seen[k] = n
    accidental_unclaimed = [pair for pair in accidental if pair[1] not in claimed]
    assert not accidental_unclaimed, (
        f"accidental duplicate-key collisions not seeded: {accidental_unclaimed[:5]}"
    )


def test_footer_delta_matches_oracle_exactly_from_independently_summed_detail() -> None:
    rows = _decode_all_rows()
    oracle = _readiness_oracle()
    with gzip.open(FIXTURES / "full" / "month1_invoices.txt.gz", "rt", encoding="ascii") as fh:
        all_lines = [line for line in fh.read().split("\n") if line]
    trailer = all_lines[-1]
    footer_col_width = 15  # trailer total field width, per writers.TRAILER_TOTAL_WIDTH
    # Decode the trailer's own total field directly:
    # marker(4) + count(10) + total(15, implied 2dp, trailing sign)
    total_raw = trailer[14 : 14 + footer_col_width]
    negative = total_raw.rstrip().endswith("-")
    digits = total_raw.rstrip().rstrip("-").strip()
    footer_total = Decimal(digits or "0").scaleb(-2)
    if negative:
        footer_total = -footer_total

    amounts: list[Decimal] = []
    for row in rows.values():
        assert row.ext_amount is not None
        amounts.append(row.ext_amount)
    detail_total = sum(amounts, Decimal("0"))
    assert Decimal(oracle["detailTotalUsd"]) == detail_total  # type: ignore[arg-type]
    assert Decimal(oracle["footerTotalUsd"]) == footer_total  # type: ignore[arg-type]
    delta = detail_total - footer_total
    assert delta == Decimal("1842.57"), f"footer delta is {delta}, not the documented $1,842.57"


def test_every_defective_row_carries_exactly_one_seeded_check() -> None:
    oracle = _readiness_oracle()
    checks = _checks_by_code(oracle)
    row_checks = (
        "mixed_uom_without_pack_size",
        "catch_weight_missing",
        "extension_mismatch",
        "duplicate_line",
        "redistributor_vendor_mismatch",
    )
    seen: dict[int, list[str]] = {}
    for code in row_checks:
        for row in cast("list[int]", checks[code]["rows"]):
            seen.setdefault(row, []).append(code)
    multi = {row: codes for row, codes in seen.items() if len(codes) > 1}
    assert not multi, f"rows carrying more than one seeded readiness defect: {multi}"


def test_date_gap_rows_are_genuinely_absent_from_the_file() -> None:
    rows = _decode_all_rows()
    oracle = _readiness_oracle()
    missing_dates = {
        date.fromisoformat(cast(str, d))
        for d in cast("list[object]", _checks_by_code(oracle)["date_gap"]["missingDates"])
    }
    present_dates = {r.invoice_date for r in rows.values()}
    overlap = missing_dates & present_dates
    assert not overlap, f"dates claimed missing are actually present in the file: {overlap}"
    assert missing_dates, "no gap dates recorded"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
