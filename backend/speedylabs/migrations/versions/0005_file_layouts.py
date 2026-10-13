"""file_layouts

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06

How a file was cut into fields, versioned. Every upload gets version 1 at parse time -
CSV as well as fixed-width, because a CSV's delimiter and header are a layout too, and
having one shape for both means P5's mapping reads fields the same way whatever the
source format was.

Edits are additive: a human adjusting a boundary writes version 2 and version 1 stays
exactly as inferred. That is what keeps `column_profiles.layout_version` truthful - a
profile row points at the layout it was computed under, and an in-place edit would
silently re-describe statistics that were never recomputed. It also makes an edit
reversible, which matters because a boundary edit is the one action in this tool that can
invalidate every downstream number.

Deviation from the plan's DDL, additive: `inference_evidence` jsonb. P4's API contract
requires `GET .../layout` to return `inferenceEvidence` (the blank histogram and the
threshold used), and recomputing it on read would mean re-reading the file on every
request - and would report evidence from today's thresholds against boundaries stored
under yesterday's.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

LAYOUT_KINDS = ("csv", "fixed_width")

#: Matches `src.features.layouts.schemas.MAX_FIELDS`. A record layout with more than
#: this many fields is not a layout, and the cap bounds both the jsonb size and the work
#: the PATCH validator does.
MAX_FIELDS = 200


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
        "file_layouts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("encoding", sa.Text(), nullable=False, server_default=sa.text("'ascii'")),
        # Null for fixed-width: there is no delimiter, and storing a placeholder would
        # make "no delimiter" indistinguishable from "a comma we failed to record".
        sa.Column("delimiter", sa.Text(), nullable=True),
        sa.Column("quote_char", sa.Text(), nullable=True),
        sa.Column("has_header", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("header_row_index", sa.Integer(), nullable=True),
        sa.Column("footer_row_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Null for CSV, where records have no fixed width.
        sa.Column("record_width", sa.Integer(), nullable=True),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("inference_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("inferred", sa.Boolean(), nullable=False),
        sa.Column("edited_by_human", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_timestamps(),
        sa.CheckConstraint(
            "kind in (" + ", ".join(f"'{kind}'" for kind in LAYOUT_KINDS) + ")",
            name="file_layouts_kind_check",
        ),
        sa.CheckConstraint("version >= 1", name="file_layouts_version_check"),
        sa.CheckConstraint("footer_row_count >= 0", name="file_layouts_footer_check"),
        sa.CheckConstraint(
            "record_width is null or record_width > 0", name="file_layouts_record_width_check"
        ),
        # `fields` must be an array, and one within the cap. A jsonb object or a string
        # here would pass a plain `jsonb not null` and break every reader.
        sa.CheckConstraint(
            "jsonb_typeof(fields) = 'array'", name="file_layouts_fields_array_check"
        ),
        sa.CheckConstraint(
            f"jsonb_array_length(fields) between 1 and {MAX_FIELDS}",
            name="file_layouts_fields_count_check",
        ),
        # A fixed-width layout without a record width cannot be sliced.
        sa.CheckConstraint(
            "kind <> 'fixed_width' or record_width is not null",
            name="file_layouts_fixed_width_needs_width_check",
        ),
        sa.UniqueConstraint("source_file_id", "version", name="file_layouts_file_version_key"),
    )
    # `version desc` so "the current layout" is the index's first row rather than a sort.
    op.create_index(
        "ix_file_layouts_file_version_desc",
        "file_layouts",
        ["source_file_id", sa.text("version desc")],
    )


def downgrade() -> None:
    op.drop_index("ix_file_layouts_file_version_desc", table_name="file_layouts")
    op.drop_table("file_layouts")
