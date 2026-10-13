"""variance_line_links — every variance dollar tied to the line it came from

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-07

A5 claims a percentage of variance dollars explained. That claim is only as good as its unit, and
the unit is this table: one row per (variance, line) saying how many dollars of that variance came
from that line and why. Rolling up from lines rather than classifying whole variances is what makes
the percentage measurable per dollar and the drill-down truthful.

Decisions visible in the shape
------------------------------
* **`check ((root_cause = 'unexplained') = (canonical_line_id is null))`**, the honesty constraint,
  in the shape `legacy_rebate_lines`' `(match_status = 'matched') = (program_id is not null)`
  already uses. It is **tighter than mvp.md's**, which exempts `date_cutoff` from naming a line.
  Measured: all 254 `date_cutoff` lines are real canonical lines, each named **twice** - once
  negative on the month the legacy system took the money out of, once positive on the month it put
  it into. The relocation is between periods, not away from the line. The only link with no line is
  the per-variance residue, and the biconditional also forbids the other half: an `unexplained`
  link pinned to a line, which would make a residue look like a per-line judgement it is not.
* **`unexplained` is a link cause and is not `unattributed`.** `variances.root_cause` uses
  `unattributed`, meaning "nothing has run yet"; a link uses `unexplained`, meaning "the rules ran
  and these dollars were left over". Two different facts. mvp.md uses both words for both things.
* **`unique (variance_id, canonical_line_id)`** is the backstop; the mechanism is delete-then-insert
  per run in one transaction. It is safe despite `date_cutoff` naming a line twice, because those
  two links sit on two **different** variances.
* **A partial unique index for the residue**, because Postgres treats NULLs as distinct, so the
  pair key alone would permit two residue links on one variance - the same NULL-distinctness trap
  that made `variances.program_id` `not null` in 0026.
* **No non-negativity on `contribution_amount`.** Relocation writes a negative link on one side by
  construction, and 0022 and 0026 both removed the same constraint for measured reasons.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "variance_line_links",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        # Denormalised so the per-run delete and the by-cause rollup are one statement each,
        # the reasoning `rebate_calc_lines` and `materialization_errors` both use.
        sa.Column(
            "ingest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variance_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("variances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "canonical_line_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("canonical_lines.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("contribution_amount", sa.Numeric(18, 2), nullable=False),
        # What the rule saw. The drill-down renders it, so a reader can check the judgement
        # rather than take it.
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "(root_cause = 'unexplained') = (canonical_line_id is null)",
            name="variance_line_links_residue_has_no_line_check",
        ),
        sa.UniqueConstraint(
            "variance_id", "canonical_line_id", name="variance_line_links_variance_line_key"
        ),
    )
    op.create_index(
        "variance_line_links_one_residue_key",
        "variance_line_links",
        ["variance_id"],
        unique=True,
        postgresql_where=sa.text("root_cause = 'unexplained'"),
    )
    op.create_index(
        "ix_variance_line_links_variance_cause",
        "variance_line_links",
        ["variance_id", "root_cause"],
    )
    op.create_index(
        "ix_variance_line_links_run_cause", "variance_line_links", ["ingest_run_id", "root_cause"]
    )
    op.create_index(
        "ix_variance_line_links_canonical_line", "variance_line_links", ["canonical_line_id"]
    )


def downgrade() -> None:
    # Attribution is gone, so no variance can still claim to be explained.
    op.execute(
        "update variances set explained = false, root_cause = 'unattributed' "
        "where explained or root_cause <> 'unattributed'"
    )
    op.drop_index("ix_variance_line_links_canonical_line", table_name="variance_line_links")
    op.drop_index("ix_variance_line_links_run_cause", table_name="variance_line_links")
    op.drop_index("ix_variance_line_links_variance_cause", table_name="variance_line_links")
    op.drop_index("variance_line_links_one_residue_key", table_name="variance_line_links")
    op.drop_table("variance_line_links")
