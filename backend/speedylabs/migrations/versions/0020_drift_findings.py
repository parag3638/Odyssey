"""drift_checks, drift_findings, the decision, and the drift_blocked status

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-07

Two tables, for the reason Phase 8 has two: `drift_checks` records **every check, every time**,
including the ones that found nothing and the ones that could not run. "`column_renamed` is not
applicable, because a fixed-width layout's field names are positional" is a sentence a user needs,
and a missing row cannot say it.

`drift_blocked` is a **new status**, not a reuse of `blocked`
--------------------------------------------------------------
`blocked` already means "materialised, and readiness found blocking issues" (P8). One badge value
cannot also mean "nothing was ingested and a human must decide": they are opposite situations at
opposite ends of the chain - one has 48,150 rows in the database and defects to fix, the other has
nothing in and a decision to make. `RunStatus` is a 1:1 badge vocabulary, so a second meaning costs
a migration, and that is the right price.

`drift_blocked` joins `TERMINAL_STATUSES`: nothing follows it without a human.

`rejected` already exists in `RunStatus` and in `TERMINAL_STATUSES` and has never been written by
anything. Drift reject is its first writer, so it needs no migration.

Other decisions visible in the shape
------------------------------------
* `severity` is **stored**, not derived at read time. A run blocked over a cited column stays
  recorded that way even if the mapping is edited afterwards.
* `baseline_column_name` is separate from `column_name`, so a `column_renamed` finding carries both
  names on one row. mvp.md's single `column_name` cannot.
* `nulls not distinct` on the unique index: without it the index does not constrain
  `record_width_changed`, whose `column_name` is null, and a redelivered job would insert it twice.
  Postgres 15+; this stack runs 16.
* The baseline is recorded **once on the run**, not on every finding as mvp.md has it. It
  is one fact per comparison, and putting it on the run is the only way a run with **zero**
  findings can still say what it was compared against.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

OUTCOMES = ("findings", "clean", "not_applicable", "not_comparable", "errored")
SEVERITIES = ("blocking", "warning")
DECISIONS = ("pending", "accepted", "rejected")

#: Run statuses after this migration. Frozen copies - a migration is a snapshot.
STATUSES_BEFORE = (
    "queued",
    "parsing",
    "parsed",
    "profiled",
    "mapped",
    "materializing",
    "materialized",
    "ready",
    "blocked",
    "rejected",
    "failed",
)
STATUSES_AFTER = (*STATUSES_BEFORE, "drift_blocked")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _replace_status_check(values: tuple[str, ...]) -> None:
    op.drop_constraint("ingest_runs_status_check", "ingest_runs", type_="check")
    op.create_check_constraint(
        "ingest_runs_status_check", "ingest_runs", _in_list("status", values)
    )


def upgrade() -> None:
    op.create_table(
        "drift_checks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "ingest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("drift_code", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("columns_examined", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("ingest_run_id", "drift_code", name="drift_checks_run_code_key"),
        sa.CheckConstraint(_in_list("outcome", OUTCOMES), name="drift_checks_outcome_check"),
        sa.CheckConstraint("columns_examined >= 0", name="drift_checks_columns_check"),
        # Anything other than a plain finding or a clean pass has to say why, or the row is a
        # shrug - and a shrug is indistinguishable from "we checked and it was fine".
        sa.CheckConstraint(
            "outcome in ('findings', 'clean') or reason is not null",
            name="drift_checks_reason_check",
        ),
    )

    op.create_table(
        "drift_findings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "ingest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("drift_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("column_name", sa.Text(), nullable=True),
        # The name this column had in the baseline, when they differ. One row carries both.
        sa.Column("baseline_column_name", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(_in_list("severity", SEVERITIES), name="drift_findings_severity_check"),
    )
    # `nulls not distinct`, so a finding whose `column_name` is null is still constrained.
    op.execute(
        "create unique index drift_findings_run_code_column_key on drift_findings "
        "(ingest_run_id, drift_code, column_name) nulls not distinct"
    )
    op.create_index(
        "ix_drift_findings_run_severity", "drift_findings", ["ingest_run_id", "severity"]
    )

    op.add_column(
        "ingest_runs",
        sa.Column(
            "drift_baseline_spec_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("mapping_specs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ingest_runs",
        sa.Column(
            "drift_baseline_source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("ingest_runs", sa.Column("drift_decision", sa.Text(), nullable=True))
    op.add_column("ingest_runs", sa.Column("drift_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ingest_runs_drift_decision_check",
        "ingest_runs",
        f"drift_decision is null or {_in_list('drift_decision', DECISIONS)}",
    )
    _replace_status_check(STATUSES_AFTER)


def downgrade() -> None:
    # A blocked run would fail the narrower constraint, so settle it first. `profiled` rather than
    # anything later: nothing was ingested, which is exactly what `profiled` means.
    op.execute("update ingest_runs set status = 'profiled' where status = 'drift_blocked'")
    _replace_status_check(STATUSES_BEFORE)
    op.drop_constraint("ingest_runs_drift_decision_check", "ingest_runs", type_="check")
    op.drop_column("ingest_runs", "drift_at")
    op.drop_column("ingest_runs", "drift_decision")
    op.drop_column("ingest_runs", "drift_baseline_source_file_id")
    op.drop_column("ingest_runs", "drift_baseline_spec_id")
    op.drop_index("ix_drift_findings_run_severity", table_name="drift_findings")
    op.drop_index("drift_findings_run_code_column_key", table_name="drift_findings")
    op.drop_table("drift_findings")
    op.drop_table("drift_checks")
