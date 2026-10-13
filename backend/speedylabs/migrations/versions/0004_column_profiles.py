"""column_profiles

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06

`layout_version` exists from the start even though nothing increments it yet. P4 lets a
human adjust inferred fixed-width boundaries, which re-profiles the same file under a new
layout - and retrofitting a version column onto a unique key later would mean a migration
that cannot preserve the existing rows' identity.

The unique key is what makes the job idempotent: the profiler upserts on
(source_file_id, layout_version, column_index), so a redelivered job rewrites rather than
duplicating.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

#: The guess vocabulary. A one-way door: it feeds P5's mapping validators and appears in
#: the API, so adding a value is additive and renaming one is a §3 breaking change.
TYPE_GUESSES = ("integer", "decimal", "date", "boolean", "text", "empty")


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
        "column_profiles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layout_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("column_index", sa.Integer(), nullable=False),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("type_guess", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("null_count", sa.Integer(), nullable=False),
        # numeric, not float: a ratio the UI colours by thresholds should not drift.
        sa.Column("null_pct", sa.Numeric(9, 6), nullable=False),
        sa.Column("distinct_count", sa.Integer(), nullable=False),
        sa.Column("distinct_capped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("min_text", sa.Text(), nullable=True),
        sa.Column("max_text", sa.Text(), nullable=True),
        # The date encoding every value fitted, so P5's transform knows which to decode
        # without re-reading the file.
        sa.Column("date_format", sa.Text(), nullable=True),
        sa.Column("sample_values", postgresql.JSONB(), nullable=False),
        sa.Column("patterns", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "type_guess in (" + ", ".join(f"'{t}'" for t in TYPE_GUESSES) + ")",
            name="column_profiles_type_guess_check",
        ),
        sa.CheckConstraint("null_pct between 0 and 1", name="column_profiles_null_pct_check"),
        sa.CheckConstraint("row_count >= 0", name="column_profiles_row_count_check"),
        sa.CheckConstraint(
            "null_count >= 0 and null_count <= row_count",
            name="column_profiles_null_count_check",
        ),
        sa.CheckConstraint("distinct_count >= 0", name="column_profiles_distinct_check"),
        sa.CheckConstraint("layout_version >= 1", name="column_profiles_layout_version_check"),
        sa.UniqueConstraint(
            "source_file_id",
            "layout_version",
            "column_index",
            name="column_profiles_file_layout_index_key",
        ),
    )
    op.create_index(
        "ix_column_profiles_file_layout",
        "column_profiles",
        ["source_file_id", "layout_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_column_profiles_file_layout", table_name="column_profiles")
    op.drop_table("column_profiles")
