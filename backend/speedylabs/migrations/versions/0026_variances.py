"""variances — what they said, what we computed, and the difference

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-07

One row per program per calendar month: the legacy amount, ours, and the signed difference. P13
then attributes each difference to a cause; this migration defines the columns it will write so
that phase migrates nothing.

Decisions visible in the shape
------------------------------
* **`check (variance_amount = legacy_amount - calculated_amount)`** keeps the arithmetic true in
  the schema rather than only in code (BACKEND.md §1.7). The computation writes all three in one
  `insert … select`, so the constraint is structurally satisfied and a test proving it bites has to
  insert an inconsistent row by hand.
* **`presence`, a named column, not a convention inside `detail`.** A jsonb convention cannot be
  check-constrained and cannot be filtered on, and both matter: `legacy_only` means we calculated
  nothing for a month they paid for, `calculated_only` the reverse, and the two have opposite
  meanings for whoever has to act. Its two companion constraints make the zero side structural.
* **`check (explained = false or root_cause <> 'unattributed')`** is this table's honesty
  constraint - a variance marked explained must name what explains it. P12 writes only the
  defaults; P13 writes both together or neither.
* **`root_cause` is a plain text column with a default, not a database enum.** P13 is still
  settling its rule set, the vocabulary lives in `features/variance/codes.py` with an import-time
  guard and a test pinning it to the committed oracle's cause names, and that is what
  `readiness_findings.code` already does.
* **`program_id` is `not null`**, unlike mvp.md. Postgres treats NULLs as distinct in a unique
  index, so a nullable `program_id` would defeat `unique (ingest_run_id, program_id, period_start)`
  entirely - the one key that makes a recompute replace rather than accumulate.
* **No non-negativity on any of the three amounts.** Two of the eighteen demo variances are
  negative (`PG-CPC-CS 2026-03` at -1003.50 and `PG-NVF-CS 2026-03` at -1748.75), because the
  legacy system pushed 31 March's lines into April and paid *less* than we do in that month.
* **`ix_variances_run_period_program`** matches the list's keyset order, which the unique key
  cannot serve because it puts `program_id` first. At eighteen rows Postgres will prefer a
  sequential scan over either, correctly.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

PRESENCE = ("both", "legacy_only", "calculated_only")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "variances",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "ingest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "program_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("rebate_programs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Which export these legacy numbers came from. One comparison per run, replaced on a
        # recompute rather than accumulated.
        sa.Column(
            "legacy_source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("legacy_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("calculated_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("variance_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("presence", sa.Text(), nullable=False),
        sa.Column(
            "root_cause", sa.Text(), nullable=False, server_default=sa.text("'unattributed'")
        ),
        sa.Column("explained", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "ingest_run_id",
            "program_id",
            "period_start",
            name="variances_run_program_period_key",
        ),
        sa.CheckConstraint(
            "variance_amount = legacy_amount - calculated_amount",
            name="variances_arithmetic_check",
        ),
        sa.CheckConstraint(_in_list("presence", PRESENCE), name="variances_presence_check"),
        sa.CheckConstraint(
            "presence <> 'legacy_only' or calculated_amount = 0",
            name="variances_legacy_only_check",
        ),
        sa.CheckConstraint(
            "presence <> 'calculated_only' or legacy_amount = 0",
            name="variances_calculated_only_check",
        ),
        sa.CheckConstraint(
            "explained = false or root_cause <> 'unattributed'",
            name="variances_explained_names_a_cause_check",
        ),
        sa.CheckConstraint(
            "period_start = date_trunc('month', period_start)::date",
            name="variances_period_start_is_month_start_check",
        ),
        sa.CheckConstraint(
            "period_end = (date_trunc('month', period_start) + interval '1 month' "
            "- interval '1 day')::date",
            name="variances_period_is_whole_month_check",
        ),
    )
    op.create_index(
        "ix_variances_run_period_program",
        "variances",
        ["ingest_run_id", "period_start", "program_id"],
    )
    op.create_index("ix_variances_run_cause", "variances", ["ingest_run_id", "root_cause"])


def downgrade() -> None:
    # A computed run would keep a timestamp pointing at comparisons that no longer exist.
    op.execute(
        "update ingest_runs set first_variance_at = null where first_variance_at is not null"
    )
    op.drop_index("ix_variances_run_cause", table_name="variances")
    op.drop_index("ix_variances_run_period_program", table_name="variances")
    op.drop_table("variances")
