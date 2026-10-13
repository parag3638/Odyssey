"""ingest_runs: allow the 'materializing' status

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-07

The status vocabulary is guarded by a check constraint, so adding a status is a migration
rather than an edit to a `Literal`. That is the constraint doing its job: Phase 7's first
materialise attempt was refused by the database, not discovered later by a reader of a
status nobody recognised.

Additive, per BACKEND.md §3: `materializing` joins the set, nothing is renamed or removed,
and no existing row changes.
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

#: Frozen copies. A migration is a snapshot: importing the application's current list would
#: make this file mean something different after the next status is added.
STATUSES_BEFORE = (
    "queued",
    "parsing",
    "parsed",
    "profiled",
    "mapped",
    "materialized",
    "ready",
    "blocked",
    "rejected",
    "failed",
)
STATUSES_AFTER = (
    "queued",
    "parsing",
    "parsed",
    "profiled",
    "mapped",
    "materializing",
    "materialized",
    "ready",
    "blocked",
    "rejected",
    "failed",
)


def _replace_check(values: tuple[str, ...]) -> None:
    op.drop_constraint("ingest_runs_status_check", "ingest_runs", type_="check")
    op.create_check_constraint(
        "ingest_runs_status_check",
        "ingest_runs",
        "status in (" + ", ".join(f"'{value}'" for value in values) + ")",
    )


def upgrade() -> None:
    _replace_check(STATUSES_AFTER)


def downgrade() -> None:
    # A run caught mid-materialise would fail the narrower constraint, so settle it first.
    # `profiled` rather than `materialized`: it has no rows, and claiming otherwise would
    # leave a run that says it has output and has none.
    op.execute("update ingest_runs set status = 'profiled' where status = 'materializing'")
    _replace_check(STATUSES_BEFORE)
