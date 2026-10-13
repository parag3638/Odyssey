"""customers, rebate_programs, and procrastinate's queue

Revision ID: 0001
Revises:
Create Date: 2026-10-04

procrastinate's own schema is applied *inside* this migration rather than by
`procrastinate schema --apply`. One history, one command: a deploy that runs
`alembic upgrade head` cannot end up with application tables present and the queue
missing, which is the failure a separate schema step invites.
"""

from __future__ import annotations

import re
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from procrastinate.schema import SchemaManager

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# procrastinate ships no teardown, and its schema is 4 tables, 3 types, 18 functions and
# 7 triggers. Enumerating 18 function signatures here would break silently on the next
# procrastinate upgrade, so the downgrade drops whatever `procrastinate*` functions the
# catalog actually holds. Tables go first with CASCADE, which takes the triggers and
# indexes with them.
_DROP_PROCRASTINATE_FUNCTIONS = """
do $$
declare
    r record;
begin
    for r in
        select p.oid::regprocedure as signature
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = current_schema()
          and p.proname like 'procrastinate%'
    loop
        execute 'drop function if exists ' || r.signature || ' cascade';
    end loop;
end
$$;
"""


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    """BACKEND.md §5: every table carries created_at and updated_at as timestamptz."""
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
        "customers",
        # No server default: ids are UUIDv7 from src.lib.ids, so they are time-ordered.
        # gen_random_uuid() would be v4 and scatter index writes.
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        *_timestamps(),
    )

    op.create_table(
        "rebate_programs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("brand_manufacturer", sa.Text(), nullable=False),
        # numeric, never float: BACKEND.md §5 and the spec's core principle.
        sa.Column("rate", sa.Numeric(18, 6), nullable=False),
        sa.Column("uom_basis", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        *_timestamps(),
        # Constraints live in the database, not only in code (BACKEND.md §5): these must
        # hold even for a row written by a migration or by hand.
        sa.CheckConstraint(
            "kind in ('per_case_volume','per_lb_billback')",
            name="rebate_programs_kind_check",
        ),
        sa.CheckConstraint("rate > 0", name="rebate_programs_rate_check"),
        sa.CheckConstraint("uom_basis in ('CS','LB')", name="rebate_programs_uom_basis_check"),
        sa.CheckConstraint(
            "effective_to is null or effective_to >= effective_from",
            name="rebate_programs_effective_range_check",
        ),
        sa.UniqueConstraint("customer_id", "name", name="rebate_programs_customer_name_key"),
    )
    # Every foreign key gets an index (BACKEND.md §5).
    op.create_index("ix_rebate_programs_customer_id", "rebate_programs", ["customer_id"])

    # The queue, in the same transaction as the tables above.
    schema_sql = SchemaManager.get_schema()
    if not re.search(r"procrastinate_jobs", schema_sql):
        raise RuntimeError("procrastinate schema SQL looks wrong; refusing to apply it")
    op.execute(schema_sql)


def downgrade() -> None:
    # Literal statements, not f-strings. The identifiers are compile-time constants, so
    # interpolation added no flexibility and did trip semgrep's formatted-sql-query rule -
    # a false positive, but "no formatted SQL anywhere" is a cheaper rule to keep than one
    # with an exception for constants a reviewer has to verify by hand.
    op.execute(
        "drop table if exists "
        "procrastinate_events, procrastinate_periodic_defers, "
        "procrastinate_jobs, procrastinate_workers "
        "cascade"
    )
    op.execute(_DROP_PROCRASTINATE_FUNCTIONS)
    op.execute(
        "drop type if exists "
        "procrastinate_job_to_defer_v1, procrastinate_job_event_type, "
        "procrastinate_job_status "
        "cascade"
    )

    op.drop_index("ix_rebate_programs_customer_id", table_name="rebate_programs")
    op.drop_table("rebate_programs")
    op.drop_table("customers")
