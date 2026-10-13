"""rate_limits

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05

A fixed-window counter keyed by (key, window_start). Old windows are never read again,
so a periodic delete is enough housekeeping - no index on window_start is needed for
correctness, only for that cleanup, which is what the one index below is for.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limits",
        # Not a UUID id: the natural key *is* the identity, and a surrogate key would
        # need a unique constraint on the pair anyway for the atomic upsert to work.
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("count >= 0", name="rate_limits_count_check"),
    )
    # For expiring old windows, not for the hot path (which hits the primary key).
    op.create_index("ix_rate_limits_window_start", "rate_limits", ["window_start"])


def downgrade() -> None:
    op.drop_index("ix_rate_limits_window_start", table_name="rate_limits")
    op.drop_table("rate_limits")
