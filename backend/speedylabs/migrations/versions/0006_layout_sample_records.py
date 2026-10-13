"""file_layouts.sample_records

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06

A few verbatim records, so the field editor can draw a ruler over real characters.

Separate from `inference_evidence` on purpose. The first version of this put them inside
that document, which broke the editor the moment a human saved: a human-authored layout
deliberately stores no inference evidence - copying the histogram forward would credit
their boundaries to a measurement that did not produce them - and the sample records went
with it, leaving version 2's editor with no ruler and no preview.

The two are different kinds of fact. The histogram and its threshold are evidence *for*
the inferred boundaries, and belong only to an inferred layout. The records are the file's
own content, unchanged by anybody's edit, and every version needs them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "file_layouts",
        sa.Column(
            "sample_records",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "file_layouts_sample_records_array_check",
        "file_layouts",
        "jsonb_typeof(sample_records) = 'array'",
    )
    # Backfill from where they were previously written, so layouts created before this
    # migration keep their ruler instead of silently losing it.
    op.execute(
        """
        update file_layouts
        set sample_records = coalesce(inference_evidence -> 'sampleRecords', '[]'::jsonb)
        where inference_evidence ? 'sampleRecords'
        """
    )


def downgrade() -> None:
    op.drop_constraint("file_layouts_sample_records_array_check", "file_layouts")
    op.drop_column("file_layouts", "sample_records")
