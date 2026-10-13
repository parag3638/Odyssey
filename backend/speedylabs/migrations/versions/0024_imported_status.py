"""The `imported` run status, for a legacy rebate export

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-07

A `legacy_rebate` run has no canonical rows, no readiness checklist, no drift comparison and no
rebate calculation. It is read, matched against the customer's programs, and finished. None of the
existing statuses can say that:

* `parsed` is **not** terminal - P3 demoted it deliberately - so reusing it would leave every legacy
  run looking unfinished, and making it terminal would make every ERP run terminal halfway through.
* `profiled` means a layout was inferred, which is exactly the thing P12.0 stopped happening to
  these files.
* `ready` means a materialised run passed its readiness checks. There is nothing to be ready for.

So one new value, which costs this migration plus a badge entry, and that is the right price for a
status vocabulary that maps 1:1 onto what the UI shows (`docs/decisions.md`, P10's `drift_blocked`).

`imported` joins `TERMINAL_STATUSES`: nothing follows it automatically. Computing variances is a
human action against an *ERP* run, taking this file as an input.

The downgrade settles `imported` → `parsed` before narrowing the constraint, because a narrowing
constraint must not fail on data the wider one allowed. 0020 set that precedent.
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

#: Frozen copies - a migration is a snapshot, not a reference to today's vocabulary.
STATUSES_BEFORE = (
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
    "drift_blocked",
)
STATUSES_AFTER = (*STATUSES_BEFORE, "imported")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _replace_status_check(values: tuple[str, ...]) -> None:
    op.drop_constraint("ingest_runs_status_check", "ingest_runs", type_="check")
    op.create_check_constraint(
        "ingest_runs_status_check", "ingest_runs", _in_list("status", values)
    )


def upgrade() -> None:
    _replace_status_check(STATUSES_AFTER)


def downgrade() -> None:
    op.execute("update ingest_runs set status = 'parsed' where status = 'imported'")
    _replace_status_check(STATUSES_BEFORE)
