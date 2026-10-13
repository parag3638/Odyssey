"""Attack item 1: the thresholds, both directions, on synthetic numbers the fixture never
produced - so a pass here cannot be explained by a constant quietly tuned to
`expected_drift.json`. Pure unit tests against `distribution.py`/`structural.py`/
`thresholds.py` directly; no DB, no fixture file.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.drift import thresholds as t
from src.features.drift.distribution import compare_distribution
from src.features.drift.rows import ColumnStats, ComparisonInput
from src.features.drift.structural import compare_structure, name_ratio


def _stats(
    name: str,
    *,
    type_guess: str = "text",
    index: int = 0,
    value_counts: dict[str, int] | None = None,
    null_pct: str = "0",
    numeric_count: int | None = None,
    numeric_sum: str | None = None,
    distinct_count: int | None = None,
) -> ColumnStats:
    vc = value_counts
    total = sum(vc.values()) if vc else 0
    return ColumnStats(
        column_index=index,
        column_name=name,
        type_guess=type_guess,
        row_count=total or 1000,
        null_count=0,
        null_pct=Decimal(null_pct),
        distinct_count=distinct_count if distinct_count is not None else (len(vc) if vc else 0),
        distinct_capped=False,
        value_counts=vc,
        numeric_count=numeric_count,
        numeric_sum=None if numeric_sum is None else Decimal(numeric_sum),
    )


# --------------------------------------------------------------------------------------
# NULL_RATE_POINTS = 0.10 - synthetic numbers nowhere near the fixture's 8.20pp
# --------------------------------------------------------------------------------------


def test_null_rate_spike_fires_just_over_the_point_boundary() -> None:
    baseline = (_stats("X", null_pct="0.200000"),)
    current = (_stats("X", null_pct="0.300001"),)  # +10.0001pp
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["null_rate_spike"].outcome == "findings"


def test_null_rate_spike_does_not_fire_just_under_the_point_boundary() -> None:
    baseline = (_stats("X", null_pct="0.200000"),)
    current = (_stats("X", null_pct="0.299999"),)  # +9.9999pp
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["null_rate_spike"].outcome == "clean"


def test_null_rate_spike_fires_on_a_decrease_too() -> None:
    baseline = (_stats("X", null_pct="0.500000"),)
    current = (_stats("X", null_pct="0.389999"),)  # -11.0001pp
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["null_rate_spike"].outcome == "findings"


# --------------------------------------------------------------------------------------
# MIX_SHARE_POINTS = 0.10 - a value's share move, entirely synthetic counts
# --------------------------------------------------------------------------------------


def test_value_mix_shift_fires_just_over_the_share_boundary() -> None:
    # baseline: A=400/1000=0.40, current: A=501/1000=0.501 -> move 0.101
    baseline = (_stats("X", value_counts={"A": 400, "B": 600}),)
    current = (_stats("X", value_counts={"A": 501, "B": 499}),)
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["value_mix_shift"].outcome == "findings"


def test_value_mix_shift_does_not_fire_just_under_the_share_boundary() -> None:
    # baseline A share 0.4000, current A share 0.4999 -> move exactly 0.0999
    baseline = (_stats("X", value_counts={"A": 4000, "B": 6000}),)
    current = (_stats("X", value_counts={"A": 4999, "B": 5001}),)
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["value_mix_shift"].outcome == "clean"


def test_value_mix_shift_is_clean_not_errored_above_the_value_count_cap() -> None:
    """A baseline with >= VALUE_COUNT_CAP distinct values has no `value_counts` recorded (the
    profiler's own cap) - per 10.3's 'distinct_capped, honestly' criterion that must read as a
    deliberate `clean` ("not an enumeration"), not a crash and not a finding."""
    many_values = {f"v{i}": 1 for i in range(30)}
    baseline = (
        ColumnStats(
            column_index=0,
            column_name="X",
            type_guess="text",
            row_count=30,
            null_count=0,
            null_pct=Decimal("0"),
            distinct_count=30,
            distinct_capped=True,
            value_counts=None,
            numeric_count=None,
            numeric_sum=None,
        ),
    )
    current = (_stats("X", value_counts=many_values),)
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["value_mix_shift"].outcome == "clean"


def test_new_enum_value_is_not_comparable_for_a_pre_migration_profile() -> None:
    """BUG (plan 10.3, 'the other null'): "given a baseline profile with distinct_count 6 and
    value_counts null - a profile written before migration 0019 - then BOTH [value_mix_shift and
    new_enum_value] record not_comparable with the reason naming that the baseline was profiled
    before value counts were recorded." `_value_mix` tracks this distinction (`missing_counts`);
    `_new_enum` in distribution.py has no such branch at all - it silently returns 'clean'
    whenever `value_counts` is None, identical to the legitimately-capped case. This test asserts
    the plan's documented requirement and is expected to fail until that branch is added.
    """
    baseline = (
        ColumnStats(
            column_index=0,
            column_name="X",
            type_guess="text",
            row_count=10,
            null_count=0,
            null_pct=Decimal("0"),
            distinct_count=6,
            distinct_capped=False,
            value_counts=None,
            numeric_count=None,
            numeric_sum=None,
        ),
    )
    current = (_stats("X", value_counts={"a": 3, "b": 3, "c": 4}),)
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["value_mix_shift"].outcome == "not_comparable"
    assert checks["new_enum_value"].outcome == "not_comparable"


# --------------------------------------------------------------------------------------
# MEAN_RELATIVE = 0.20 - relative move, synthetic means far from SHPWGT's 57.5/77.36
# --------------------------------------------------------------------------------------


def test_mean_shift_fires_just_over_the_relative_boundary() -> None:
    baseline = (_stats("X", numeric_count=10, numeric_sum="1000"),)  # mean 100
    current = (_stats("X", numeric_count=10, numeric_sum="1201"),)  # mean 120.1 -> +20.1%
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["mean_shift"].outcome == "findings"


def test_mean_shift_does_not_fire_just_under_the_relative_boundary() -> None:
    baseline = (_stats("X", numeric_count=10, numeric_sum="1000"),)  # mean 100
    current = (_stats("X", numeric_count=10, numeric_sum="1199"),)  # mean 119.9 -> +19.9%
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["mean_shift"].outcome == "clean"


def test_mean_shift_handles_a_zero_baseline_mean_as_not_comparable_not_a_crash() -> None:
    baseline = (_stats("X", numeric_count=5, numeric_sum="0"),)
    current = (_stats("X", numeric_count=5, numeric_sum="500"),)
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["mean_shift"].outcome == "clean"  # "nothing to compare", examined=0, no raise


# --------------------------------------------------------------------------------------
# ENUM_MAX_DISTINCT = 20 - the enumeration boundary, synthetic
# --------------------------------------------------------------------------------------


def test_new_enum_value_fires_when_baseline_is_a_de_facto_enumeration() -> None:
    baseline_counts = {f"b{i}": 1 for i in range(19)}  # 19 < 20
    current_counts = {**baseline_counts, "NEWVAL": 1}
    baseline = (_stats("X", value_counts=baseline_counts),)
    current = (_stats("X", value_counts=current_counts),)
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["new_enum_value"].outcome == "findings"


def test_new_enum_value_does_not_fire_once_baseline_crosses_the_enum_boundary() -> None:
    baseline_counts = {f"b{i}": 1 for i in range(20)}  # 20 >= ENUM_MAX_DISTINCT
    current_counts = {**baseline_counts, "NEWVAL": 1}
    baseline = (_stats("X", value_counts=baseline_counts),)
    current = (_stats("X", value_counts=current_counts),)
    checks = {c.drift_code: c for c in compare_distribution(ComparisonInput(baseline, current))}
    assert checks["new_enum_value"].outcome == "clean"


# --------------------------------------------------------------------------------------
# RENAME_NAME_RATIO = 0.60 - synthetic names, not SHPUNT/SHPQTY
# --------------------------------------------------------------------------------------


def test_rename_pairs_just_over_the_ratio_boundary() -> None:
    # Pick two synthetic names whose difflib ratio sits just above 0.60.
    old, new = "WIDGET_CODE", "WIDGETCODEX"
    ratio = name_ratio(old, new)
    assert ratio > t.RENAME_NAME_RATIO, f"fixture names chosen badly: ratio={ratio}"
    baseline = (_stats(old, type_guess="text", index=3),)
    current = (_stats(new, type_guess="text", index=3),)
    checks = {
        c.drift_code: c for c in compare_structure(ComparisonInput(baseline, current, kind="csv"))
    }
    renamed = checks["column_renamed"]
    assert renamed.outcome == "findings"
    assert renamed.findings[0].baseline_column_name == old
    assert renamed.findings[0].column_name == new


def test_rename_does_not_pair_just_under_the_ratio_boundary() -> None:
    old, new = "ABCDEFGH", "ZZZZEFGH"  # half overlap, ratio well under 0.60
    ratio = name_ratio(old, new)
    assert ratio < t.RENAME_NAME_RATIO, f"fixture names chosen badly: ratio={ratio}"
    baseline = (_stats(old, type_guess="text", index=3),)
    current = (_stats(new, type_guess="text", index=3),)
    checks = {
        c.drift_code: c for c in compare_structure(ComparisonInput(baseline, current, kind="csv"))
    }
    assert checks["column_renamed"].outcome == "clean"
    # Reported as an add and a remove instead - the documented safe fallback.
    assert checks["column_removed"].outcome == "findings"
    assert checks["column_added"].outcome == "findings"


def test_rename_requires_equal_type_even_at_a_perfect_name_match() -> None:
    """A perfect name match with a type change must not pair - type is part of the rule, not
    only the name ratio."""
    baseline = (_stats("SAMECOL", type_guess="integer", index=3),)
    current = (_stats("SAMECOL", type_guess="text", index=3),)
    # Same name means this isn't even a remove+add in the normal sense, but construct a
    # genuinely different-named pair at equal index with mismatched types to exercise the rule.
    baseline = (_stats("ALPHA", type_guess="integer", index=3),)
    current = (_stats("ALPHA_X", type_guess="text", index=3),)
    checks = {
        c.drift_code: c for c in compare_structure(ComparisonInput(baseline, current, kind="csv"))
    }
    assert checks["column_renamed"].outcome == "clean"


def test_rename_tie_pairs_nothing() -> None:
    """Two removed columns equally similar to one added column, all at the same index - the
    realistic tie. Exercises `_pair_renames` directly, since `ComparisonInput` cannot itself
    hold two different baseline columns at one current index."""
    from src.features.drift.structural import (
        _pair_renames,  # pyright: ignore[reportPrivateUsage]
    )

    baseline_map = {
        "FOOBAR": _stats("FOOBAR", type_guess="text", index=1),
        "FOOBAZ": _stats("FOOBAZ", type_guess="text", index=1),
    }
    current_map = {"FOOQUX": _stats("FOOQUX", type_guess="text", index=1)}
    ratio_1 = name_ratio("FOOBAR", "FOOQUX")
    ratio_2 = name_ratio("FOOBAZ", "FOOQUX")
    assert ratio_1 == ratio_2, "fixture names must tie for this test to mean anything"
    pairs = _pair_renames(["FOOBAR", "FOOBAZ"], ["FOOQUX"], baseline_map, current_map)
    assert pairs == [], "a tie must pair nothing"
