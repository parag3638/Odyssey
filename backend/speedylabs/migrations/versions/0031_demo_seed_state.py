"""demo_seed_state — whether this instance's demo data is the one we think it is

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-07

One row saying which seed version is loaded and whether it finished.

**`completed_at` is what the table is for.** The seeder must be able to tell "seeded" from "seeded
halfway and interrupted", and nothing else in the schema can: a row count cannot, because a
half-seed has a plausible-looking count, and a recorded list of run ids cannot either.

**No `run_ids` column**, which mvp.md specifies. The customer foreign key already reaches every
seeded row, and a recorded id list could never name a run uploaded *after* the seed - it would be a
scoping key that is wrong by construction.

**No foreign key to `customers`, deliberately.** This row has to survive the reset's cascade, or the
next run has nothing to read. It is the one table that is *supposed* to outlive a reset.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_seed_state",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("seed_version", sa.Text(), nullable=False),
        sa.Column("seeded_at", sa.DateTime(timezone=True), nullable=False),
        # Null until the seed finishes. A null here on a present row means "interrupted", which is
        # the one state the seeder has to be able to recognise.
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("seed_version", name="demo_seed_state_version_key"),
    )


def downgrade() -> None:
    op.drop_table("demo_seed_state")
