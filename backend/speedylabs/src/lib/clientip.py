"""Resolving the client IP, and the one hash we are allowed to keep.

There is no sign-in, so a per-IP rate limit is the whole protection posture. That makes
getting the client's address right a security property rather than a convenience.

**`TRUSTED_PROXY_HOPS` is a count, not an allowlist.** `0` ignores `X-Forwarded-For`
entirely and uses the socket peer, which is correct locally. `n` takes the **n-th entry
from the right** of the header.

Counting from the right is what makes it spoof-resistant. A client can send any
`X-Forwarded-For` it likes, and a trusted proxy *appends* its view of the peer to the
end - so the attacker's values land to the left of the real ones. Counting leftwards
(`[0]`, the usual mistake) reads whatever the client typed; counting rightwards skips
every attacker-controlled entry. A CIDR allowlist would be stronger, and was the
original design; it was dropped because the deploy target publishes no proxy ranges.

Only a salted hash is ever stored or logged. `BACKEND.md` §4: logs carry no personal
data beyond ids, and a raw address is personal data.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

from fastapi import Request

XFF_HEADER: Final = "x-forwarded-for"

#: What to attribute a request to when there is no peer at all (an ASGI transport with
#: no client, which happens in tests). A fixed sentinel so such requests share one
#: bucket rather than silently bypassing the limiter.
UNKNOWN_CLIENT: Final = "unknown"


def resolve_client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """The caller's address, as far as we are willing to believe it."""
    peer = request.client.host if request.client else UNKNOWN_CLIENT

    if trusted_proxy_hops <= 0:
        # No proxy in front: the header is pure client input and is ignored.
        return peer

    forwarded = request.headers.get(XFF_HEADER)
    if not forwarded:
        # Configured for a proxy but the header is absent. Fall back to the peer rather
        # than failing the request: a missing header is a deployment problem to notice
        # in logs, not a reason to reject a visitor.
        return peer

    entries = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not entries:
        return peer

    # n-th from the right. If the list is shorter than the configured hop count, the
    # leftmost entry is the best available answer - and it means the deployment has
    # fewer hops than configured, which is worth noticing but not worth a 500.
    index = min(trusted_proxy_hops, len(entries))
    return entries[-index]


def hash_ip(ip: str, salt: str) -> str:
    """A salted hash, so the raw address never reaches the database or the logs.

    HMAC rather than a plain salted digest: the salt is a key here, not a per-record
    nonce, and HMAC is the construction built for that. The output is truncated to 32
    hex characters - enough that two addresses will not collide in a demo's lifetime,
    and short enough to read in a log line.
    """
    return hmac.new(salt.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def client_ip_hash(request: Request, trusted_proxy_hops: int, salt: str) -> str:
    return hash_ip(resolve_client_ip(request, trusted_proxy_hops), salt)
