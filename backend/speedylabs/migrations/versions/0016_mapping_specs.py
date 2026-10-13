"""mapping_specs: the approved mapping a customer's next file maps itself with

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-07

A spec is keyed to a **customer and a format**, not to a file: that is the whole idea - approve
once, and the next export maps itself. It carries the layout as well as the mapping, because for
a fixed-width file the field boundaries are as much "how to read this customer's export" as the
canonical targets are.

At most one approved spec per customer and kind
-----------------------------------------------
Enforced by a **partial unique index**, so it is a database fact and not a code convention. Two
concurrent approvals: one commits, the other violates either that index or
`(customer_id, kind, version)`, and both map to `409 spec_conflict`. The version is computed
inside the insert (`coalesce(max(version), 0) + 1` in an `insert … select`), the pattern
`layouts/repo._INSERT_NEXT_VERSION` already proves - two writers cannot both read "max is 1"
and both write 2.

History is append-only
----------------------
No `revoked` status and no delete. Reversing a decision means approving an earlier version's
content again, which produces a *new* version. A spec governs how months of invoices were read;
being able to quietly remove one would make every run that cites it unexplainable.

`approved_from_source_file_id` is `on delete set null`, not cascade: deleting a file must not
destroy the spec approved from it.

`approved_forced` records that a human overrode a failing verdict, and the check constraint makes
the explanation mandatory - on the row, not in the document, because a reworded note must not
look like a changed mapping. `approved_from_ip_hash` matches `source_files.uploader_ip_hash`: the
demo is unauthenticated and this is its most consequential write, so it is auditable or it is not.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

KINDS = ("csv", "fixed_width")
STATUSES = ("approved", "superseded")
SHA256_LENGTH = 64
MAX_NOTE_LENGTH = 2000


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "mapping_specs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("canonical_schema_version", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("spec_sha256", sa.Text(), nullable=False),
        sa.Column(
            "approved_from_source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_note", sa.Text(), nullable=True),
        sa.Column("approved_forced", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("approved_from_ip_hash", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint("customer_id", "kind", "version", name="mapping_specs_version_key"),
        sa.CheckConstraint("version >= 1", name="mapping_specs_version_check"),
        sa.CheckConstraint(_in_list("kind", KINDS), name="mapping_specs_kind_check"),
        sa.CheckConstraint(_in_list("status", STATUSES), name="mapping_specs_status_check"),
        sa.CheckConstraint(
            f"char_length(spec_sha256) = {SHA256_LENGTH}", name="mapping_specs_sha256_check"
        ),
        sa.CheckConstraint(
            f"approved_note is null or char_length(approved_note) <= {MAX_NOTE_LENGTH}",
            name="mapping_specs_note_length_check",
        ),
        # Forcing over a failing verdict requires an explanation. In the schema, because the
        # rule must always hold (BACKEND.md §1) and because the explanation is the only record
        # of why a spec nobody validated cleanly now governs a customer's files.
        sa.CheckConstraint(
            "approved_forced is false or approved_note is not null",
            name="mapping_specs_forced_needs_note_check",
        ),
    )
    # The rule that makes "approve once" mean something: one live spec per customer per format.
    op.create_index(
        "ux_mapping_specs_one_approved",
        "mapping_specs",
        ["customer_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )
    op.create_index(
        "ix_mapping_specs_customer_kind_status",
        "mapping_specs",
        ["customer_id", "kind", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_mapping_specs_customer_kind_status", table_name="mapping_specs")
    op.drop_index("ux_mapping_specs_one_approved", table_name="mapping_specs")
    op.drop_table("mapping_specs")
