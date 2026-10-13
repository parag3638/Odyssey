"""rebate_programs: external_ref, the basis agreement, and the brand foreign key

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-07

`rebate_programs` was created by migration 0001 and **no row has ever been written to it**. P11 is
its first writer, so this is the moment to close three things that would each be a migration plus a
reseed afterwards.

The brand foreign key is the important one
------------------------------------------
A program has to find its lines, and it cannot do it by comparing its own spelling to
`canonical_lines.brand_manufacturer`. The ERP file carries `"BLC"`; `tests/mappings.py` maps that
column through `map_values`, so every committed-fixture path stores `"Bayline Creamery"`. A string
comparison between the two matches **zero lines on the demo run** and reports $0.00 - correctly
shaped, entirely wrong, and silent.

So a program stores the manufacturer's **name** and finds its lines through
`customer_manufacturers.brand_values`, which already lists every spelling. This foreign key on
`(customer_id, brand_manufacturer) -> customer_manufacturers (customer_id, name)` makes a program
keyed to a manufacturer the customer has no reference row for *unrepresentable* rather than
silently unmatchable. It also makes seed order load-bearing - reference rows before programs - and
the database is the right place for that to be enforced, not the order of two function calls.

`on delete restrict`, so removing a manufacturer from the reference list cannot orphan a program;
the fix is to remove the program first.

The other two
-------------
* `kind` and `uom_basis` are redundant - `per_case_volume` is `CS`, `per_lb_billback` is `LB` - and
  two columns that can disagree eventually will. The calculator keys off `uom_basis` because it
  names the basis directly; the constraint means reading either gives the same answer.
* `external_ref` is a deliberate reach forward into P12, whose importer matches
  `legacy_rebate_lines.program_external_ref` against a program and whose seeded CSV's `PROGRAM`
  column carries `PG-BLC-LB`. Adding it after P11 has seeded the table means reseeding every row;
  adding it now, while the table is empty, costs one line. Nullable, because a real customer's
  programs may have no external reference.

The table is empty, so none of these can fail on existing data. That is a fact about *this*
environment and not a general one - adding a validating constraint to a populated table is a
different operation with a different risk - so a test asserts the table is empty before this runs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rebate_programs", sa.Column("external_ref", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "rebate_programs_external_ref_key", "rebate_programs", ["customer_id", "external_ref"]
    )
    op.create_check_constraint(
        "rebate_programs_basis_agrees_check",
        "rebate_programs",
        "(kind = 'per_case_volume') = (uom_basis = 'CS')",
    )
    op.create_foreign_key(
        "rebate_programs_brand_fk",
        "rebate_programs",
        "customer_manufacturers",
        ["customer_id", "brand_manufacturer"],
        ["customer_id", "name"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("rebate_programs_brand_fk", "rebate_programs", type_="foreignkey")
    op.drop_constraint("rebate_programs_basis_agrees_check", "rebate_programs", type_="check")
    op.drop_constraint("rebate_programs_external_ref_key", "rebate_programs", type_="unique")
    op.drop_column("rebate_programs", "external_ref")
