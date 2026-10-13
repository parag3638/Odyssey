"""rebate_programs.redistributor_rate — the commercial term P13's rule 4 needs

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-07

The `redistributor_misattribution` cause is the legacy system looking a program up by vendor number,
landing on a redistributor agreement, and paying that agreement's rate instead of the brand's own.
Pricing that cause needs the redistributor's rate, and **production has no way to know it**: the
number exists only as `src/generator/catalog.py::REDISTRIBUTOR_RATE`, and production never imports
the generator (a test pins that).

Measured consequence of leaving it out: the explained percentage falls from 99.81% to **88.43%** -
below A5's bar, for a missing input rather than a broken rule.

So it becomes seeded commercial data on the program, by the route `customer_manufacturers` and the
programs themselves already take: the generator emits it into the committed fixture and
`seed_demo.py` loads it. One nullable column, no backfill, no index - it is read once per
attribution run by the same query the rules already use.

**Nullable is the degradation path, not an oversight.** A customer who has not told us their
redistributor terms gets no dollars attributed to that cause and a lower explained percentage,
which is the honest answer rather than a guessed one.

`>= 0` and not `> 0`: a redistributor agreement paying nothing is representable, and a zero rate
makes the shortfall the whole program rate, which is correct arithmetic.

Downgrading drops the column. Attribution rows that depended on it are **not** deleted - they are
historical statements about a comparison that was run - but that cause's dollars become
unreproducible afterwards, which is the honest consequence of removing the input.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rebate_programs", sa.Column("redistributor_rate", sa.Numeric(12, 6), nullable=True)
    )
    op.create_check_constraint(
        "rebate_programs_redistributor_rate_check",
        "rebate_programs",
        "redistributor_rate is null or redistributor_rate >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("rebate_programs_redistributor_rate_check", "rebate_programs", type_="check")
    op.drop_column("rebate_programs", "redistributor_rate")
