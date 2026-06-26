"""drop watchlist

The watchlist feature was removed. The table was originally created by
Base.metadata.create_all (not a prior migration), so we drop it idempotently.

Revision ID: c7e1a2f4b9d0
Revises: 24bedaea9eb0
Create Date: 2026-06-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e1a2f4b9d0'
down_revision: Union[str, Sequence[str], None] = '24bedaea9eb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS watchlist")


def downgrade() -> None:
    op.create_table(
        'watchlist',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=12), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('symbol'),
    )
