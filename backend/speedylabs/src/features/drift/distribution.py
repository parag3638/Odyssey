"""Distribution comparison: how a column's values are spread, against how they were spread.

**Nothing here can block, and that is the load-bearing decision of the phase.** Measured on the
committed fixtures: `month2_nodrift_invoices.*`, whose layout is byte-identical to month 1's,
carries
month 2's *data* - so it shows `value_mix_shift` on `UMCODE` at 3.59x the threshold and `mean_shift`
on `SHPWGT` at 1.73x, the **same margins as the drifted file**. A distribution check that blocked
would stop every month on ordinary seasonal variation, break A3 alongside A4, and teach users to
click the gate away.

So these findings are warnings that a human reads, and every one carries the baseline number, the
current number and the threshold, because a reader judging "is a 34% heavier average a problem?"
needs
all three and a verdict helps them not at all.

`not_comparable` is the outcome that keeps this honest. A baseline profiled before migration
0019 has
no value counts, which is **not** the same as a column with too many values to count - and
`distinct_count` is what distinguishes them, which is why the cap sits above the enumeration test.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.drift.codes import (
    MEAN_SHIFT,
    NEW_ENUM_VALUE,
    NULL_RATE_SPIKE,
    VALUE_MIX_SHIFT,
    severity_for,
)
from src.features.drift.rows import ColumnStats, ComparisonInput, DriftCheck, DriftFinding
from src.features.drift.thresholds import (
    ENUM_MAX_DISTINCT,
    MEAN_RELATIVE,
    MIX_SHARE_POINTS,
    NULL_RATE_POINTS,
)
from src.lib.profiling.accumulator import VALUE_COUNT_CAP

#: Values named in a mix-shift finding. The biggest movers; a column at the cap could otherwise put
#: 25 entries in one finding's detail.
MAX_NAMED_VALUES = 5

NO_VALUE_COUNTS_REASON = (
    "The approved file was profiled before per-value counts were recorded, so there is nothing to "
    "compare this file's value mix against. Re-profile that file to enable this check."
)


def compare_distribution(data: ComparisonInput) -> list[DriftCheck]:
    """Every distribution check's answer, in vocabulary order."""
    if data.not_comparable_reason is not None:
        return [
            DriftCheck(drift_code=code, outcome="not_comparable", reason=data.not_comparable_reason)
            for code in (NULL_RATE_SPIKE, VALUE_MIX_SHIFT, MEAN_SHIFT, NEW_ENUM_VALUE)
        ]

    pairs = _aligned(data)
    return [
        _null_rate(pairs),
        _value_mix(pairs),
        _mean(pairs),
        _new_enum(pairs),
    ]


def _aligned(data: ComparisonInput) -> list[tuple[ColumnStats, ColumnStats]]:
    """Columns present on both sides, paired by name. A column that only exists on one side is a
    structural finding, not a distributional one."""
    current = {stats.column_name: stats for stats in data.current}
    return [
        (before, current[before.column_name])
        for before in data.baseline
        if before.column_name in current
    ]


def _null_rate(pairs: list[tuple[ColumnStats, ColumnStats]]) -> DriftCheck:
    findings: list[DriftFinding] = []
    for before, after in pairs:
        move = after.null_pct - before.null_pct
        if abs(move) <= NULL_RATE_POINTS:
            continue
        findings.append(
            DriftFinding(
                drift_code=NULL_RATE_SPIKE,
                severity=severity_for(NULL_RATE_SPIKE, cited=False),
                column_name=after.column_name,
                baseline_column_name=before.column_name,
                detail={
                    "baselineNullShare": f"{before.null_pct:f}",
                    "currentNullShare": f"{after.null_pct:f}",
                    "movePoints": f"{move:f}",
                    "threshold": f"{NULL_RATE_POINTS:f}",
                    "direction": "emptier" if move > 0 else "fuller",
                },
            )
        )
    return _check(NULL_RATE_SPIKE, findings, len(pairs))


