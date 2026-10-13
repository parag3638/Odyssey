"""Measuring what a stage cost, without letting the measurement break the stage.

One pattern, following `lib/llm.py`'s existing one: `time.monotonic_ns()` around the work, integer
milliseconds out. **Monotonic**, so a clock adjustment mid-stage cannot produce a negative
duration - which matters because `elapsed_ms_to_first_variance` carries a `>= 0` check constraint
and a wall-clock subtraction across a daylight-saving boundary would violate it.

**The timing is merged into the `update` the stage already issues**, never a second statement and
never a second transaction. There is therefore no ordering in which recording a measurement can roll
back the work it measured, and an instrumentation failure degrades to a missing key rather than to a
lost stage.
"""

from __future__ import annotations

import time
from typing import Any, Final

from psycopg.types.json import Json

from src.lib import logger

log = logger.get_logger(__name__)

NS_PER_MS: Final = 1_000_000

#: The stages a run can carry, and the only keys `stage_timings` may hold. `profile` covers the
#: drift gate and map-from-spec because one job does all three; there is deliberately no `map`.
STAGE_NAMES: Final = (
    "parse",
    "profile",
    "materialize",
    "readiness",
    "rebate_calc",
    "legacy_import",
    "variance",
    "attribution",
)


class StageTimer:
    """Starts on construction; `ms` is however long it has been since, read whenever you ask.

    Deliberately not a context manager. A stage's timing has to be merged into the `update` that
    stage already issues - which sits in the middle of a function, not at the end of a block - and
    wrapping each worker function's whole body in a `with` to reach it would be a large edit for no
    gain. Constructing it at the top and reading `ms` at the write is both smaller and the same
    measurement.
    """

    __slots__ = ("_started",)

    def __init__(self) -> None:
        """Never raises either, for the same reason `ms` does not.

        This is constructed at the **top** of a stage, before any of its real work. An unguarded
        clock read here is strictly worse than one at the write: it loses the whole stage rather
        than one measurement, and it does so before anything has happened that could be recovered.
        A tester's bug report caught the read in `ms` and missed this one; `None` here means
        "unmeasurable", which `ms` then reports as `-1` and `stage_timing_param` merges as `{}`.
        """
        try:
            self._started: int | None = time.monotonic_ns()
        except Exception:  # pragma: no cover - the clock does not normally fail
            self._started = None

    @property
    def ms(self) -> int:
        """Never raises. A clock that cannot be read is a missing measurement, not a lost stage."""
        if self._started is None:
            return -1
        try:
            return max(0, (time.monotonic_ns() - self._started) // NS_PER_MS)
        except Exception:  # pragma: no cover - the clock does not normally fail
            return -1


def stage_timing_param(stage: str, elapsed_ms: int, attempts: int = 1) -> Json:
    """The jsonb value to merge, or an empty object when the measurement failed.

    **This must never raise**, and the reason is subtle enough to be worth stating. The call sits
    inside the parameter tuple of the `execute` that writes the stage's *real* status, and Python
    builds that tuple before calling `execute`. So anything that raises here takes the status write
    with it - the stage's actual work would be lost to a failed clock read, which is the precise
    opposite of the degradation this module's docstring promises. A tester found exactly that by
    patching `time.monotonic_ns` to raise.

    An unknown stage and a failed measurement both become `{}`, which merges as a no-op, and both
    are logged. A name typo is caught by the import-time `STAGE_NAMES` check in the test suite
    rather than by taking a worker down in production.
    """
    if stage not in STAGE_NAMES or elapsed_ms < 0:
        log.warning(
            "stage_timing_skipped", stage=stage, elapsed_ms=elapsed_ms, known=stage in STAGE_NAMES
        )
        return Json({})
    payload: dict[str, Any] = {stage: {"ms": int(elapsed_ms), "attempts": int(attempts)}}
    return Json(payload)


#: Merged, not replaced: each stage owns its own key and must not erase another's.
MERGE_SQL: Final = "stage_timings = stage_timings || cast(%s as jsonb)"

#: The whole statement, for a stage whose timing is the only thing it writes - which is every stage
#: that records its timing separately from its status write. A **constant**, not an f-string built
#: at each call site: `MERGE_SQL` never contained user input, but two call sites interpolating it
#: tripped semgrep's `sqlalchemy-execute-raw-query` rule while half a dozen others with the same
#: shape did not, and arguing with a scanner's heuristic is worse than not needing it. Takes the
#: timing parameter then the run id: `cur.execute(MERGE_ONLY_SQL, (stage_timing_param(...), id))`.
MERGE_ONLY_SQL: Final = f"update ingest_runs set {MERGE_SQL}, updated_at = now() where id = %s"
