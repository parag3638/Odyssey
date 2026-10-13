"""Structural comparison: which columns exist, what they are called, what kind of values they hold.

Exact, not statistical. A column is present or it is not; a type is `decimal` or it is `text`.
That is
why these are the codes that can **block**: the mapping either still reads or it does not.

Two rules here are not obvious and both were decided by measurement:

**A transition to or from `empty` is not a `type_changed`.** `ALWPRC` carries deviated prices
and goes
`decimal -> empty` on **both** month-2 files, because month 2 has no deviated lines - so without
this
rule the no-drift control blocks on a file whose layout is byte-identical to the baseline's. An
empty
column is an absence of values, not a different kind of value, and the honest report for it is its
null rate.

**A rename is paired by three facts, never by name alone.** Equal column index, equal `type_guess`
with neither side empty, and name similarity at or above the threshold. On a tie between two
candidates nothing is paired and both are reported as an add and a remove, because a wrong pairing
tells a user a column was renamed when it was replaced.
"""

from __future__ import annotations

from decimal import Decimal
from difflib import SequenceMatcher

from src.features.drift.codes import (
    COLUMN_ADDED,
    COLUMN_REMOVED,
    COLUMN_RENAMED,
    RECORD_WIDTH_CHANGED,
    TYPE_CHANGED,
    severity_for,
)
from src.features.drift.rows import ColumnStats, ComparisonInput, DriftCheck, DriftFinding
from src.features.drift.thresholds import RENAME_NAME_RATIO

#: A column of nothing. `lib/profiling` writes this when every value is blank.
TYPE_EMPTY = "empty"


def name_ratio(left: str, right: str) -> Decimal:
    """Similarity of two column names, 0 to 1. `difflib`'s ratio, as an exact decimal."""
    return Decimal(str(SequenceMatcher(None, left.upper(), right.upper()).ratio()))


def compare_structure(data: ComparisonInput) -> list[DriftCheck]:
    """Every structural check's answer, in vocabulary order."""
    if data.record_width_change is not None:
        return _width_only(data)

    baseline = {stats.column_name: stats for stats in data.baseline}
    current = {stats.column_name: stats for stats in data.current}
    removed = [name for name in baseline if name not in current]
    added = [name for name in current if name not in baseline]

    renames = _pair_renames(removed, added, baseline, current)
    renamed_from = {old for old, _ in renames}
    renamed_to = {new for _, new in renames}

    checks: list[DriftCheck] = [
        DriftCheck(
            drift_code=RECORD_WIDTH_CHANGED,
            outcome="not_applicable",
            reason=(
                "This is a delimited file, so it has no fixed record width to compare."
                if data.kind == "csv"
                else "The record width is unchanged."
            ),
        )
    ]

    checks.append(
        _finding_check(
            COLUMN_REMOVED,
            [
                DriftFinding(
                    drift_code=COLUMN_REMOVED,
                    severity=severity_for(COLUMN_REMOVED, cited=name in data.cited_columns),
                    column_name=None,
                    baseline_column_name=name,
                    detail={
                        "baselineColumn": name,
                        "baselineType": baseline[name].type_guess,
                        "cited": name in data.cited_columns,
                    },
                )
                for name in removed
                if name not in renamed_from
            ],
            examined=len(baseline),
            clean_reason=None,
        )
    )

    if data.kind == "csv":
        checks.append(
            _finding_check(
                COLUMN_RENAMED,
                [
                    DriftFinding(
                        drift_code=COLUMN_RENAMED,
                        severity=severity_for(COLUMN_RENAMED, cited=old in data.cited_columns),
                        column_name=new,
                        baseline_column_name=old,
                        detail={
                            "baselineColumn": old,
                            "currentColumn": new,
                            "columnIndex": current[new].column_index,
                            "typeGuess": current[new].type_guess,
                            "nameSimilarity": f"{name_ratio(old, new):f}",
                            "threshold": f"{RENAME_NAME_RATIO:f}",
                            "cited": old in data.cited_columns,
                        },
                    )
                    for old, new in renames
                ],
                examined=len(baseline),
                clean_reason=None,
            )
        )
    else:
        checks.append(
            DriftCheck(
                drift_code=COLUMN_RENAMED,
                outcome="not_applicable",
                reason=(
                    "A fixed-width file's field names are positional and come from the record "
                    "layout, not from the file, so a rename is not something this file can show."
                ),
            )
        )

    checks.append(
        _finding_check(
            COLUMN_ADDED,
            [
                DriftFinding(
                    drift_code=COLUMN_ADDED,
                    severity=severity_for(COLUMN_ADDED, cited=False),
                    column_name=name,
                    baseline_column_name=None,
                    detail={
                        "currentColumn": name,
                        "currentType": current[name].type_guess,
                        "columnIndex": current[name].column_index,
                    },
                )
                for name in added
                if name not in renamed_to
            ],
            examined=len(current),
            clean_reason=None,
        )
    )

    checks.append(_type_changes(data, baseline, current, renames))
    return checks


