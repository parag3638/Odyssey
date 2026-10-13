"""The drift comparison, as the profile job runs it.

Worker-side on a plain psycopg connection, inside `profile_source_file`'s own transaction - so
no run
ever exists with its profiles written and its drift undecided. The comparisons themselves are pure
(`structural.py`, `distribution.py`); this module finds the baseline, reads two sets of profiles,
stores the answers and says whether to block.

**It fails closed.** A comparison that raises records the raise against its check row and the run
lands `drift_blocked`, not `profiled`. A tool whose job is catching bad data has exactly one safe
failure direction. The single exception is "no baseline", which is not a failure at all - there is
nothing to compare a customer's first file against, and saying so is the honest answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from src.features.drift.codes import DISTRIBUTION_CODES, STRUCTURAL_CODES
from src.features.drift.distribution import compare_distribution
from src.features.drift.rows import ColumnStats, ComparisonInput, DriftCheck
from src.features.drift.structural import compare_structure
from src.lib import logger
from src.lib.ids import uuid7

log = logger.get_logger(__name__)

#: Why there is nothing to compare against. Four ways, each a different sentence for a user.
NO_SPEC = "This customer has no approved mapping spec yet, so there is no approved file to compare."
NO_BASELINE_FILE = (
    "The file this customer's spec was approved from is no longer stored, so there is nothing to "
    "compare this file against."
)
NO_BASELINE_VERSION = (
    "This customer's spec was approved before the layout version was recorded, so the "
    "columns it was approved from cannot be identified. Approve a new spec from a current "
    "file to enable drift "
    "checks."
)
NO_BASELINE_PROFILES = (
    "The approved file has no stored column profiles for the layout its spec was approved from, so "
    "there is nothing to compare this file against."
)

_SELECT_BASELINE = """
    select s.id as spec_id, s.spec, s.approved_from_source_file_id, s.approved_from_layout_version
    from mapping_specs s
    join source_files f on f.customer_id = s.customer_id
    where f.id = %(source_file_id)s and s.kind = %(kind)s and s.status = 'approved'
"""

_SELECT_PROFILES = """
    select column_index, column_name, type_guess, row_count, null_count, null_pct,
           distinct_count, distinct_capped, value_counts, numeric_count, numeric_sum
    from column_profiles
    where source_file_id = %(source_file_id)s and layout_version = %(layout_version)s
    order by column_index
"""

_FILE_EXISTS = "select id from source_files where id = %(id)s"

_INSERT_CHECK = """
    insert into drift_checks (
        id, ingest_run_id, drift_code, outcome, reason, columns_examined
    ) values (%(id)s, %(ingest_run_id)s, %(drift_code)s, %(outcome)s, %(reason)s, %(columns)s)
"""

_INSERT_FINDING = """
    insert into drift_findings (
        id, ingest_run_id, drift_code, severity, column_name, baseline_column_name, detail
    ) values (
        %(id)s, %(ingest_run_id)s, %(drift_code)s, %(severity)s, %(column_name)s,
        %(baseline_column_name)s, %(detail)s
    )
