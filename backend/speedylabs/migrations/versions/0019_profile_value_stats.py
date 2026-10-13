"""column_profiles: the two statistics drift needs and the profiler did not keep

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-07

mvp.md's Phase 10 says drift "reads only derived profile data". That is not true of the schema as it
stood: `column_profiles` holds `type_guess`, `null_pct`, `distinct_count`, `distinct_capped`,
`min_text`, `max_text`, `date_format`, `sample_values` and `patterns`, and none of it can answer
either distribution question.

* A **mix share** needs per-value counts. `sample_values` is five arbitrary values and
  `distinct_count` is a cardinality.
* A **mean** needs a numeric sum. `min_text` and `max_text` are *lexicographic on truncated text*
  and carry no arithmetic at all.

The honest options were to extend the profiler or to re-read rows at drift time. Re-reading is
rejected twice over: it is O(rows) on the request path, and it needs the **baseline file's bytes**,
which `approved_from_source_file_id`'s `on delete set null` says may be gone. The profiler already
visits every value, so both statistics are free there and bounded there.

`value_counts` is written only when a column has at most 25 distinct non-null values, so it is
bounded by the cap and not by the row count. The enumeration test is 20, and the 5 of headroom is
deliberate: a column with 20-25 distinct values can then answer "not an enumeration" *from recorded
data* rather than from an absence.

`numeric_sum` is `numeric` with no precision, because the sum of 250,000 eleven-digit
values does not fit `numeric(18,2)`. The mean is computed at read time; storing the mean
would throw away the count a reader needs to trust it.

All three nullable forever, and the two null cases are distinguishable without ambiguity:
`value_counts is null` with `distinct_count >= 25` is a legitimately capped column (so the
enumeration check answers `clean` - "not an enumeration"), while `value_counts is null` with
`distinct_count < 25` is a profile written before this migration (so it answers `not_comparable`,
naming that). No backfill: re-profiling is a user action and a migration must not read
upload storage.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("column_profiles", sa.Column("value_counts", postgresql.JSONB(), nullable=True))
    op.add_column("column_profiles", sa.Column("numeric_count", sa.Integer(), nullable=True))
    # No precision on purpose - see the module docstring.
    op.add_column("column_profiles", sa.Column("numeric_sum", sa.Numeric(), nullable=True))
    op.create_check_constraint(
        "column_profiles_numeric_count_check",
        "column_profiles",
        "numeric_count is null or numeric_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("column_profiles_numeric_count_check", "column_profiles", type_="check")
    op.drop_column("column_profiles", "numeric_sum")
    op.drop_column("column_profiles", "numeric_count")
    op.drop_column("column_profiles", "value_counts")
