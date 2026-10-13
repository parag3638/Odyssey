"""customers.legacy_period_cutoff_day — the other legacy quirk production cannot infer

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-07

The `date_cutoff` cause is the legacy system posting one day's invoices into the following period -
in the seeded data, 31 March's lines paid in April. Attributing it needs to know **which day**, and
production has no way to work that out:

* The day is `generator/legacy.py::CUTOFF_DAY`, and production never imports the generator.
* "The last day of any month" is not a substitute: the legacy system shifted **only** March. A rule
  claiming every month-end line would invent a relocation in January and February, which breaks
  conservation on four periods rather than fixing two.
* Inferring it from the variance signs would let the rule claim whatever makes the arithmetic work,
  which is the opposite of an explanation.

So it is seeded, exactly as migration 0027 seeds `redistributor_rate`, and for the same reason: this
is a fact the customer tells you during onboarding ("our March close ran late, so the 31st landed in
April"), not a constant to hardcode or a pattern to guess.

On `customers` rather than on a program, because it is a property of one legacy *system* and applies
to every program that system paid.

**Nullable, and that is the degradation path.** Without it the rule claims nothing, those dollars
fall to residue, and the explained percentage drops honestly rather than resting on a guess.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("legacy_period_cutoff_day", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "legacy_period_cutoff_day")
