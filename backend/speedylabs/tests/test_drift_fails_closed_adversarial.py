"""Attack: does a raising drift stage actually block, as `features/drift/run.py`'s module
docstring and the Phase 10 plan (10.4) both claim ("fails closed ... the run lands
`drift_blocked`, and materialising is refused")?

`run_drift`'s `_compare` helper catches a raising stage and records the *remaining* codes as
`errored` - but it never adds a `drift_findings` row for that, and the materialise gate
(`canonical/repo.drift_gate`) counts only `drift_findings` rows with `severity = 'blocking'`.
An `errored` `drift_checks` row with zero findings therefore does not block anything through
that path. Two scenarios are checked directly against `_compare` (pure, no DB) and then against
the real Postgres `run_drift` end to end, because a unit-level finding is not evidence about
what actually lands in the database.

Deliberately not mocking the DB: BACKEND.md and the tester's own rules want real Postgres for
anything that touches a constraint, and the second bug this file finds (`drift_checks`'
`unique (ingest_run_id, drift_code)` violated when the raising stage is *first*) can only be
seen against a real connection.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from psycopg.types.json import Json

from src.features.drift import run as run_mod
from src.features.drift.codes import STRUCTURAL_CODES
from src.features.drift.rows import ColumnStats, ComparisonInput
from tests.live import APP_DB_URL


def _skip_without_live_db() -> None:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=0.5):
            return
    except OSError:
        pytest.skip("no Postgres listening on 127.0.0.1:5432 - run `docker compose up -d db`")


def _trivial_input() -> ComparisonInput:
    """Identical baseline and current, one text column - deliberately boring, so any finding at
    all has to come from the bug under test, not from real drift."""
    stats = (
        ColumnStats(
            column_index=0,
            column_name="A",
            type_guess="text",
            row_count=10,
            null_count=0,
            null_pct=Decimal("0"),
            distinct_count=2,
            distinct_capped=False,
            value_counts={"x": 5, "y": 5},
            numeric_count=None,
            numeric_sum=None,
        ),
    )
    return ComparisonInput(baseline=stats, current=stats)


# --------------------------------------------------------------------------------------
# Pure, no DB: what `_compare` itself returns when a stage raises.
# --------------------------------------------------------------------------------------


def test_a_raising_distribution_stage_records_four_errored_checks_and_no_findings() -> None:
    """The common ordering: structure is clean, then the distribution stage blows up.

    An `errored` check legitimately carries **no findings** - there is nothing it managed to find -
    so this asserts that shape, and the two tests below assert the thing that actually matters:
    that the *outcome* blocks anyway. Counting findings alone was the original defect.
    """
    data = _trivial_input()
    with patch("src.features.drift.run.compare_distribution", side_effect=RuntimeError("boom")):
        checks = run_mod._compare(data)  # pyright: ignore[reportPrivateUsage]

    errored = [c for c in checks if c.outcome == "errored"]
    assert len(errored) == 4, "all four distribution codes should be recorded errored"
    assert all(check.reason for check in errored), "an errored check must say what went wrong"
    blocking_findings = sum(1 for c in checks for f in c.findings if f.severity == "blocking")
    assert blocking_findings == 0, "an errored check carries no findings - it found nothing"


def test_a_raising_structural_stage_records_each_code_exactly_once() -> None:
    """When the *first* stage raises, each stage may only mark the codes **it owns**.

    The original version marked every not-yet-seen code - structural and distribution alike - so
    the loop then ran distribution for real and appended its four codes a second time. A duplicate
    `drift_code` per comparison, which `drift_checks`' `unique (ingest_run_id, drift_code)` cannot
    hold, aborting the transaction the profile job shares with its own writes. `STRUCTURAL_CODES`
    and `DISTRIBUTION_CODES` now partition the vocabulary, with an import-time check that they do.
    """
    data = _trivial_input()
    with patch("src.features.drift.run.compare_structure", side_effect=RuntimeError("boom")):
        checks = run_mod._compare(data)  # pyright: ignore[reportPrivateUsage]

    codes = [c.drift_code for c in checks]
    dupes = {code: n for code, n in Counter(codes).items() if n > 1}
    assert not dupes, f"each drift code must be recorded once per comparison; got {dupes}"
    errored = {c.drift_code for c in checks if c.outcome == "errored"}
    assert errored == set(STRUCTURAL_CODES), (
        "only the stage that raised may be recorded errored - the other stage ran and has real "
        "outcomes to report"
    )


# --------------------------------------------------------------------------------------
# Real Postgres: does this actually reach the database the way the gate reads it?
# --------------------------------------------------------------------------------------


@pytest.fixture
def live_conn() -> Iterator[psycopg.Connection[Any]]:
    _skip_without_live_db()
    conn = psycopg.connect(APP_DB_URL, autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _seed_minimal_run(conn: psycopg.Connection[Any]) -> tuple[str, str, str, str]:
    """A customer, a baseline file + spec approved from it, and a 'current' file + run - the
    minimum `run_drift` needs. Returns (run_id, current_file_id, baseline_file_id, customer_id).
    """
    customer_id = str(uuid.uuid4())
    baseline_file_id = str(uuid.uuid4())
    current_file_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    spec_id = str(uuid.uuid4())
    ip_hash = hashlib.sha256(secrets.token_bytes(8)).hexdigest()
    sha = hashlib.sha256(secrets.token_bytes(8)).hexdigest()

    with conn.cursor() as cur:
        cur.execute(
            "insert into customers (id, name, slug) values (%s, %s, %s)",
            (customer_id, "Adversarial Drift Co", f"adversarial-drift-{customer_id[:8]}"),
        )
        for file_id, name in ((baseline_file_id, "month1.csv"), (current_file_id, "month2.csv")):
            cur.execute(
                """
                insert into source_files
                  (id, customer_id, kind, original_filename, stored_name, byte_size, sha256,
                   sniffed_type, format, row_count, uploader_ip_hash)
                values (%s, %s, 'erp_invoice_lines', %s, %s, 100, %s, 'text/csv', 'csv', 10, %s)
                """,
                (file_id, customer_id, name, f"{file_id}.csv", sha, ip_hash),
            )
        cur.execute(
            """
            insert into ingest_runs (id, source_file_id, customer_id, status)
            values (%s, %s, %s, 'profiled')
            """,
            (run_id, current_file_id, customer_id),
        )
        cur.execute(
            """
            insert into mapping_specs
              (id, customer_id, kind, version, status, canonical_schema_version, spec,
               spec_sha256, approved_from_source_file_id, approved_from_ip_hash, approved_at,
               approved_from_layout_version)
            values (%s, %s, 'csv', 1, 'approved', '1.0.0', %s, %s, %s, %s, now(), 1)
            """,
            (
                spec_id,
                customer_id,
                Json({"mapping": {}}),
                sha,
                baseline_file_id,
                ip_hash,
            ),
        )
        for file_id in (baseline_file_id, current_file_id):
            cur.execute(
                """
                insert into column_profiles
                  (id, source_file_id, layout_version, column_index, column_name, type_guess,
                   row_count, null_count, null_pct, distinct_count, distinct_capped,
                   sample_values, patterns, value_counts)
                values (%s, %s, 1, 0, 'A', 'text', 10, 0, 0, 2, false, %s, %s, %s)
                """,
                (str(uuid.uuid4()), file_id, Json(["x", "y"]), Json([]), Json({"x": 5, "y": 5})),
            )
    return run_id, current_file_id, baseline_file_id, customer_id


def test_a_raising_distribution_stage_leaves_the_run_unblocked_in_the_real_database(
    live_conn: psycopg.Connection[Any],
) -> None:
    """End to end against real Postgres: the comparison itself is boring (identical columns on
    both sides), but `compare_distribution` is forced to raise, exactly as the plan's own
    criterion does ('`compare_structure` patched to raise ... the run lands `drift_blocked`, and
    materialising is refused'). Here it is the sibling stage, because that pairing is the one
    that cannot hit the duplicate-key path and so isolates the fails-closed claim on its own.
    """
    run_id, current_file_id, _baseline_file_id, _customer_id = _seed_minimal_run(live_conn)

    with patch("src.features.drift.run.compare_distribution", side_effect=RuntimeError("boom")):
        outcome = run_mod.run_drift(
            live_conn,
            run_id=run_id,
            source_file_id=current_file_id,
            layout_version=1,
            kind="csv",
            record_width=None,
            spec_skip_code=None,
            spec_skip_detail=None,
        )

    assert outcome.compared is True
    assert outcome.blocking == 4, (
        "fails closed: a comparison that could not be made must block, and the four errored "
        "distribution checks are what `run_drift` counts. Counting only `drift_findings` rows let "
        "a raising stage sail through to `mapped` - the exact failure direction P10 forbids."
    )

    with live_conn.cursor() as cur:
        cur.execute(
            "select drift_code, outcome from drift_checks "
            "where ingest_run_id = %s order by drift_code",
            (run_id,),
        )
        rows = cur.fetchall()
        errored_codes = {code for code, outcome_ in rows if outcome_ == "errored"}
        assert errored_codes == {
            "null_rate_spike",
            "value_mix_shift",
            "mean_shift",
            "new_enum_value",
        }

        cur.execute(
            "select count(*) from drift_findings "
            "where ingest_run_id = %s and severity = 'blocking'",
            (run_id,),
        )
        count_row = cur.fetchone()
        assert count_row is not None
        assert count_row[0] == 0, "an errored check produces no findings; the gate counts it anyway"

        # The gate's own query, which has to agree with `run_drift` or the status and the refusal
        # would disagree about the same run.
        cur.execute(
            "select (select count(*) from drift_findings "
            "         where ingest_run_id = %(run)s and severity = 'blocking') "
            "     + (select count(*) from drift_checks "
            "         where ingest_run_id = %(run)s and outcome = 'errored')",
            {"run": run_id},
        )
        gate_row = cur.fetchone()
        assert gate_row is not None
        assert gate_row[0] == 4, "the materialise gate must see the failed comparison too"


def test_a_raising_structural_stage_crashes_the_persist_step_in_the_real_database(
    live_conn: psycopg.Connection[Any],
) -> None:
    """The other ordering: the *first* stage (structural) raises.

    Originally `_compare`'s catch-all marked every code not yet seen - structural and distribution
    alike - and then the loop moved on and distribution ran for real, re-adding its four codes.
    `_persist` does a plain `insert`, so the duplicate hit `drift_checks_run_code_key` and raised
    `UniqueViolation`, aborting the transaction `profile_source_file` shares with its profile
    upserts and its status write: the job crashed rather than merely letting a file through. Each
    stage now owns its own codes, so this asserts one row per code and a transaction still usable.
    """
    run_id, current_file_id, _baseline_file_id, _customer_id = _seed_minimal_run(live_conn)

    with patch("src.features.drift.run.compare_structure", side_effect=RuntimeError("boom")):
        outcome = run_mod.run_drift(
            live_conn,
            run_id=run_id,
            source_file_id=current_file_id,
            layout_version=1,
            kind="csv",
            record_width=None,
            spec_skip_code=None,
            spec_skip_detail=None,
        )

    # No `UniqueViolation`, so the transaction the profile job shares with its profile upserts,
    # its draft insert and its status write is still usable - which is the half of this bug that
    # would have crashed the whole job rather than merely letting a file through.
    assert outcome.blocking == len(STRUCTURAL_CODES)
    with live_conn.cursor() as cur:
        cur.execute(
            "select drift_code, count(*) from drift_checks where ingest_run_id = %s "
            "group by drift_code having count(*) > 1",
            (run_id,),
        )
        assert cur.fetchall() == [], "a redelivered or raising comparison must not duplicate a code"
        cur.execute("select 1")  # the transaction is not aborted
        assert cur.fetchone() == (1,)
