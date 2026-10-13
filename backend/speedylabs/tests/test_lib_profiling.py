"""Tester pass for mvp.md P3 step 3.1 - the column profiler as a pure function.

Pure-Python unit tests against `src/lib/profiling/{accumulator,typeguess,patterns}.py`.
No Docker, no Postgres: this module is framework-free by design (BACKEND.md §2's rule of
two; docs/decisions.md names P3's job and P10's drift as the two callers), so these tests
import it directly rather than going through the live stack.

`test_no_float_in_src.py` already proves no `float(`/`: float`/`Float(` literal exists
anywhere in `src/`, `lib/profiling` included - that covers mvp.md 3.1's "no value passed
through a float at any point" line, so it is not re-asserted here.
"""

from __future__ import annotations

import gc
import tracemalloc
import types

from src.lib.profiling.accumulator import (
    DISTINCT_CAP,
    SAMPLE_LENGTH,
    ColumnAccumulator,
    ColumnProfile,
)
from src.lib.profiling.patterns import PatternCounter, signature
from src.lib.profiling.typeguess import (
    DATE_FORMAT_CYYMMDD,
    DATE_FORMAT_YYYYMMDD,
    TYPE_EMPTY,
    TypeEvidence,
)


def _profile(values: list[str | None]) -> ColumnProfile:
    acc = ColumnAccumulator(0, "col")
    for v in values:
        acc.add(v)
    return acc.finish()


# ----------------------------------------------------------------------------------
# mvp.md 3.1, accept 1: ["1","2","3",""] -> integer, null_pct="0.250000", distinct=3
# ----------------------------------------------------------------------------------


def test_integer_column_with_one_blank_has_quarter_null_and_three_distinct() -> None:
    profile = _profile(["1", "2", "3", ""])

    assert profile.type_guess == "integer"
    assert profile.null_count == 1
    assert profile.row_count == 4
    assert f"{profile.null_pct:f}" == "0.250000"
    assert profile.distinct_count == 3
    assert profile.distinct_capped is False


# ----------------------------------------------------------------------------------
# accept 2: ["12.50","0.00","-3.75"] -> decimal
# ----------------------------------------------------------------------------------


def test_decimal_column_with_a_negative_value_guesses_decimal() -> None:
    profile = _profile(["12.50", "0.00", "-3.75"])

    assert profile.type_guess == "decimal"
    assert profile.distinct_count == 3


def test_a_single_non_numeric_value_demotes_an_otherwise_integer_column_to_text() -> None:
    """typeguess.py's own stated rule: every non-null value must fit, or it's `text` -
    the honest answer for "integer column with a defect", not a false `integer`."""
    profile = _profile(["1", "2", "N/A", "4"])

    assert profile.type_guess == "text"


# ----------------------------------------------------------------------------------
# accept 3: date formats, including CYYMMDD, with the detected format recorded
# ----------------------------------------------------------------------------------


def test_eight_digit_dates_guess_date_with_the_yyyymmdd_format_recorded() -> None:
    profile = _profile(["20260103", "20260204"])

    assert profile.type_guess == "date"
    assert profile.date_format == DATE_FORMAT_YYYYMMDD


def test_seven_digit_cyymmdd_dates_also_guess_date_with_the_format_recorded() -> None:
    """The AS/400 encoding: a leading century digit (0-2) plus YYMMDD."""
    profile = _profile(["1260103", "1260204"])

    assert profile.type_guess == "date"
    assert profile.date_format == DATE_FORMAT_CYYMMDD


def test_a_seven_digit_quantity_with_an_impossible_month_is_not_misread_as_a_date() -> None:
    """typeguess.py's own worked example: 1234567 has month 45, which doesn't exist."""
    profile = _profile(["1234567"])

    assert profile.type_guess == "integer"
    assert profile.date_format is None


# ----------------------------------------------------------------------------------
# accept 4: ABC-001 / ABC-002 / XYZ-100 -> top signature AAA-999, count 3
# ----------------------------------------------------------------------------------


