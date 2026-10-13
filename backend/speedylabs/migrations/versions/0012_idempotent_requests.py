"""idempotent_requests: honour the Idempotency-Key header

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07

BACKEND.md §3: "Actions with side effects (orders, payments, bot starts) accept an
`Idempotency-Key` header. Same key = same result, never a duplicate." The header was listed
in CORS and documented as honoured, and nothing read it - the P6 tester found the gap and
proved it structurally. A paid model call is exactly such an action.

What the table is, and is not
-----------------------------
One row per `(route, key)`, holding the request's fingerprint and a **reference** to the answer
that key produced, not the answer itself. The propose route's answer is already stored,
content-addressed, in `mapping_proposals.cache_key`; duplicating the payload here would be a
second copy that
could disagree with the first. `response_ref` is opaque to this table - the route that wrote
it knows how to resolve it.

Keyed by `(route, key)` rather than by the key alone. §3 does not say keys are global, and a
per-route scope is the conservative reading: a client reusing one key across two different
actions gets both actions, not the first one's answer twice.

Not an expiry mechanism. Rows are small and a sweep is a later concern; when one is added it
belongs beside the spend-reservation sweep, not in a migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotent_requests",
        # The route's own name, not a URL: a path carries ids, and two requests to
        # different files are different requests whatever key they send.
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        # What the key was used *for*: enough of the request to tell "this is my retry" from
        # "I reused a key by mistake". Reusing one key for a different request must not replay
        # the first request's answer - that would hand the caller somebody else's result - so
        # a mismatch is refused rather than served.
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        # How the route finds what it answered last time. Opaque here on purpose. **Nullable**:
        # the key is claimed before the work runs, so a request that failed leaves a claim with
        # no answer, and a retry is allowed to do the work rather than replaying a failure.
        sa.Column("response_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("route", "key", name="idempotent_requests_pkey"),
        # A client-supplied key is untrusted input: capped here as well as at the edge, so
        # a 2 MB header cannot become a 2 MB row.
        sa.CheckConstraint(
            "length(key) between 1 and 200", name="idempotent_requests_key_length_check"
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotent_requests")
