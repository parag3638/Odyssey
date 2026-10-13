"""source_files and ingest_runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05

`ingest_runs.status` is a one-way door: it appears in the API and maps 1:1 to the UI's
badge variants, so renaming a value is a §3 breaking change, not a refactor.

The stage timestamps are the basis of the A6 metric ("time from raw upload to first
variance report"), which is why each stage gets its own column rather than a single
`updated_at` that would make the elapsed time unrecoverable.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

#: The full vocabulary. Every value maps to exactly one badge variant in the UI.
RUN_STATUSES = (
    "queued",
    "parsing",
    "parsed",
    "profiled",
    "mapped",
    "materialized",
    "ready",
    "blocked",
    "rejected",
    "failed",
)


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
        "source_files",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        # Kept to show the user what they uploaded. Never used to build a path - the
        # on-disk identity is `stored_name`, which we generate.
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("stored_name", sa.Text(), nullable=False, unique=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("sniffed_type", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        # Null until the parse job counts them.
        sa.Column("row_count", sa.Integer(), nullable=True),
        # A salted HMAC. §4 forbids the raw address reaching the database.
        sa.Column("uploader_ip_hash", sa.Text(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "kind in ('erp_invoice_lines','legacy_rebate')", name="source_files_kind_check"
        ),
        sa.CheckConstraint("byte_size > 0", name="source_files_byte_size_check"),
        sa.CheckConstraint("format in ('csv','fixed_width')", name="source_files_format_check"),
        sa.CheckConstraint(
            "row_count is null or row_count >= 0", name="source_files_row_count_check"
        ),
    )
    op.create_index(
        "ix_source_files_customer_created",
        "source_files",
        ["customer_id", sa.text("created_at desc")],
    )
    # For spotting a re-upload of a file already seen.
    op.create_index("ix_source_files_sha256", "source_files", ["sha256"])

    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        # procrastinate's job id, so a run can be traced to the job that moved it.
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        # One column per stage: A6 measures the span between two of these, which a
        # single updated_at could not reconstruct.
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profiled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("readiness_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_variance_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elapsed_ms_to_first_variance", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status in (" + ", ".join(f"'{s}'" for s in RUN_STATUSES) + ")",
            name="ingest_runs_status_check",
        ),
        sa.CheckConstraint(
            "elapsed_ms_to_first_variance is null or elapsed_ms_to_first_variance >= 0",
            name="ingest_runs_elapsed_check",
        ),
        # An error code and a message travel together or not at all.
        sa.CheckConstraint(
            "(error_code is null) = (error_message is null)", name="ingest_runs_error_pair_check"
        ),
    )
    op.create_index(
        "ix_ingest_runs_customer_created",
        "ingest_runs",
        ["customer_id", sa.text("created_at desc")],
    )
    op.create_index("ix_ingest_runs_status", "ingest_runs", ["status"])
    op.create_index("ix_ingest_runs_source_file", "ingest_runs", ["source_file_id"])


def downgrade() -> None:
    op.drop_index("ix_ingest_runs_source_file", table_name="ingest_runs")
    op.drop_index("ix_ingest_runs_status", table_name="ingest_runs")
    op.drop_index("ix_ingest_runs_customer_created", table_name="ingest_runs")
    op.drop_table("ingest_runs")
    op.drop_index("ix_source_files_sha256", table_name="source_files")
    op.drop_index("ix_source_files_customer_created", table_name="source_files")
    op.drop_table("source_files")