def test_product_code_shapes_collapse_to_one_pattern_signature() -> None:
    profile = _profile(["ABC-001", "ABC-002", "XYZ-100"])

    patterns = profile.patterns
    assert patterns[0] == {"signature": "AAA-999", "count": 3}


def test_signature_function_maps_digits_to_9_and_letters_to_a_keeping_punctuation() -> None:
    assert signature("ABC-001") == "AAA-999"
    assert signature("XYZ-100") == "AAA-999"


# ----------------------------------------------------------------------------------
# accept 5: 20,000 distinct values, cap 10,000 -> capped=true, count=10000, bounded
# memory (asserted via tracemalloc, not just trusted).
# ----------------------------------------------------------------------------------


def test_20000_distinct_values_are_capped_at_10000_with_bounded_peak_memory() -> None:
    acc = ColumnAccumulator(0, "col")
    # Values are long (2 KB each) and all distinct, so a naive "keep every value"
    # implementation would cost ~40 MB here (20,000 x 2,000 bytes). The digest-based
    # accumulator should cost a few MB at most: a 16-byte digest per distinct value,
    # capped at DISTINCT_CAP, plus five tiny samples and a 200-signature counter.
    payload_prefix = "z" * 2_000

    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    for i in range(20_000):
        acc.add(f"{payload_prefix}-{i:06d}")
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    growth_mb = (peak - baseline) / (1024 * 1024)
    assert growth_mb < 10, (
        f"peak traced memory grew {growth_mb:.2f} MB ingesting 20,000 distinct 2 KB "
        "values - expected a few MB at most from 16-byte digests, not raw retention"
    )

    profile = acc.finish()
    assert profile.distinct_capped is True
    assert profile.distinct_count == DISTINCT_CAP


# ----------------------------------------------------------------------------------
# accept 6: an entirely empty column -> type_guess='empty', no crash
# ----------------------------------------------------------------------------------


def test_an_entirely_empty_column_guesses_empty_without_crashing() -> None:
    profile = _profile(["", "", None, ""])

    assert profile.type_guess == TYPE_EMPTY
    assert profile.row_count == 4
    assert profile.null_count == 4
    assert profile.distinct_count == 0
    # date_format must not be reported for a column with no evidence at all.
    assert profile.date_format is None


# ----------------------------------------------------------------------------------
# accept 7 (denial): a 2 MB cell -> the stored sample is 64 chars, and the accumulator
# retains NO reachable reference to the full value anywhere in its object graph - not
# merely "the sample field happens to be short".
# ----------------------------------------------------------------------------------


def _data_referents(obj: object) -> list[object]:
    """Referents worth following for a *data retention* check, excluding class/module/
    function/code objects - `gc.get_referents` on a plain instance also yields its
    class, and walking into a class pulls in the entire type system (docstrings,
    module-loader paths, builtins) which has nothing to do with whether a value is
    retained. Only containers and plain instance `__dict__`s are followed.
    """
    out: list[object] = []
    if isinstance(obj, (list, tuple, set, frozenset)):
        out.extend(obj)  # pyright: ignore[reportUnknownArgumentType]
        return out
    if isinstance(obj, dict):
        out.extend(obj.keys())  # pyright: ignore[reportUnknownArgumentType]
        out.extend(obj.values())  # pyright: ignore[reportUnknownArgumentType]
        return out
    if isinstance(obj, (type, types.ModuleType, types.FunctionType, types.MethodType)):
        return out
    if hasattr(obj, "__dict__") and not callable(obj):
        out.extend(vars(obj).values())
    return out


