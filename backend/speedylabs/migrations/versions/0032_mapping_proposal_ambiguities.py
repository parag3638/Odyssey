"""mapping_proposals.ambiguities - the model's own "I could not tell these apart" lines

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-13

Both cache paths in `features/mapping/propose.py` used to answer `ambiguities: []`, because
nothing stored them: an ambiguity the model named on the paid call was lost on every replay.
The deterministic look-alike rule (`proposal_guard.flag_indistinguishable`) is recomputed on read
and needs no column; the model's own wording cannot be recomputed, so it is stored here.

Additive, `not null default '[]'`: on Postgres 16 that is a metadata-only change, and a row made
before this migration reads as "the model named none", which is what the old code served anyway.
Downgrading drops the column and loses only the stored model wording - no one-way door.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mapping_proposals",
        sa.Column(
            "ambiguities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "mapping_proposals_ambiguities_array_check",
        "mapping_proposals",
        "jsonb_typeof(ambiguities) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "mapping_proposals_ambiguities_array_check", "mapping_proposals", type_="check"
    )
    op.drop_column("mapping_proposals", "ambiguities")
