"""mapping_drafts: record which mapping the verdicts were computed from

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-07

`mapping_validations` rows are keyed to a draft, and nothing recorded *which* mapping produced
them. So: validate a mapping, change one field's source column, and the stored `pass` verdicts
stand - describing a mapping that no longer exists.

That was survivable while verdicts were advice on a screen. Phase 9 makes them a
**precondition**: a spec is promoted from a validated draft, and the spec then governs every
future file from that customer. Approving on stale verdicts would carry a mapping nobody
validated into the document that decides how the next twelve months are read.

One nullable column closes it. `validate_mapping` writes the sha256 of the canonical JSON of
the mapping it sampled, in the same transaction as the verdicts; approval refuses with
`422 validation_stale` when the stored hash no longer matches the draft's current mapping, and
`GET .../mapping` publishes `validationsStale` so the UI can say so before anybody clicks.

Nullable forever: a draft nobody has validated has nothing to record, and that is the truthful
answer rather than a sentinel.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

#: A sha256 as lower-case hex.
SHA256_LENGTH = 64


def upgrade() -> None:
    op.add_column("mapping_drafts", sa.Column("validated_sha256", sa.Text(), nullable=True))
    op.create_check_constraint(
        "mapping_drafts_validated_sha256_check",
        "mapping_drafts",
        f"validated_sha256 is null or char_length(validated_sha256) = {SHA256_LENGTH}",
    )


def downgrade() -> None:
    op.drop_constraint("mapping_drafts_validated_sha256_check", "mapping_drafts", type_="check")
    op.drop_column("mapping_drafts", "validated_sha256")
