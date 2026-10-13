"""mapping_specs: which layout version the spec was approved from

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-07

`approved_from_source_file_id` alone does not identify the column profiles a spec's columns belong
to. A human may edit that file's field boundaries afterwards, and version 2's profiles are keyed to
different columns - so drift comparing against "the newest layout of the baseline file" would
silently compare the wrong columns and report differences nobody introduced.

Nullable forever: a spec approved before this column existed has no recorded version, and drift says
so (`baseline_version_unknown`) rather than guessing. No backfill - a migration must not reconstruct
a decision nobody recorded.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mapping_specs",
        sa.Column("approved_from_layout_version", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "mapping_specs_approved_from_layout_version_check",
        "mapping_specs",
        "approved_from_layout_version is null or approved_from_layout_version >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "mapping_specs_approved_from_layout_version_check", "mapping_specs", type_="check"
    )
    op.drop_column("mapping_specs", "approved_from_layout_version")