def _value_mix(pairs: list[tuple[ColumnStats, ColumnStats]]) -> DriftCheck:
    findings: list[DriftFinding] = []
    examined = 0
    missing_counts = 0
    for before, after in pairs:
        if before.value_counts is None:
            # Two different absences, told apart by cardinality. A legitimately capped column is not
            # an enumeration and needs no comparison; a pre-0019 profile cannot be compared at all.
            if before.distinct_count < VALUE_COUNT_CAP:
                missing_counts += 1
            continue
        if after.value_counts is None:
            continue
        examined += 1
        moves: list[tuple[Decimal, str, Decimal, Decimal]] = []
        for value in sorted(set(before.value_counts) | set(after.value_counts)):
            was, now = before.share(value), after.share(value)
            if was is None or now is None:
                continue
            move = now - was
            if abs(move) > MIX_SHARE_POINTS:
                moves.append((abs(move), value, was, now))
        if not moves:
            continue
        moves.sort(reverse=True)
        findings.append(
            DriftFinding(
                drift_code=VALUE_MIX_SHIFT,
                severity=severity_for(VALUE_MIX_SHIFT, cited=False),
                column_name=after.column_name,
                baseline_column_name=before.column_name,
                detail={
                    "threshold": f"{MIX_SHARE_POINTS:f}",
                    "valuesMoved": len(moves),
                    "shifts": [
                        {
                            "value": value,
                            "baselineShare": f"{was:f}",
                            "currentShare": f"{now:f}",
                            "movePoints": f"{now - was:f}",
                        }
                        for _, value, was, now in moves[:MAX_NAMED_VALUES]
                    ],
                },
            )
        )
    if not findings and missing_counts and not examined:
        return DriftCheck(
            drift_code=VALUE_MIX_SHIFT,
            outcome="not_comparable",
            reason=NO_VALUE_COUNTS_REASON,
            columns_examined=0,
        )
    return _check(VALUE_MIX_SHIFT, findings, examined)


def _mean(pairs: list[tuple[ColumnStats, ColumnStats]]) -> DriftCheck:
    findings: list[DriftFinding] = []
    examined = 0
    for before, after in pairs:
        was, now = before.mean, after.mean
        if was is None or now is None or was == 0:
            # A mean of zero has no relative move, and a column with no numeric values has no mean.
            # Both are "nothing to compare" rather than "no change".
            continue
        examined += 1
        relative = (now - was) / abs(was)
        if abs(relative) <= MEAN_RELATIVE:
            continue
        findings.append(
            DriftFinding(
                drift_code=MEAN_SHIFT,
                severity=severity_for(MEAN_SHIFT, cited=False),
                column_name=after.column_name,
                baseline_column_name=before.column_name,
                detail={
                    "baselineMean": f"{was:.4f}",
                    "currentMean": f"{now:.4f}",
                    "relativeMove": f"{relative:.4f}",
                    "threshold": f"{MEAN_RELATIVE:f}",
                    "baselineNumericCount": before.numeric_count,
                    "currentNumericCount": after.numeric_count,
                },
            )
        )
    return _check(MEAN_SHIFT, findings, examined)


def _new_enum(pairs: list[tuple[ColumnStats, ColumnStats]]) -> DriftCheck:
    findings: list[DriftFinding] = []
    examined = 0
    missing_counts = 0
    for before, after in pairs:
        if before.value_counts is None or after.value_counts is None:
            # The same two absences `_value_mix` separates, and this check has to separate them
            # too: a legitimately capped column is not an enumeration and needs no comparison,
            # while a profile written before migration 0019 cannot be compared at all. Reporting
            # the second as `clean` claims a comparison nobody made - the tester caught that this
            # branch was missing here while `_value_mix` had it.
            if before.value_counts is None and before.distinct_count < VALUE_COUNT_CAP:
                missing_counts += 1
            continue
        if before.distinct_count >= ENUM_MAX_DISTINCT:
            # Not a de-facto enumeration, so a value it has not carried before is unremarkable.
            continue
        examined += 1
        new_values = sorted(set(after.value_counts) - set(before.value_counts))
        if not new_values:
            continue
        findings.append(
            DriftFinding(
                drift_code=NEW_ENUM_VALUE,
                severity=severity_for(NEW_ENUM_VALUE, cited=False),
                column_name=after.column_name,
                baseline_column_name=before.column_name,
                detail={
                    "baselineDistinct": before.distinct_count,
                    "enumerationThreshold": ENUM_MAX_DISTINCT,
                    "newValues": new_values[:MAX_NAMED_VALUES],
                    "newValueCount": len(new_values),
                },
            )
        )
    if not findings and missing_counts and not examined:
        return DriftCheck(
            drift_code=NEW_ENUM_VALUE,
            outcome="not_comparable",
            reason=NO_VALUE_COUNTS_REASON,
            columns_examined=0,
        )
    return _check(NEW_ENUM_VALUE, findings, examined)


def _check(code: str, findings: list[DriftFinding], examined: int) -> DriftCheck:
    if findings:
        return DriftCheck(
            drift_code=code, outcome="findings", columns_examined=examined, findings=tuple(findings)
        )
    return DriftCheck(drift_code=code, outcome="clean", columns_examined=examined)