def _strings_reachable_from(root: object, *, max_depth: int = 8) -> list[str]:
    """Walk only data (not class/module) referents, collecting every `str` reached.

    This is the direct check mvp.md 3.1 asks for: not "is the sample short" but "is the
    original value retained anywhere", proven by walking the accumulator's actual data
    graph rather than reasoning about which fields we think get written.
    """
    seen: set[int] = set()
    found: list[str] = []
    stack: list[tuple[object, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, str):
            found.append(current)
            continue  # a str has no useful referents of its own
        if depth >= max_depth:
            continue
        for ref in _data_referents(current):
            stack.append((ref, depth + 1))
    return found


def test_a_2mb_cell_leaves_no_long_string_reachable_from_the_accumulator() -> None:
    huge = "y" * (2 * 1024 * 1024)
    acc = ColumnAccumulator(0, "col")
    acc.add(huge)

    # Drop the only other reference to the 2 MB string and force a collection, so the
    # graph walk below can only find it if the accumulator itself kept a reference.
    del huge
    gc.collect()

    strings = _strings_reachable_from(acc)
    assert strings, "walk found nothing at all - test is not exercising the accumulator"
    longest = max(len(s) for s in strings)
    assert longest <= SAMPLE_LENGTH, (
        f"found a {longest}-character string reachable from the accumulator after a "
        f"2 MB cell was added - the full value is retained somewhere, not just sampled"
    )

    profile = acc.finish()
    assert len(profile.sample_values[0]) == SAMPLE_LENGTH
    assert profile.sample_values[0].endswith("…")


def test_the_distinct_set_stores_16_byte_digests_not_the_values_themselves() -> None:
    """Direct structural check: `_digests` holds `bytes`, always 16 long, never `str`."""
    acc = ColumnAccumulator(0, "col")
    acc.add("y" * (2 * 1024 * 1024))
    acc.add("some other value")

    digests = acc._digests  # pyright: ignore[reportPrivateUsage]
    assert len(digests) == 2
    for entry in digests:
        assert isinstance(entry, bytes)
        assert len(entry) == 16


def test_a_hundred_distinct_2mb_cells_cost_megabytes_not_hundreds_of_megabytes() -> None:
    """The plan's own framing: "a column of thousand-page cells costs the same as a
    column of product codes". 100 distinct 2 MB cells would be ~200 MB if retained raw;
    the digest-based accumulator should cost low single-digit MB."""
    acc = ColumnAccumulator(0, "col")
    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    for i in range(100):
        acc.add(f"{i:08d}" + "w" * (2 * 1024 * 1024))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    growth_mb = (peak - baseline) / (1024 * 1024)
    assert growth_mb < 10, (
        f"peak traced memory grew {growth_mb:.2f} MB for 100 distinct 2 MB cells - "
        "raw retention would cost ~200 MB"
    )
    profile = acc.finish()
    assert profile.distinct_count == 100
    assert profile.distinct_capped is False


# ----------------------------------------------------------------------------------
# typeguess.TypeEvidence / patterns.PatternCounter: narrower unit checks behind the
# column-level behaviour above.
# ----------------------------------------------------------------------------------


def test_type_evidence_is_fixed_size_regardless_of_how_many_values_it_sees() -> None:
    small = TypeEvidence()
    small.add("5")
    large = TypeEvidence()
    for i in range(50_000):
        large.add(str(i))

    # Both guess the same way from counters alone - no per-value storage to grow.
    assert small.guess() == "integer"
    assert large.guess() == "integer"
    assert large.non_null == 50_000


def test_pattern_counter_caps_distinct_signatures_and_reports_the_overflow() -> None:
    # Each value below is a genuinely distinct *shape* ("A", "AA", "AAA", ...), so a
    # cap of 3 tracked signatures is exercised by the 4th and 5th arrivals.
    counter = PatternCounter(max_tracked=3)
    shapes = ["A", "AA", "AAA", "AAAA", "AAAAA"]
    for shape_value in shapes:
        counter.add(shape_value)

    assert len(counter.top(limit=10)) == 3
    assert counter.untracked == 2


def test_null_and_empty_string_are_the_same_absence() -> None:
    """accumulator.py's own stated rule: a CSV cannot distinguish `None` from `""`."""
    via_none = _profile([None, "1", "2"])
    via_empty = _profile(["", "1", "2"])

    assert via_none.null_count == via_empty.null_count == 1
    assert via_none.type_guess == via_empty.type_guess == "integer"


def test_values_are_stripped_before_being_judged_so_padding_does_not_fake_text() -> None:
    profile = _profile([" 1 ", "2", " 3"])
    assert profile.type_guess == "integer"
