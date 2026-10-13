"""readiness_check_runs, readiness_findings

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-07

Two tables, because a readiness pass produces two different kinds of fact.

`readiness_check_runs` records **every check, every time**, including the ones that found
nothing and the ones that could not run. That is what makes "the checklist is generated from
this file" provable rather than asserted: a reader can see that the footer check examined
48,150 rows and agreed, or that it was not applicable because the file has no trailer, and
those are different statements. A templated checklist cannot express the difference.

`readiness_findings` holds only what was actually found - one row per check code, so a run has
at most eight.

Why `severity` is stored rather than derived
--------------------------------------------
A run that was blocked stays recorded as blocked for the reason that applied when it ran. If
the severity policy changes later - and `duplicate_line`'s is exactly the kind of thing a real
customer conversation changes - history must not silently rewrite itself.

Why the five-example cap is a check constraint
----------------------------------------------
`example_row_numbers` is bounded in the code too, but a bug that put 50,000 integers in a
JSON response would be a denial of service against our own UI, and the schema is where rules
that must always hold belong (BACKEND.md §1).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

OUTCOMES = ("findings", "clean", "not_applicable", "errored")
SEVERITIES = ("blocking", "warning")

#: Example rows per finding. Matches `readiness/rows.py::MAX_EXAMPLES`.
MAX_EXAMPLES = 5


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "readiness_check_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "ingest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_code", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("not_applicable_reason", sa.Text(), nullable=True),
        sa.Column("errored_message", sa.Text(), nullable=True),
        # How many rows this check actually *applied* to, which is not the file's row count:
        # the catch-weight check looks at pound lines only. The number is the check's own
        # statement about its coverage.
        sa.Column("rows_examined", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("context", postgresql.JSONB(), nullable=True),
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
        sa.UniqueConstraint(
            "ingest_run_id", "check_code", name="readiness_check_runs_run_code_key"
        ),
        sa.CheckConstraint(
            _in_list("outcome", OUTCOMES), name="readiness_check_runs_outcome_check"
        ),
        sa.CheckConstraint("rows_examined >= 0", name="readiness_check_runs_rows_check"),
        # A not-applicable outcome with no reason is the one thing this table must never
        # hold: it would be indistinguishable from "we checked and it was fine".
        sa.CheckConstraint(
            "outcome <> 'not_applicable' or not_applicable_reason is not null",
            name="readiness_check_runs_reason_check",
        ),
        sa.CheckConstraint(
            "outcome <> 'errored' or errored_message is not null",
            name="readiness_check_runs_errored_check",
        ),
    )

    op.create_table(
        "readiness_findings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "ingest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        # What the check found, in the unit the check counts in: lines for most, business
        # days for `date_gap`, one file for `footer_total_mismatch`.
        sa.Column("affected_rows", sa.Integer(), nullable=False),
        # Null where a dollar figure would be invented: the value of invoices that are not
        # in the file is not a number anybody has.
        sa.Column("affected_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column(
            "example_row_numbers",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'::integer[]"),
        ),
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
        sa.UniqueConstraint("ingest_run_id", "check_code", name="readiness_findings_run_code_key"),
        sa.CheckConstraint(
            _in_list("severity", SEVERITIES), name="readiness_findings_severity_check"
        ),
        sa.CheckConstraint("affected_rows >= 0", name="readiness_findings_affected_check"),
        sa.CheckConstraint(
            f"coalesce(array_length(example_row_numbers, 1), 0) <= {MAX_EXAMPLES}",
            name="readiness_findings_examples_check",
        ),
    )
    # The readiness screen's own filter: blocking first, warnings after.
    op.create_index(
        "ix_readiness_findings_run_severity", "readiness_findings", ["ingest_run_id", "severity"]
    )


def downgrade() -> None:
    op.drop_index("ix_readiness_findings_run_severity", table_name="readiness_findings")
    op.drop_table("readiness_findings")
    op.drop_table("readiness_check_runs")
