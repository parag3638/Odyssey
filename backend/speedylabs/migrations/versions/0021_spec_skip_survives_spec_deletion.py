"""ingest_runs: a recorded skip survives its spec being deleted

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-07

Migration 0017 added two things that cannot both hold:

* `considered_spec_id` is `on delete set null`, so deleting a spec nulls the reference rather than
  deleting the run - correct, because a run is a record of what happened and must outlive the
  documents it mentions;
* `ingest_runs_spec_skip_needs_spec_check` requires `spec_skip_code is null or considered_spec_id is
  not null`.

So deleting a `mapping_specs` row fires the `set null`, which then violates the check - and the
delete fails. A spec became undeletable the moment any run recorded a skip against it. Found while
resetting state for Phase 10's acceptance test, and it would have reached Phase 15's demo reset,
which has to clear specs between visitors.

The check is the half that is wrong. Its intent was "a skip code with no spec is a state nobody can
act on", and that is true at *write* time - the job always knows which spec it skipped. But
after the
spec is gone, `spec_skip_code` plus `spec_skip_detail` ("`SHPUNT` is missing") is still exactly the
truth about that run, and the thing that is no longer knowable is which document said so. Forcing
that pair to be erased together would destroy the more useful half to protect a constraint about the
less useful one.

`ingest_runs_spec_skip_code_check` stays: the code itself is still a closed vocabulary.
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ingest_runs_spec_skip_needs_spec_check", "ingest_runs", type_="check")


def downgrade() -> None:
    # Re-creating it would fail on any row the application has legitimately produced since, so the
    # rows are reconciled first - dropping the skip code is the only direction available, and it
    # loses the less useful half of the pair.
    op.execute(
        "update ingest_runs set spec_skip_code = null, spec_skip_detail = null "
        "where considered_spec_id is null and spec_skip_code is not null"
    )
    op.create_check_constraint(
        "ingest_runs_spec_skip_needs_spec_check",
        "ingest_runs",
        "spec_skip_code is null or considered_spec_id is not null",
    )
