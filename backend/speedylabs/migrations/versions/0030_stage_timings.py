"""ingest_runs.stage_timings — how long each stage actually took

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-07

A6 claims a time from raw upload to first variance report. The stored
`elapsed_ms_to_first_variance` answers that, but it is **wall clock**: measured on the demo run it
is 32,762 ms, of which only 3,666 ms is this software working and 26 ms is queue waiting. The other
88.7% is a human deciding - mapping, approving, materialising, choosing a legacy file. Both numbers
are true and they are not the same claim, so the dashboard needs the second one, and that means
recording what each stage cost.

**One jsonb column, not eight integers.** The stage list belongs to `jobs/tasks.py` and will grow
again before this project ends. Eight nullable integer columns make every new stage a migration;
one object makes it a key.

The shape, whose vocabulary is fixed here and nowhere else:

    {"parse": {"ms": 99, "attempts": 1}, "profile": {"ms": 1268, "attempts": 1}, ...}

`profile` covers the drift gate and map-from-spec because **one job does all three** - there is no
`map` key, because there is no map job to time. A stage that has not run is **absent**, never zero:
`{}` by default, so "did not run" and "ran instantly" never look alike.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingest_runs",
        sa.Column(
            "stage_timings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ingest_runs_stage_timings_object",
        "ingest_runs",
        "jsonb_typeof(stage_timings) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint("ingest_runs_stage_timings_object", "ingest_runs", type_="check")
    op.drop_column("ingest_runs", "stage_timings")
