"""ingest_runs and file_layouts: what a run did with the approved spec

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-07

A3 is "approve a mapping once, and the next file maps itself with no LLM call". That has to be
**queryable**, not a log line, or the acceptance test is reading prose.

Three states, legible from two columns:

| `considered_spec_id` | `spec_skip_code` | meaning                                              |
|----------------------|------------------|------------------------------------------------------|
| null                 | null             | no approved spec existed for this customer and kind  |
| set                  | null             | the spec was applied                                 |
| set                  | set              | found, did not fit; the detail says how              |

The check constraint refuses a skip code with no spec, because "something did not fit" with
nothing named is a state nobody can act on.

Why there is no `mapping_origin` column
---------------------------------------
mvp.md proposes one, with values `spec|llm|cache|manual`. A run whose spec covered fifteen fields
and whose sixteenth a human typed in has no honest single value, and any precedence rule we
invented would be a lie stored in a column. Per-field `origin` already answers the question at
the only granularity where it has an answer, and `considered_spec_id` answers "was a spec
applied". **"No LLM call" is provable from `mapping_proposals` having no row for the new
`source_file_id`** - a new file has a new digest, so a new cache key, so any call would have left
one.

`fields_from_spec_id` is set only when the layout's `fields` actually came from the spec, which is
the fixed-width path. A CSV layout keeps null even on a run where the spec was applied, because a
CSV's columns come from its own header row and claiming otherwise would credit a document that did
not produce them.

No backfill. Every existing run reads as "no spec existed", which is true.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

#: Why a spec that existed was not applied. A closed set: each value is a different thing to tell
#: a user, and an open text column would become a dumping ground for messages.
SKIP_CODES = (
    "schema_version_changed",
    "missing_source_columns",
    "record_width_changed",
    "spec_unreadable",
)


def upgrade() -> None:
    op.add_column(
        "ingest_runs",
        sa.Column(
            "considered_spec_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("mapping_specs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("ingest_runs", sa.Column("spec_skip_code", sa.Text(), nullable=True))
    op.add_column("ingest_runs", sa.Column("spec_skip_detail", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "ingest_runs_spec_skip_code_check",
        "ingest_runs",
        "spec_skip_code is null or spec_skip_code in ("
        + ", ".join(f"'{code}'" for code in SKIP_CODES)
        + ")",
    )
    op.create_check_constraint(
        "ingest_runs_spec_skip_needs_spec_check",
        "ingest_runs",
        "spec_skip_code is null or considered_spec_id is not null",
    )
    op.create_index("ix_ingest_runs_considered_spec", "ingest_runs", ["considered_spec_id"])

    op.add_column(
        "file_layouts",
        sa.Column(
            "fields_from_spec_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("mapping_specs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("file_layouts", "fields_from_spec_id")
    op.drop_index("ix_ingest_runs_considered_spec", table_name="ingest_runs")
    op.drop_constraint("ingest_runs_spec_skip_needs_spec_check", "ingest_runs", type_="check")
    op.drop_constraint("ingest_runs_spec_skip_code_check", "ingest_runs", type_="check")
    op.drop_column("ingest_runs", "spec_skip_detail")
    op.drop_column("ingest_runs", "spec_skip_code")
    op.drop_column("ingest_runs", "considered_spec_id")
