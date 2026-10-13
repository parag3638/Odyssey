"""Opaque pagination cursors. One home for the encoding and for its refusal.

The cursor is base64url of the key's parts joined by `|`. Opaque rather than readable so
clients cannot construct one by hand and come to depend on its shape - §3 says changes are
additive, and a cursor format callers parse is a format we can never change.

A malformed cursor is a `400 malformed_request`, never an attempt to interpolate it. That
is the whole answer to `cursor=';DROP TABLE source_files;--'`: it is decoded and converted
into typed values before any query sees it, so there is nothing to inject into. The
offending value is deliberately never echoed back - it is caller-controlled and has no
business appearing in a response.

Here rather than in a feature because P7's canonical preview was the second paginated list
and copying the second one is how a format starts to diverge (BACKEND.md §9, rule of two).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable

from src.lib.errors import MalformedRequest


def encode(*parts: str) -> str:
    """The parts as one opaque token. Padding is stripped so the token is URL-clean."""
    raw = "|".join(parts).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode[T](value: str, *, parse: Callable[[list[str]], T], message: str) -> T:
    """Decode and convert, or raise `MalformedRequest` with the caller's message.

    `parse` does the type conversion, so "this is not a valid cursor" is decided in one
    place whether the token was corrupt base64 or carried a row number that is not a
    number.
    """
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode("ascii")
        return parse(raw.split("|"))
    except (binascii.Error, UnicodeDecodeError, ValueError, IndexError) as exc:
        raise MalformedRequest(message) from exc
