"""The `Idempotency-Key` store: one home for "has this request already happened?".

BACKEND.md §3 requires actions with side effects to accept the header and answer a repeat with
the same result rather than doing the work twice. This is the mechanism, in `lib/` because the
propose route is the first user and P11's orders are the next - and because a second copy of
this logic is how two routes start disagreeing about what a key means.

Three states, and the middle one is the one a naive implementation gets wrong:

* **new** - nothing has used this key. The caller claims it, does the work, and records the
  answer.
* **reused for a different request** - the same key arrives with a different fingerprint. This
  is refused (`409`), because replaying the first request's answer would hand the caller a
  result belonging to something they did not ask for. It is a client bug, and a silent merge
  would hide it.
* **the same request again** - the fingerprint matches. If the first attempt recorded an
  answer, it is replayed. If it did not (it failed), the work runs again: a claim with no
  answer is not a side effect worth protecting.

A key is **client-supplied, untrusted input**: length-checked, treated as an opaque string, and
never interpolated anywhere. It is stored, so it must not be secret - the header is a
correlation id, not a credential.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.errors import Conflict, MalformedRequest

#: The longest key accepted. Long enough for a UUID or a hash, short enough that a hostile
#: client cannot post a megabyte of header into a row. Matches the DB's own check.
MAX_KEY_LENGTH = 200

_SELECT = text(
    """
    select request_fingerprint, response_ref from idempotent_requests
    where route = :route and key = :key
    """
)

#: `do nothing` on conflict: two concurrent first attempts with the same key race here, and the
#: loser simply reads the row the winner wrote on its next statement.
_CLAIM = text(
    """
    insert into idempotent_requests (route, key, request_fingerprint)
    values (:route, :key, :fingerprint)
    on conflict (route, key) do nothing
    """
)

_FULFIL = text(
    """
    update idempotent_requests set response_ref = :response_ref
    where route = :route and key = :key
    """
)


def clean_key(raw: str | None) -> str | None:
    """A usable key, or `None` when the client sent no header.

    A present-but-unusable key is a `400`, not a silent ignore: a client that sends a
    10,000-character key believes it is getting idempotency, and quietly not providing it
    would be the same false promise this table exists to end.
    """
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > MAX_KEY_LENGTH:
        raise MalformedRequest(
            f"The Idempotency-Key header must be {MAX_KEY_LENGTH} characters or fewer."
        )
    return key


@dataclass(frozen=True)
class Claim:
    """What the caller should do. `replay_ref` is set only when there is an answer to serve."""

    replay_ref: str | None


async def claim(session: AsyncSession, *, route: str, key: str, fingerprint: str) -> Claim:
    """Reserve this key for this request, or hand back the answer it already produced.

    Raises `Conflict` when the key was used for a different request.
    """
    await session.execute(_CLAIM, {"route": route, "key": key, "fingerprint": fingerprint})
    row = (await session.execute(_SELECT, {"route": route, "key": key})).first()
    if row is None:
        # The insert above guarantees a row; a missing one means something else deleted it
        # between the two statements, and treating that as "new" is the safe reading.
        return Claim(replay_ref=None)

    stored_fingerprint, response_ref = str(row[0]), row[1]
    if stored_fingerprint != fingerprint:
        raise Conflict(
            "This Idempotency-Key has already been used for a different request. Use a new "
            "key, or repeat the original request unchanged."
        )
    return Claim(replay_ref=None if response_ref is None else str(response_ref))


async def fulfil(session: AsyncSession, *, route: str, key: str, response_ref: str) -> None:
    """Record what this key answered. Safe to call twice."""
    await session.execute(_FULFIL, {"route": route, "key": key, "response_ref": response_ref})
