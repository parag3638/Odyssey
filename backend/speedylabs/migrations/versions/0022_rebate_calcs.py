"""rebate_calc_attempts, rebate_calcs and rebate_calc_lines

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-07

Three tables, for the reason Phases 8 and 10 each have two: **a rebate calculation that failed,
matched nothing, or ran against unresolvable brand spellings produces exactly the output of a
calculation that correctly found nothing** - a number, `0.00`, in a well-formed table. So
`rebate_calc_attempts` records every program, every time, and a check constraint forbids an
`errored` attempt from carrying a period count. A row saying "this program examined 48,150 lines
and matched 17,422" is the only available proof that a zero was *computed*.

`rebate_calc_lines` is the per-line provenance, and it is not optional
---------------------------------------------------------------------
Every one of P13's five seeded causes computes its dollars as a single quantised line amount. If
this table stored anything other than that figure, attribution could not reconstruct the oracle's
numbers from stored rows. It is also what makes the drill-down honest: the headline is the sum of
its parts because the parts are what the headline was built from.

Decisions visible in the shape
------------------------------
* **Two calendar-month check constraints.** "Periods are calendar months" becomes a property of
  the data rather than a convention inside a function. A period that is not a whole month is the
  kind of mistake that surfaces as a rebate paid twice.
* **`contributed_line_count > 0 or (basis_qty = 0 and amount = 0)`** - the honesty constraint. A
  period with nothing in it cannot carry money. Its mirror on the line table is
  `line_status = 'contributed' or both contributions are zero`: a skipped line cannot quietly pay.
* **No `basis_qty >= 0` or `amount >= 0`.** Deliberate, and measured: 548 of the 48,150 lines in
  the committed seeded file carry a **negative** qty, because a distributor's export includes
  credits and returns, and `canonical_lines.qty` allows a leading minus with no non-negativity
  check of its own. A return reduces the rebate earned - which is what
  `generator/legacy.py::new_rebate` already computes, since it multiplies the rate by whatever the
  qty is - so a negative contribution is correct arithmetic, and a period whose returns exceed its
  sales has a negative total. A sign constraint here would turn that ordinary month into a failed
  job with a constraint violation instead of a number.
* **Three named counters, not one `line_count`.** `contributed`, `skipped` and `out_of_scope` are
  three different facts, and the UI shows the middle one as a warning and the last as neutral
  text. 17,550 lines a program was never about are not 17,550 problems.
* **`line_status` has three values, not two.** `out_of_basis_scope` is separate from `skipped`
  because a case line under a per-pound billback is not a defect. Measured over the committed
  fixture: lumping them reports 17,550 skipped lines where 195 are real.
* **`ingest_run_id` is denormalised onto `rebate_calc_lines`**, so the per-run delete is one
  statement rather than a join and the brand-coverage anti-join is run-scoped without touching
  `rebate_calcs`. `materialization_errors` carries its run id for the same reason.
* **No `updated_at` on `rebate_calc_lines`**, following `canonical_lines`: these rows are written
  once by `COPY` and never updated, and 48,150 columns nothing reads are not free.
* **No `basis_uom` column.** It would be a copy of the program's `uom_basis` that no constraint
  could keep honest across two tables, and the list query already joins `rebate_programs`.
* **`program_id` cascades rather than restricts.** A deleted program's computed results are
  meaningless, and `restrict` would fire spuriously when a customer is deleted and Postgres
  chooses to cascade to `rebate_programs` before `ingest_runs`.
* **`unique (rebate_calc_id, canonical_line_id)` is the backstop, not the mechanism.** The
  mechanism is delete-then-insert per run, inside one transaction.

No new `ingest_runs` status: `calculated_at` has existed, unwritten, since migration 0003.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

OUTCOMES = ("calculated", "no_lines_matched", "out_of_effective_range", "errored")
LINE_STATUSES = ("contributed", "skipped", "out_of_basis_scope")


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


def _run_fk(name: str = "ingest_run_id") -> sa.Column[UUID]:
    return sa.Column(
        name,
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
        nullable=False,
    )


def _program_fk() -> sa.Column[UUID]:
    return sa.Column(
        "program_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("rebate_programs.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "rebate_calc_attempts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        _run_fk(),
        _program_fk(),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("lines_examined", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lines_matched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("periods_written", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Stored per attempt, not read from code at display time: a result must keep saying which
        # engine produced it after the engine has moved on.
        sa.Column("engine_name", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "ingest_run_id", "program_id", name="rebate_calc_attempts_run_program_key"
        ),
        sa.CheckConstraint(
            _in_list("outcome", OUTCOMES), name="rebate_calc_attempts_outcome_check"
        ),
        # Anything but a plain success has to say why, or the row is a shrug - and a shrug reads
        # the same as "we calculated and the answer was nothing".
        sa.CheckConstraint(
            "outcome = 'calculated' or reason is not null",
            name="rebate_calc_attempts_reason_check",
        ),
        # The load-bearing one: a failure can never be read as a total.
        sa.CheckConstraint(
            "outcome <> 'errored' or periods_written = 0",
            name="rebate_calc_attempts_errored_has_no_periods_check",
        ),
        sa.CheckConstraint(
            "lines_examined >= 0 and lines_matched >= 0 and lines_matched <= lines_examined "
            "and periods_written >= 0",
            name="rebate_calc_attempts_counts_check",
        ),
    )

    op.create_table(
        "rebate_calcs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        _run_fk(),
        _program_fk(),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        # numeric, never float: BACKEND.md §5. 4dp on a basis because a case count can be a
        # fraction of a case; 2dp on money because money is cents.
        sa.Column("basis_qty", sa.Numeric(18, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "contributed_line_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("skipped_line_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "out_of_scope_line_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("engine_name", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "ingest_run_id",
            "program_id",
            "period_start",
            name="rebate_calcs_run_program_period_key",
        ),
        sa.CheckConstraint(
            "period_start = date_trunc('month', period_start)::date",
            name="rebate_calcs_period_start_is_month_start_check",
        ),
        sa.CheckConstraint(
            "period_end = (date_trunc('month', period_start) + interval '1 month' "
            "- interval '1 day')::date",
            name="rebate_calcs_period_is_whole_month_check",
        ),
        sa.CheckConstraint(
            "contributed_line_count >= 0 and skipped_line_count >= 0 "
            "and out_of_scope_line_count >= 0",
            name="rebate_calcs_counts_check",
        ),
        # A period with nothing in it cannot carry money.
        sa.CheckConstraint(
            "contributed_line_count > 0 or (basis_qty = 0 and amount = 0)",
            name="rebate_calcs_empty_period_pays_nothing_check",
        ),
    )
    op.create_index("ix_rebate_calcs_run_period", "rebate_calcs", ["ingest_run_id", "period_start"])

    op.create_table(
        "rebate_calc_lines",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        _run_fk(),
        sa.Column(
            "rebate_calc_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("rebate_calcs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "canonical_line_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("canonical_lines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_status", sa.Text(), nullable=False),
        sa.Column("skipped_reason", sa.Text(), nullable=True),
        sa.Column(
            "basis_contribution", sa.Numeric(18, 4), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "amount_contribution", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0")
        ),
        # No updated_at: written once by COPY, never updated. `canonical_lines` is the precedent.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "rebate_calc_id", "canonical_line_id", name="rebate_calc_lines_calc_line_key"
        ),
        sa.CheckConstraint(
            _in_list("line_status", LINE_STATUSES), name="rebate_calc_lines_status_check"
        ),
        # A reason belongs to exactly the skipped rows: an out-of-scope line is not a defect and
        # must not carry an explanation that reads like one, and a skipped line must say which.
        sa.CheckConstraint(
            "(line_status = 'skipped') = (skipped_reason is not null)",
            name="rebate_calc_lines_reason_check",
        ),
        sa.CheckConstraint(
            "line_status = 'contributed' or (basis_contribution = 0 and amount_contribution = 0)",
            name="rebate_calc_lines_only_contributed_pays_check",
        ),
    )
    op.create_index("ix_rebate_calc_lines_run", "rebate_calc_lines", ["ingest_run_id"])
    op.create_index(
        "ix_rebate_calc_lines_calc_status", "rebate_calc_lines", ["rebate_calc_id", "line_status"]
    )
    op.create_index(
        "ix_rebate_calc_lines_canonical_line", "rebate_calc_lines", ["canonical_line_id"]
    )


def downgrade() -> None:
    # A calculated run would keep a timestamp pointing at results that no longer exist.
    op.execute("update ingest_runs set calculated_at = null where calculated_at is not null")
    op.drop_index("ix_rebate_calc_lines_canonical_line", table_name="rebate_calc_lines")
    op.drop_index("ix_rebate_calc_lines_calc_status", table_name="rebate_calc_lines")
    op.drop_index("ix_rebate_calc_lines_run", table_name="rebate_calc_lines")
    op.drop_table("rebate_calc_lines")
    op.drop_index("ix_rebate_calcs_run_period", table_name="rebate_calcs")
    op.drop_table("rebate_calcs")
    op.drop_table("rebate_calc_attempts")
