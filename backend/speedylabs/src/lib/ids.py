"""UUIDv7 generation, monotonic within a process.

BACKEND.md §3 and §5 require UUIDv7: time-ordered, so ids cluster at the right-hand end
of the index instead of scattering writes the way v4 does. `uuid.uuid7` only lands in the
stdlib in 3.14 and we pin 3.12, so this implements RFC 9562 directly rather than adding a
dependency for forty lines of bit shifting.

The 12 `rand_a` bits hold a **monotonic counter**, not random data — RFC 9562 §6.2
method 1. Drawing them randomly (the obvious implementation) yields valid, unique,
conformant ids whose order *within a single millisecond* is random, which quietly costs
the ordering property that is the whole reason for choosing v7. At observed rates near
800 ids/ms that is not a theoretical concern.

Limits, stated rather than assumed:
- Monotonicity is **per process**. Two processes in the same millisecond order by
  millisecond only. Nothing in this project needs a cross-process total order; if
  something ever does, it needs a sequence in the database, not a bigger id.
- The counter is 12 bits, so 4096 ids per millisecond. Beyond that this spin-waits for
  the next millisecond rather than wrapping, because wrapping would break ordering.
- A backwards clock step (NTP) reuses the last timestamp and keeps counting, so ids stay
  ordered even though the embedded time briefly stops advancing.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid

_TS_MASK = (1 << 48) - 1
_COUNTER_MAX = (1 << 12) - 1
_RAND_B_MASK = (1 << 62) - 1

_lock = threading.Lock()
_last_ts_ms = -1
_counter = 0


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def uuid7() -> uuid.UUID:
    """A UUIDv7, strictly increasing within this process.

    Layout per RFC 9562 §5.7, most significant bit first:
        48 bits unix_ts_ms | 4 bits version (7) | 12 bits counter
        | 2 bits variant (0b10) | 62 bits rand_b
    """
    global _last_ts_ms, _counter

    with _lock:
        ts_ms = _now_ms()

        if ts_ms > _last_ts_ms:
            _last_ts_ms = ts_ms
            _counter = 0
        else:
            # Same millisecond, or the clock moved backwards. Either way, hold the
            # timestamp and advance the counter so order is preserved.
            if _counter >= _COUNTER_MAX:
                # Counter exhausted: wait for a genuinely new millisecond.
                while ts_ms <= _last_ts_ms:
                    ts_ms = _now_ms()
                _last_ts_ms = ts_ms
                _counter = 0
            else:
                _counter += 1
                ts_ms = _last_ts_ms

        counter = _counter

    value = (ts_ms & _TS_MASK) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62) & _RAND_B_MASK

    return uuid.UUID(int=value)


def timestamp_ms(value: uuid.UUID) -> int:
    """Read the embedded millisecond timestamp back out. Used by tests to prove ordering."""
    return value.int >> 80
