"""legacy_rebate_lines — the customer's own rebate numbers, as imported

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-07

One row per data row of the legacy export, matched to a seeded program or reported as unmatched.
Never dropped: a legacy row nobody could match is the single most useful thing this screen can tell
someone, because it means their program reference and ours disagree and the variance table would
otherwise just be missing a line.

Decisions visible in the shape
------------------------------
* **`check ((match_status = 'matched') = (program_id is not null))`** is the honesty constraint. A
  matched row without a program, or an unmatched row carrying one, is unrepresentable rather than
  merely unlikely. It is also why `program_id` cascades rather than nulling: `on delete set null`
  would violate it.
* **`match_status` has two values, not three.** mvp.md adds `ambiguous`, which cannot occur: both
  candidate keys are unique per customer - `(customer_id, external_ref)` from 0023 and
  `(customer_id, name)` from 0001 - so no lookup can return two programs. A check constraint can be
  widened later if that ever stops being true.
* **`customer_id` is denormalised** so an ownership check is one statement, the reasoning
  `rebate_calc_lines` and `materialization_errors` both use for carrying their own run id.
* **`source_row_number`** is the physical line (`docs/decisions.md`), so the first data row of a
  CSV with a header is 2. Needed twice: a refusal names the row, and the unmatched panel points at
  it.
* **`raw jsonb`** holds the whole record as header -> value. It is the only place `BASIS_UNITS`
  exists, and deliberately so: that column carries *our* quantity rather than the legacy system's
  (the generator never adjusts it for the seeded causes), so it is kept as provenance and read by
  nothing.
* **No non-negativity on `amount`.** A legacy system can report a negative period, and P11 already
  removed the same constraint from `rebate_calcs` after measuring 548 negative-qty canonical lines.
* **No index on `(customer_id, period_start)`**, which mvp.md asks for: no query in this phase
  filters legacy lines that way, and an index for a query nobody has is BACKEND.md §11.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

MATCH_STATUSES = ("matched", "unmatched_program")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "legacy_rebate_lines",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("program_external_ref", sa.Text(), nullable=False),
        # The name the file carried, kept for the unmatched report. Not a matching key.
        sa.Column("program_name_text", sa.Text(), nullable=True),
        sa.Column(
            "program_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("rebate_programs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        # numeric, never float: BACKEND.md §5.
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("match_status", sa.Text(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "source_file_id",
            "program_external_ref",
            "period_start",
            name="legacy_rebate_lines_file_program_period_key",
        ),
        sa.CheckConstraint(
            _in_list("match_status", MATCH_STATUSES),
            name="legacy_rebate_lines_match_status_check",
        ),
        sa.CheckConstraint(
            "(match_status = 'matched') = (program_id is not null)",
            name="legacy_rebate_lines_match_agrees_check",
        ),
        sa.CheckConstraint(
            "period_start = date_trunc('month', period_start)::date",
            name="legacy_rebate_lines_period_start_is_month_start_check",
        ),
        sa.CheckConstraint(
            "period_end = (date_trunc('month', period_start) + interval '1 month' "
            "- interval '1 day')::date",
            name="legacy_rebate_lines_period_is_whole_month_check",
        ),
        sa.CheckConstraint("source_row_number > 0", name="legacy_rebate_lines_row_number_check"),
    )
    op.create_index(
        "ix_legacy_rebate_lines_file_match",
        "legacy_rebate_lines",
        ["source_file_id", "match_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_legacy_rebate_lines_file_match", table_name="legacy_rebate_lines")
    op.drop_table("legacy_rebate_lines")
