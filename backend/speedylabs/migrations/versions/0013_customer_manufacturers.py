"""customer_manufacturers: the reference list the vendor checks need

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07

Two readiness checks need knowledge no export contains: whether a brand is one this
distributor only ever buys through a redistributor, and whether a brand is known at all. That
is the customer's own vendor master, so it lives here and is read per customer.

Why arrays of **values**, not ids
---------------------------------
A canonical row carries whatever the customer's mapping produced. `tests/mappings.py` maps
both the brand and the vendor column through `map_values`, so one customer's rows say `BLC`
where another's say `Bayline Creamery` - the choice is theirs, not ours. Each entry therefore
lists every spelling it may appear under, and matching is trimmed and case-folded because a
mapping may also `upper` a column.

Why there is no `customer_redistributors` table
-----------------------------------------------
The check is the narrow, provable one: a redistributor-only brand billed under its *own*
direct vendor value. "This vendor is not a known redistributor" would warn on every
unfamiliar vendor the day a customer's list is first loaded, and a checklist nobody reads is
worse than no checklist. Knowing which vendors are redistributors is not needed to make that
judgement, so the table that would hold them is not needed either.

A customer with no rows here is not an error: both checks then report `not_applicable` with a
reason, which is the truthful answer and the reason `readiness_check_runs` has that outcome.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

#: Spellings per entry. Twenty is generous for "code, name, and a few historical variants",
#: and bounded because this is data a seeder writes in bulk.
MAX_VALUES = 20


def upgrade() -> None:
    op.create_table(
        "customer_manufacturers",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("brand_values", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("direct_vendor_values", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "redistributor_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
        # One entry per manufacturer per customer, so a re-seed updates rather than doubles.
        sa.UniqueConstraint("customer_id", "name", name="customer_manufacturers_name_key"),
        sa.CheckConstraint(
            f"cardinality(brand_values) between 1 and {MAX_VALUES}",
            name="customer_manufacturers_brand_values_check",
        ),
        sa.CheckConstraint(
            f"cardinality(direct_vendor_values) <= {MAX_VALUES}",
            name="customer_manufacturers_vendor_values_check",
        ),
    )
    op.create_index("ix_customer_manufacturers_customer", "customer_manufacturers", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_manufacturers_customer", table_name="customer_manufacturers")
    op.drop_table("customer_manufacturers")
