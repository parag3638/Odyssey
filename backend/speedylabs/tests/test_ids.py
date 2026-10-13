"""UUIDv7 per RFC 9562 §5.7 (BACKEND.md §3, §5: "time-ordered, index-friendly" ids).

Covers the four properties called out as worth checking independently: the version
nibble, the variant bits, the embedded timestamp against wall clock, and whether ids
generated back-to-back sort in creation order as strings - the property the
"index-friendly" claim rests on.
"""

from __future__ import annotations

import time
import uuid

from src.lib.ids import timestamp_ms, uuid7


def test_version_nibble_is_7() -> None:
    for _ in range(100):
        assert uuid7().version == 7


def test_variant_bits_are_rfc_4122() -> None:
    for _ in range(100):
        assert uuid7().variant == uuid.RFC_4122


def test_embedded_timestamp_matches_wall_clock() -> None:
    before_ms = int(time.time() * 1000)
    value = uuid7()
    after_ms = int(time.time() * 1000)

    assert before_ms <= timestamp_ms(value) <= after_ms


def test_ids_generated_back_to_back_sort_in_creation_order_as_strings() -> None:
    """The whole reason BACKEND.md §5 picks UUIDv7 over v4: new rows cluster at the end
    of the index instead of scattering writes across it. That only holds if ids
    created in sequence compare in the order they were created.

    Modern hardware generates thousands of ids within a single millisecond tick, so
    this is not a contrived scenario - it is the common case under real traffic, not
    an edge case.
    """
    ids = [str(uuid7()) for _ in range(5000)]

    assert ids == sorted(ids)
