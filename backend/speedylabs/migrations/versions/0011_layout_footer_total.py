"""file_layouts: the trailer's declared row count and total

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07

A fixed-width export ends with a record stating what the file contains. P8's
`footer_total_mismatch` check is the comparison between that statement and the detail
lines, so the statement has to be captured when the file is read - it is the one moment the
whole file is in front of us.

Both columns are nullable **permanently**, and that is the design rather than a concession:

* a CSV has no trailer, so there is nothing to declare;
* a trailer in a shape `lib/parsers/trailer.py` does not recognise has no readable total,
  and the readiness check reports not-applicable with a reason instead of a delta from a
  number that was never a total;
* a layout written before this migration existed has nulls, which says exactly the right
  thing: nobody captured it.

No backfill. A migration must not read the upload directory, and re-parsing files is a job's
work, not a schema change's.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("file_layouts", sa.Column("footer_total", sa.Numeric(18, 2), nullable=True))
    op.add_column("file_layouts", sa.Column("footer_declared_rows", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "file_layouts_footer_declared_rows_check",
        "file_layouts",
        "footer_declared_rows is null or footer_declared_rows >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("file_layouts_footer_declared_rows_check", "file_layouts", type_="check")
    op.drop_column("file_layouts", "footer_declared_rows")
    op.drop_column("file_layouts", "footer_total")
