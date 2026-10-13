"""mapping_drafts, mapping_validations

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06

The working mapping for one file and layout version, plus the verdicts last computed for
it. mvp.md numbers this 0006; 0006 was taken by `file_layouts.sample_records`, so it is
0007 here.

One draft per (source_file_id, layout_version), not per attempt. A boundary edit produces
a new layout version and therefore a new draft, which is right: the columns a mapping
refers to are the columns that layout defines, and carrying a mapping across a boundary
change would have it point at fields that no longer exist.

`mapping` is jsonb rather than a row per field because the PATCH has to be a single
merging statement. Two people - or one person and P6's "propose mapping" - editing
different canonical fields at the same time must both survive, and read-modify-write in
the application loses one of them. `mapping || :patch` in one statement cannot.

`canonical_schema_version` is stamped on the draft so a mapping written against 1.0.0 is
never silently reinterpreted under a later schema. It is also part of P6's cache key.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

VERDICTS = ("pass", "warn", "fail")


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
        "mapping_drafts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layout_version", sa.Integer(), nullable=False),
        sa.Column("canonical_schema_version", sa.Text(), nullable=False),
        sa.Column(
            "mapping", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamps(),
        sa.CheckConstraint("layout_version >= 1", name="mapping_drafts_layout_version_check"),
        # An object, not an array or a scalar: every reader indexes it by canonical field
        # name, and a jsonb array would pass a plain `not null` and break all of them.
        sa.CheckConstraint(
            "jsonb_typeof(mapping) = 'object'", name="mapping_drafts_mapping_object_check"
        ),
        sa.UniqueConstraint(
            "source_file_id", "layout_version", name="mapping_drafts_file_layout_key"
        ),
    )

    op.create_table(
        "mapping_validations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "mapping_draft_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("mapping_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_field", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("checked_rows", sa.Integer(), nullable=False),
        sa.Column("failed_rows", sa.Integer(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        # int[] rather than jsonb: these are row numbers a drill-down query filters on,
        # and an array of integers is the shape Postgres can index and compare.
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
        sa.CheckConstraint(
            "verdict in (" + ", ".join(f"'{verdict}'" for verdict in VERDICTS) + ")",
            name="mapping_validations_verdict_check",
        ),
        sa.CheckConstraint("checked_rows >= 0", name="mapping_validations_checked_check"),
        sa.CheckConstraint(
            "failed_rows >= 0 and failed_rows <= checked_rows",
            name="mapping_validations_failed_check",
        ),
        # One verdict per field per draft: a re-validation replaces the previous answer
        # rather than appending, so the UI never has to work out which run it is seeing.
        sa.UniqueConstraint(
            "mapping_draft_id",
            "canonical_field",
            name="mapping_validations_draft_field_key",
        ),
    )
    op.create_index("ix_mapping_validations_draft", "mapping_validations", ["mapping_draft_id"])


def downgrade() -> None:
    op.drop_index("ix_mapping_validations_draft", table_name="mapping_validations")
    op.drop_table("mapping_validations")
    op.drop_table("mapping_drafts")