"""


@dataclass
class DriftOutcome:
    """What the profile job needs to know: whether to block, and what to record on the run."""

    compared: bool
    blocking: int
    warnings: int
    baseline_spec_id: UUID | None = None
    baseline_source_file_id: UUID | None = None
    reason: str | None = None


def run_drift(
    connection: psycopg.Connection[Any],
    *,
    run_id: str,
    source_file_id: str,
    layout_version: int,
    kind: str,
    record_width: int | None,
    spec_skip_code: str | None,
    spec_skip_detail: dict[str, Any] | None,
) -> DriftOutcome:
    """Compare this file against the approved baseline and store the answer.

    Does not commit: the caller owns the transaction, which is the whole point - the profiles, the
    drift and the status land together.
    """
    baseline = _baseline(connection, source_file_id, kind)
    if isinstance(baseline, str):
        # Not comparable, and not an error. Nothing is stored: a run with no baseline has no
        # comparison to record, and inventing eight `not_applicable` rows would imply one happened.
        log.info("drift_no_baseline", ingest_run_id=run_id, reason=baseline[:60])
        return DriftOutcome(compared=False, blocking=0, warnings=0, reason=baseline)

    spec_id, spec_document, baseline_file_id, baseline_version = baseline
    baseline_stats = _profiles(connection, str(baseline_file_id), baseline_version)
    if not baseline_stats:
        return DriftOutcome(
            compared=False,
            blocking=0,
            warnings=0,
            baseline_spec_id=spec_id,
            baseline_source_file_id=baseline_file_id,
            reason=NO_BASELINE_PROFILES,
        )

    current_stats = _profiles(connection, source_file_id, layout_version)
    cited = frozenset(
        str(entry["sourceColumn"]) for entry in dict(spec_document.get("mapping") or {}).values()
    )

    # The width change is **promoted** from P9's own fit check, never recomputed: there is one
    # decision about whether this file's records are the right width and it already happened.
    width_change: tuple[int, int] | None = None
    not_comparable: str | None = None
    if spec_skip_code == "record_width_changed" and spec_skip_detail:
        before = int(spec_skip_detail.get("specRecordWidth") or 0)
        after = int(spec_skip_detail.get("fileRecordWidth") or (record_width or 0))
        width_change = (before, after)
        not_comparable = (
            f"Every field boundary is an offset into a {before}-character record and this file's "
            f"are {after}, so no column could be lined up to compare."
        )

    data = ComparisonInput(
        baseline=tuple(baseline_stats),
        current=tuple(current_stats),
        cited_columns=cited,
        kind=kind,
        record_width_change=width_change,
        not_comparable_reason=not_comparable,
    )

    checks = _compare(data)
    _persist(connection, run_id, checks)
    # **An errored comparison blocks.** It carries no findings - there is nothing it managed to
    # find - so counting findings alone let a failed comparison through, which is the exact
    # opposite of failing closed and is what the tester caught. A comparison that could not be made
    # is not a comparison that passed.
    errored = sum(1 for check in checks if check.outcome == "errored")
    blocking = errored + sum(
        1 for check in checks for finding in check.findings if finding.severity == "blocking"
    )
    warnings = sum(
        1 for check in checks for finding in check.findings if finding.severity == "warning"
    )
    log.info(
        "drift_compared",
        ingest_run_id=run_id,
        baseline_source_file_id=str(baseline_file_id),
        blocking=blocking,
        warnings=warnings,
        errored=errored,
    )
    return DriftOutcome(
        compared=True,
        blocking=blocking,
        warnings=warnings,
        baseline_spec_id=spec_id,
        baseline_source_file_id=baseline_file_id,
    )


def _compare(data: ComparisonInput) -> list[DriftCheck]:
    """Both comparisons, with a raising stage recorded as `errored` rather than lost.

    Broad `except` on purpose, and for the opposite reason to P8's: there, one broken rule must not
    hide six good reports; here, a broken rule must not let a file through.

    **Each stage may only mark its own codes.** The first version marked every code not yet seen,
    so a structural raise pre-filled the distribution codes too - and then the loop carried on and
    distribution appended those four again. A duplicate `drift_code` per run, which
    `drift_checks_run_code_key` refuses, aborting the transaction the profile job shares with its
    profile upserts, its draft insert and its status write. The Phase 8-10 tester found it.
    """
    checks: list[DriftCheck] = []
    stages = (
        (compare_structure, "structural", STRUCTURAL_CODES),
        (compare_distribution, "distribution", DISTRIBUTION_CODES),
    )
    for stage, name, owned in stages:
        try:
            checks.extend(stage(data))
        except Exception as error:
            log.error("drift_stage_errored", stage=name, error=str(error))
            message = f"{type(error).__name__}: {error}"
            done = {check.drift_code for check in checks}
            checks.extend(
                DriftCheck(drift_code=code, outcome="errored", reason=message)
                for code in owned
                if code not in done
            )
    return checks


def _baseline(
    connection: psycopg.Connection[Any], source_file_id: str, kind: str
) -> tuple[UUID, dict[str, Any], UUID, int] | str:
    """The spec to compare against, or a sentence saying why there is none."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_SELECT_BASELINE, {"source_file_id": source_file_id, "kind": kind})
        row = cursor.fetchone()
    if row is None:
        return NO_SPEC
    if row["approved_from_source_file_id"] is None:
        return NO_BASELINE_FILE
    if row["approved_from_layout_version"] is None:
        return NO_BASELINE_VERSION
    with connection.cursor() as cursor:
        cursor.execute(_FILE_EXISTS, {"id": row["approved_from_source_file_id"]})
        if cursor.fetchone() is None:
            return NO_BASELINE_FILE
    return (
        UUID(str(row["spec_id"])),
        dict(row["spec"]),
        UUID(str(row["approved_from_source_file_id"])),
        int(row["approved_from_layout_version"]),
    )


def _profiles(
    connection: psycopg.Connection[Any], source_file_id: str, layout_version: int
) -> list[ColumnStats]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            _SELECT_PROFILES,
            {"source_file_id": source_file_id, "layout_version": layout_version},
        )
        rows = cursor.fetchall()
    return [
        ColumnStats(
            column_index=int(row["column_index"]),
            column_name=str(row["column_name"]),
            type_guess=str(row["type_guess"]),
            row_count=int(row["row_count"]),
            null_count=int(row["null_count"]),
            null_pct=Decimal(row["null_pct"]),
            distinct_count=int(row["distinct_count"]),
            distinct_capped=bool(row["distinct_capped"]),
            value_counts=(
                {str(key): int(count) for key, count in dict(row["value_counts"]).items()}
                if row["value_counts"] is not None
                else None
            ),
            numeric_count=(None if row["numeric_count"] is None else int(row["numeric_count"])),
            numeric_sum=(None if row["numeric_sum"] is None else Decimal(row["numeric_sum"])),
        )
        for row in rows
    ]


def _persist(connection: psycopg.Connection[Any], run_id: str, checks: list[DriftCheck]) -> None:
    """Replace this run's drift rows. Delete-then-insert, so a retried job converges."""
    with connection.cursor() as cursor:
        cursor.execute("delete from drift_findings where ingest_run_id = %s", (run_id,))
        cursor.execute("delete from drift_checks where ingest_run_id = %s", (run_id,))
        for check in checks:
            cursor.execute(
                _INSERT_CHECK,
                {
                    "id": uuid7(),
                    "ingest_run_id": run_id,
                    "drift_code": check.drift_code,
                    "outcome": check.outcome,
                    "reason": check.reason,
                    "columns": check.columns_examined,
                },
            )
            for finding in check.findings:
                cursor.execute(
                    _INSERT_FINDING,
                    {
                        "id": uuid7(),
                        "ingest_run_id": run_id,
                        "drift_code": finding.drift_code,
                        "severity": finding.severity,
                        "column_name": finding.column_name,
                        "baseline_column_name": finding.baseline_column_name,
                        "detail": Json(finding.detail),
                    },
                )


def decided_at() -> datetime:
    return datetime.now(UTC)
