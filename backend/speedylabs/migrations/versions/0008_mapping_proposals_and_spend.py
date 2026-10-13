"""mapping_proposals, llm_spend, llm_spend_reservations

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06

mvp.md splits these across its 0007 and 0008; both numbers were taken (0006 by
`file_layouts.sample_records`, 0007 by `mapping_drafts`), and the three tables are one
coherent change - a proposal and what it cost are written in the same transaction - so
they land together as 0008.

Why a reservation table and not a counter
-----------------------------------------
The budget has to hold under concurrency, and OpenAI has no pre-flight token count, so
the cost of a call is unknown until it returns. A plain `settled_usd` counter cannot do
it: N simultaneous callers all read the same total and all proceed. So a call *reserves*
a pessimistic estimate, makes the call, then settles the real cost and releases the
difference. Available budget is `cap - settled - sum(held)`.

A `held` row older than the call timeout is a leak - a worker killed mid-call - so a
sweeper releases it. Hence `status` rather than deletion: a released reservation is
evidence that it happened, and the partial index on `(created_at) where status = 'held'`
is what makes the sweeper cheap.

`cost_usd numeric(12,6)`, not cents: a single call here costs well under a cent, and two
decimal places would record most of this demo as free.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

ORIGINS = ("llm", "cache", "spec", "manual")
RESERVATION_STATUSES = ("held", "settled", "released")


def upgrade() -> None:
    op.create_table(
        "mapping_proposals",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Unique, because it *is* the cache: a second request for the same file, layout,
        # schema, model and prompt version must find this row rather than pay again.
        sa.Column("cache_key", sa.Text(), nullable=False, unique=True),
        sa.Column("canonical_schema_version", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        # Nullable: a proposal served from a spec or typed by a human has no model.
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("effort", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("finish_reason", sa.Text(), nullable=True),
        sa.Column("proposal", postgresql.JSONB(), nullable=False),
        # What the model said that we refused to use, and why. Stored rather than only
        # logged: the UI tells the user, and "the model named a column you do not have"
        # is a fact about the proposal, not an operational detail.
        sa.Column(
            "dropped_fields",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "origin in (" + ", ".join(f"'{origin}'" for origin in ORIGINS) + ")",
            name="mapping_proposals_origin_check",
        ),
        sa.CheckConstraint("cost_usd >= 0", name="mapping_proposals_cost_check"),
        sa.CheckConstraint(
            "jsonb_typeof(proposal) = 'object'", name="mapping_proposals_proposal_object_check"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dropped_fields) = 'array'",
            name="mapping_proposals_dropped_array_check",
        ),
    )
    op.create_index(
        "ix_mapping_proposals_file_created",
        "mapping_proposals",
        ["source_file_id", sa.text("created_at desc")],
    )

    op.create_table(
        "llm_spend",
        # One row per UTC day, keyed by the day itself: the cap is daily, and a primary
        # key on the date is what makes "today's row, created if absent" one upsert.
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("settled_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("settled_usd >= 0", name="llm_spend_settled_check"),
        sa.CheckConstraint("call_count >= 0", name="llm_spend_call_count_check"),
    )

    op.create_table(
        "llm_spend_reservations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "source_file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in (" + ", ".join(f"'{s}'" for s in RESERVATION_STATUSES) + ")",
            name="llm_spend_reservations_status_check",
        ),
        # Strictly positive: a zero reservation would hold no budget while looking like it
        # did, which is the one failure mode this table exists to prevent.
        sa.CheckConstraint("reserved_usd > 0", name="llm_spend_reservations_amount_check"),
        sa.CheckConstraint(
            "(status = 'held') = (resolved_at is null)",
            name="llm_spend_reservations_resolved_pair_check",
        ),
    )
    op.create_index(
        "ix_llm_spend_reservations_day_status", "llm_spend_reservations", ["day", "status"]
    )
    # Partial: the sweeper only ever asks for old *held* rows, and this keeps that query
    # proportional to the number of in-flight calls rather than to all history.
    op.create_index(
        "ix_llm_spend_reservations_held_created",
        "llm_spend_reservations",
        ["created_at"],
        postgresql_where=sa.text("status = 'held'"),
    )


def downgrade() -> None:
    op.drop_index("ix_llm_spend_reservations_held_created", table_name="llm_spend_reservations")
    op.drop_index("ix_llm_spend_reservations_day_status", table_name="llm_spend_reservations")
    op.drop_table("llm_spend_reservations")
    op.drop_table("llm_spend")
    op.drop_index("ix_mapping_proposals_file_created", table_name="mapping_proposals")
    op.drop_table("mapping_proposals")
