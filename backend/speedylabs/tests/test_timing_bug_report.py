"""Timing instrumentation (`src/lib/timing.py`) - history and regression coverage.

This file started as a P14 bug report (no test file for `lib/timing.py` existed before it, despite
the P14 plan's 14.1 step naming eight separate "Accept:" criteria for it) demonstrating that a
raising clock could take a stage's real status write down with it. Both defects it reported are now
fixed, in two places:

1. `StageTimer.ms` degrades to `-1` instead of raising (this file's original finding).
2. `StageTimer.__init__` *also* degrades (`_started = None`) instead of raising - a second,
   strictly worse instance of the same defect the tester found chasing the first: a clock failure at
   construction loses the whole stage before any real work has even started, rather than losing one
   measurement. See `docs/decisions.md`'s P14-16 round-2 entry and `src/lib/timing.py`'s own
   docstrings on `StageTimer.__init__` and `.ms`.

**Round-2 tester adjudication of this file's four still-failing assertions, left disputed by the
builder rather than touched:**

- `test_bug_a_raising_clock_breaks_the_stage_it_was_only_meant_to_time` asserted
  `pytest.raises(OSError)` around `stage_timing_param("parse", elapsed.ms)` - i.e. it asserted
  the *broken* behaviour. Now that `.ms` degrades instead of raising, the assertion fails because
  nothing raises any more, which is correct: the bug is fixed and the test's job is done. Inverted
  below into `test_a_raising_clock_does_not_break_the_stage_it_was_only_meant_to_time`, which
  asserts the behaviour the module's docstring promises instead of the behaviour it used to
  exhibit.

- `test_bug_no_call_site_isolates_the_timing_read_from_the_stage_write[parse|profile|materialize]`
  asserted a literal `try:` within 400 characters before each `stage_timing_param(...)` call site
  in `tasks.py`. That is an assertion about *where* the isolation lives, not about whether the
  degradation behaviour actually holds - and the fix placed the isolation inside `StageTimer` and
  `stage_timing_param` themselves (one place, covering every call site in every module, including
  the five that live outside `tasks.py` and were never reachable by this test's 400-character
  window), which is a better fix than eight local `try:` blocks would have been: a ninth call
  site added later gets the guarantee for free instead of needing its own copy-pasted isolation.
  Confirmed empirically (`StageTimer()` constructed, clock then patched to raise, `.ms` and
  `stage_timing_param(...)` both read clean with no exception - see the regression test below)
  before accepting the builder's account rather than taking it on word. Folded below into
  `test_a_raising_clock_degrades_to_a_missing_key_for_every_stage`, parametrized over all eight
  `STAGE_NAMES` (strictly more coverage than the three `tasks.py`-only cases it replaces) and into
  `test_a_raising_clock_at_construction_also_degrades_to_a_missing_key`, which covers the
  constructor-time defect item 2 above fixed that this file's original report did not.

The remaining test (`test_attempts_is_threaded_through_for_every_stage`, renamed from
`test_bug_attempts_is_hardcoded_to_one_for_seven_of_eight_stages`) was never disputed and already
passed after the round-2 fix - kept, tightened from "seven of eight" to all eight now that `parse`
and `profile` also thread a real `attempt` argument (grep shows three arguments at every call site).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.lib.timing import STAGE_NAMES, StageTimer, stage_timing_param

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO_ROOT / "backend" / "src"

#: Every module with a `stage_timing_param(...)` call site, found with
#: `grep -rn "stage_timing_param(" backend/src/`.
_CALL_SITE_FILES = (
    BACKEND_SRC / "jobs" / "tasks.py",
    BACKEND_SRC / "features" / "rebates" / "run.py",
    BACKEND_SRC / "features" / "variance" / "compute.py",
    BACKEND_SRC / "features" / "legacy" / "importer.py",
    BACKEND_SRC / "features" / "readiness" / "run.py",
    BACKEND_SRC / "features" / "attribution" / "attribute.py",
)


def test_a_raising_clock_does_not_break_the_stage_it_was_only_meant_to_time() -> None:
    """Regression for this file's original finding: a clock that fails *after* `StageTimer` has
    started must not take the stage's real status write down with it.

    `stage_timing_param(stage, elapsed.ms)` is still built as a plain tuple element at every call
    site (Python evaluates it before `cur.execute` is ever reached) - so the guarantee has to hold
    inside the expression itself, not around it. It does: `StageTimer.ms` catches the failing clock
    read and reports `-1`, and `stage_timing_param` treats a negative `elapsed_ms` as unmeasurable
    and returns a no-op `{}` rather than propagating anything.
    """
    elapsed = StageTimer()

    original_monotonic_ns = time.monotonic_ns

    def _raising_monotonic_ns() -> int:
        raise OSError("simulated clock failure")

    time.monotonic_ns = _raising_monotonic_ns
    try:
        # Exactly the expression every stage-write call site builds inline as a tuple element
        # passed to cur.execute(...). It must not raise.
        param = stage_timing_param("parse", elapsed.ms)
    finally:
        time.monotonic_ns = original_monotonic_ns

    assert param.obj == {}, "a failed clock read must degrade to a no-op merge, not raise"


def test_a_raising_clock_at_construction_also_degrades_to_a_missing_key() -> None:
    """Regression for the second, worse instance the tester's chase of the first one turned up:
    a clock that fails at `StageTimer()` construction - before any real work has happened - must
    not raise either. `_started` becomes `None`, `.ms` reports `-1` for that, and
    `stage_timing_param` treats it the same as any other unmeasurable reading.
    """
    original_monotonic_ns = time.monotonic_ns

    def _raising_monotonic_ns() -> int:
        raise OSError("simulated clock failure")

    time.monotonic_ns = _raising_monotonic_ns
    try:
        elapsed = StageTimer()  # must not raise
        assert elapsed.ms == -1
        param = stage_timing_param("parse", elapsed.ms)  # must not raise
    finally:
        time.monotonic_ns = original_monotonic_ns

    assert param.obj == {}


@pytest.mark.parametrize("stage", STAGE_NAMES)
def test_a_raising_clock_degrades_to_a_missing_key_for_every_stage(stage: str) -> None:
    """For every one of the eight stages this module instruments (not only the three written from
    `tasks.py` the original report scanned), confirm the exact expression that stage's call site
    builds - `stage_timing_param(stage, elapsed.ms, attempt)` - survives a raising clock without
    propagating, and merges nothing rather than something wrong.

    This supersedes the original
    `test_bug_no_call_site_isolates_the_timing_read_from_the_stage_write`, which asserted a
    literal `try:` string near each call site in `tasks.py` as a proxy for this behaviour. That
    proxy stopped being true of the real fix (the isolation moved inside
    `StageTimer`/`stage_timing_param`, covering every call site including the five outside
    `tasks.py` the proxy could never see) while the behaviour it was a proxy *for* kept holding,
    which is exactly why asserting the behaviour directly is the more durable test.
    """
    elapsed = StageTimer()

    original_monotonic_ns = time.monotonic_ns

    def _raising_monotonic_ns() -> int:
        raise OSError("simulated clock failure")

    time.monotonic_ns = _raising_monotonic_ns
    try:
        param = stage_timing_param(stage, elapsed.ms, 2)
    finally:
        time.monotonic_ns = original_monotonic_ns

    assert param.obj == {}, f"stage {stage!r}: a failed clock read must degrade, not raise"


def test_attempts_is_threaded_through_for_every_stage() -> None:
    """P14 plan 14.1's accept criterion: "given a stage that retried twice, then `attempts` is 3
    and `ms` is the successful attempt's wall time" - i.e. every stage must report its real
    procrastinate attempt count, not a constant.

    Renamed from `test_bug_attempts_is_hardcoded_to_one_for_seven_of_eight_stages`: the round-2 fix
    threads `attempt=context.job.attempts + 1` through all eight call sites (`parse`, `profile`,
    `materialize`, `readiness`, `rebate_calc`, `legacy_import`, `variance`, `attribution`), not just
    the five the handover named - `parse` and `profile` already passed a third argument before this
    round, confirmed by grep below rather than assumed.
    """
    for stage in STAGE_NAMES:
        needle = f'stage_timing_param("{stage}"'
        found = False
        for path in _CALL_SITE_FILES:
            source = path.read_text(encoding="utf-8")
            start = source.find(needle)
            if start == -1:
                continue
            found = True
            # Extract exactly the call's own argument list, up to its matching close-paren, so
            # commas belonging to the surrounding tuple (e.g. a trailing `run_id`) are not counted
            # as if they were a third argument to this call.
            depth = 0
            end = start
            for index in range(start + len("stage_timing_param"), len(source)):
                char = source[index]
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        end = index
                        break
            call_text = source[start : end + 1]
            assert call_text.count(",") >= 2, (
                f"{path.relative_to(REPO_ROOT)}: {call_text!r} passes no `attempts` argument for "
                f"stage {stage!r}, so it silently defaults to 1 regardless of procrastinate's real "
                "attempt count."
            )
            break
        assert found, (
            f"no call site for stage {stage!r} found in {[str(p) for p in _CALL_SITE_FILES]}"
        )
