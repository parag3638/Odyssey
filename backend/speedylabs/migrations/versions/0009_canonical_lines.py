"""canonical_lines, materialization_errors

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07

The output of the whole pipeline: one row per invoice line, in the shape
`canonical/invoice_line.schema.json` publishes, plus a table for the rows that could not
be made to fit.

**Rejected rows are recorded, not dropped.** A quietly short output file is the worst
failure mode a data-onboarding tool has: the numbers all reconcile, against the wrong
total. So a row whose transform fails lands in `materialization_errors` with the field, the
raw value and the reason, and the run reports the count.

### `customer_id` is the ERP's code, not a foreign key

`canonical_lines.customer_id` is text, and it is the distributor's own customer number -
deliberately the same name the spec's canonical schema uses. It is **not** a reference to
our `customers` table; the run reaches our customer through `ingest_run_id`. Joining the
two by name would silently associate a distributor's account numbering with our own, so
the collision is called out here, in `canonical/FORMAT.md`, and in a check constraint's
absence: there is no foreign key precisely because there must not be one.

### Nullability is the canonical model's, not a judgement made here

A column is `not null` exactly when `CanonicalInvoiceLine` gives the field no default -
the same derivation `REQUIRED_FIELD_NAMES` makes. Being laxer here would be a second,
quieter opinion about what a canonical line is: the mapping routes already refuse to
materialise a file that leaves a required field unmapped, so a null arriving at this table
means a mapped column was blank on that line. That row is incomplete, and it is rejected
and reported rather than stored as a line nobody can attribute a rebate to.

`pack_size` is an integer because the schema says eaches per case; a fractional each is not
a thing.

### Scales

`qty` and the weights carry 4 decimals, `unit_price` 6, `ext_amount` 2. The published
schema allows 3/4/2; the table is wider than the contract on purpose, because arriving data
may carry more precision than we promise to re-export and truncating at write time would
lose evidence of what the file actually said.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UOM_VALUES = ("CS", "EA", "LB")


def upgrade() -> None:
    op.create_table(
        "canonical_lines",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "ingest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The physical line in the source file, so every canonical row can be pointed back
        # at the thing it came from. Half the product is "drill down to source rows".
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("invoice_no", sa.Text(), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        # The ERP's customer code. Not a foreign key - see the module docstring.
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("customer_name", sa.Text(), nullable=False),
        sa.Column("dist_item_id", sa.Text(), nullable=False),
        sa.Column("mfr_item_code", sa.Text(), nullable=False),
        sa.Column("brand_manufacturer", sa.Text(), nullable=False),
        sa.Column("invoice_vendor", sa.Text(), nullable=False),
        sa.Column("qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("uom", sa.Text(), nullable=False),
        sa.Column("pack_size", sa.Integer(), nullable=True),
        sa.Column("catch_weight_lbs", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("ext_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("is_deviated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deviated_price", sa.Numeric(18, 6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "uom in (" + ", ".join(f"'{value}'" for value in UOM_VALUES) + ")",
            name="canonical_lines_uom_check",
        ),
        sa.CheckConstraint(
            "pack_size is null or pack_size > 0", name="canonical_lines_pack_size_check"
        ),
        sa.CheckConstraint(
            "catch_weight_lbs is null or catch_weight_lbs >= 0",
            name="canonical_lines_catch_weight_check",
        ),
        # A deviated line without its deviated price is a line nobody can audit. The rule
        # lives in the schema because it must always hold (BACKEND.md §1).
        sa.CheckConstraint(
            "not is_deviated or deviated_price is not null",
            name="canonical_lines_deviated_price_check",
        ),
        sa.CheckConstraint("source_row_number >= 1", name="canonical_lines_row_number_check"),
        # What makes re-materialising idempotent: a second pass writes the same keys.
        sa.UniqueConstraint(
            "ingest_run_id", "source_row_number", name="canonical_lines_run_row_key"
        ),
    )
    # One index per thing a later phase groups or filters by: P8 counts issues per date and
    # item, P13 attributes variance per vendor and customer.
    for column in ("invoice_date", "dist_item_id", "invoice_vendor", "customer_id"):
        op.create_index(
            f"ix_canonical_lines_run_{column}", "canonical_lines", ["ingest_run_id", column]
        )

    op.create_table(
        "materialization_errors",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "ingest_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingest_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("canonical_field", sa.Text(), nullable=False),
        # The value as the file wrote it, truncated by the writer. Kept because "which
        # value?" is the first question anybody asks about a rejected row.
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_materialization_errors_run", "materialization_errors", ["ingest_run_id"])
    op.create_index(
        "ix_materialization_errors_run_row",
        "materialization_errors",
        ["ingest_run_id", "source_row_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_materialization_errors_run_row", table_name="materialization_errors")
    op.drop_index("ix_materialization_errors_run", table_name="materialization_errors")
    op.drop_table("materialization_errors")
    for column in ("customer_id", "invoice_vendor", "dist_item_id", "invoice_date"):
        op.drop_index(f"ix_canonical_lines_run_{column}", table_name="canonical_lines")
    op.drop_table("canonical_lines")