def _width_only(data: ComparisonInput) -> list[DriftCheck]:
    """A fixed-width file whose width changed: one finding, and every per-column check unanswerable.

    Reporting per-column differences here would be worse than reporting none. Measured: forcing a
    column comparison at the spec's boundaries on the drifted fixed-width file produces a
    `mean_shift`
    of 4.33x reading 773 where the value is 7,736 - an artefact of reading the right characters
    in the
    wrong places, presented as a fact about the data.
    """
    # Narrowed by the caller's `is not None`, which pyright does not carry across the call.
    assert data.record_width_change is not None
    before, after = data.record_width_change
    reason = (
        f"Every field boundary is an offset into a {before}-character record and this file's are "
        f"{after}, so no column could be lined up to compare."
    )
    checks = [
        DriftCheck(
            drift_code=RECORD_WIDTH_CHANGED,
            outcome="findings",
            columns_examined=0,
            findings=(
                DriftFinding(
                    drift_code=RECORD_WIDTH_CHANGED,
                    severity=severity_for(RECORD_WIDTH_CHANGED, cited=True),
                    column_name=None,
                    baseline_column_name=None,
                    detail={"baselineRecordWidth": before, "currentRecordWidth": after},
                ),
            ),
        )
    ]
    for code in (COLUMN_REMOVED, COLUMN_RENAMED, COLUMN_ADDED, TYPE_CHANGED):
        checks.append(DriftCheck(drift_code=code, outcome="not_comparable", reason=reason))
    return checks


def _pair_renames(
    removed: list[str],
    added: list[str],
    baseline: dict[str, ColumnStats],
    current: dict[str, ColumnStats],
) -> list[tuple[str, str]]:
    """Removed/added pairs that are one column renamed. See the module docstring for the rule."""
    pairs: list[tuple[str, str]] = []
    for old in removed:
        candidates: list[tuple[Decimal, str]] = []
        for new in added:
            if current[new].column_index != baseline[old].column_index:
                continue
            if current[new].type_guess != baseline[old].type_guess:
                continue
            if TYPE_EMPTY in (current[new].type_guess, baseline[old].type_guess):
                continue
            ratio = name_ratio(old, new)
            if ratio >= RENAME_NAME_RATIO:
                candidates.append((ratio, new))
        if not candidates:
            continue
        best = max(candidates)
        # A tie means two equally good readings, and picking one would tell the user a column was
        # renamed when it may have been replaced. Report both halves instead.
        if sum(1 for ratio, _ in candidates if ratio == best[0]) > 1:
            continue
        pairs.append((old, best[1]))
    return pairs


def _type_changes(
    data: ComparisonInput,
    baseline: dict[str, ColumnStats],
    current: dict[str, ColumnStats],
    renames: list[tuple[str, str]],
) -> DriftCheck:
    rename_map = dict(renames)
    findings: list[DriftFinding] = []
    examined = 0
    for name, before in baseline.items():
        after = current.get(rename_map.get(name, name))
        if after is None:
            continue
        examined += 1
        if before.type_guess == after.type_guess:
            continue
        # The `ALWPRC` rule. See the module docstring.
        if TYPE_EMPTY in (before.type_guess, after.type_guess):
            continue
        findings.append(
            DriftFinding(
                drift_code=TYPE_CHANGED,
                severity=severity_for(TYPE_CHANGED, cited=name in data.cited_columns),
                column_name=after.column_name,
                baseline_column_name=name,
                detail={
                    "baselineType": before.type_guess,
                    "currentType": after.type_guess,
                    "cited": name in data.cited_columns,
                },
            )
        )
    return _finding_check(TYPE_CHANGED, findings, examined=examined, clean_reason=None)


def _finding_check(
    code: str, findings: list[DriftFinding], *, examined: int, clean_reason: str | None
) -> DriftCheck:
    if findings:
        return DriftCheck(
            drift_code=code,
            outcome="findings",
            columns_examined=examined,
            findings=tuple(findings),
        )
    return DriftCheck(
        drift_code=code, outcome="clean", columns_examined=examined, reason=clean_reason
    )
