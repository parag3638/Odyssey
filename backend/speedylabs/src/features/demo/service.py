"""Demo state, and the one destructive action in the system.

**Reset clears and re-seeds; it does not merely clear.** A cleared instance is a broken demo, and
the point of the button is that the next visitor gets the state the last one started from.

**It deletes and rebuilds rather than recomputing in place**, which matters for one specific reason:
`first_variance_at` and `elapsed_ms_to_first_variance` are written *once, conditional on null*
(P12). A reset that kept the runs and recomputed their derived tables would leave A6's measurement
frozen at the first visitor's number forever, with nothing on screen saying so. Deleting the run
rows clears those columns for free - the careful-looking design is the one that breaks.

Four guards, because this route is public and destructive:

1. `demo_reset_enabled`, **false by default** (BACKEND.md §4: deny by default).
2. A rate limit, whose own keys the clear deliberately preserves - clearing them would let the next
   click straight through and the guard would defeat itself.
3. A **Postgres advisory lock**, so two replicas cannot reset at once. An `asyncio.Lock` guarded
   only the process holding it, which for a destructive action means one process deleting what
   another is rebuilding.
4. The delete is scoped by slug. `delete from customers` with no predicate was used once to measure
   the cascade, in a rolled-back transaction, and must never ship.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.demo import repo
from src.features.demo.seed import seed
from src.lib import db, logger, ratelimit, singleflight
from src.lib.errors import DemoResetDisabled, ResetInProgress
from src.lib.ids import uuid7
from src.lib.storage import LocalStorage

log = logger.get_logger(__name__)

DEMO_SLUG: Final = "demo-distributor"
DEMO_NAME: Final = "Harbor Point Foodservice"

#: Bumped by hand when the seeded state changes shape. Not the generator's seed, which is a one-way
#: door and unchanged at 20261003.
SEED_VERSION: Final = "2026-10-08.1"

_HOUR: Final = 3600

#: One reset at a time, **across processes**. Session-scoped rather than transaction-scoped: the
#: reset commits part-way through, and a transaction lock would be released at that commit leaving
#: the rest of the destructive work unguarded.
_RESET_LOCK: Final = "demo:reset"


def _remove_files(stored_names: list[str]) -> int:
    """Delete the uploads the cascade just orphaned.

    **After the commit, not before.** A file deleted before the rows are gone would leave a run
    pointing at nothing if the transaction rolled back; an orphaned *file* after a successful
    delete is merely waste.

    Through `Storage.delete`, not path arithmetic: that seam is what P17 swaps for object storage,
    and it is already `missing_ok`, which is what makes a retried reset safe.
    """
    storage = LocalStorage(config.load().upload_dir)
    removed = 0
    for name in stored_names:
        try:
            storage.delete(name)
            removed += 1
        except OSError as exc:  # pragma: no cover - a stuck file must not fail the reset
            log.warning("demo_reset_file_unlink_failed", stored_name=name, error=str(exc))
    return removed


async def state(session: AsyncSession) -> dict[str, Any]:
    """Whether this instance holds the seeded demo, and whether reset is available.

    `seeded` is false in three different situations - no row, a row whose `completed_at` is null
    (interrupted), or no demo customer - and they get one answer, because the caller's next action
    is the same in all three.
    """
    settings = config.load()
    row = await repo.state(session, slug=DEMO_SLUG)
    seeded = bool(
        row is not None and row["completed_at"] is not None and row["customer_name"] is not None
    )
    return {
        "seed_version": SEED_VERSION,
        "seeded_at": None if row is None else row["seeded_at"],
        "seeded": seeded,
        "reset_enabled": settings.demo_reset_enabled,
        "customer_name": None if row is None else row["customer_name"],
        "run_count": 0 if row is None else int(row["run_count"]),
    }


async def start_reset(session: AsyncSession, *, ip_hash: str) -> dict[str, Any]:
    """Check the guards, hand the work to the queue, answer 202.

    The destructive work itself is in :func:`run_reset`, on the worker, which is what the P15
    plan's Speed section asks for: a ~145,000-row cascade delete does not belong on a live request,
    and the `202` this returns is only honest if something really is still running when the caller
    reads it.

    What stays here is everything a caller needs an immediate answer about - whether reset is
    enabled, whether they are over their limit, and whether one is already running.

    **The in-flight answer is best-effort, and saying so is the point.** Three mechanisms overlap
    and none of them is a guarantee on its own:

    * `queueing_lock` refuses a second *enqueue* while the first job is still `todo`.
    * `singleflight.is_held` below refuses while a job is actually running.
    * `try_acquire` inside `run_reset` is the one that decides, across processes.

    Measured rather than assumed, and the measurement is why this docstring is hedged: two clicks
    20 ms apart both returned `202 queued` and both jobs ran, because the worker LISTENs and had
    already moved job one out of `todo` before the second check looked, while not yet having taken
    the advisory lock. With one worker at concurrency 1 the two then ran in sequence, which is
    harmless - the reset is delete-then-rebuild, so running it twice lands on the same state - and
    the rate limit caps how often anyone can do it. A guarantee would need the enqueue and the
    lock to be one atomic step; this is a demo reset, and three cheap overlapping checks plus an
    idempotent action is the right amount of machinery for it.

    What this is *not* is a `completed_at is null` check on `demo_seed_state`, which was the
    obvious design and is a trap: nothing ever clears that column, so a single crashed reset would
    refuse every later one forever. An advisory lock dies with its connection.
    """
    settings = config.load()
    if not settings.demo_reset_enabled:
        raise DemoResetDisabled("Resetting the demo is turned off on this instance.")
    await ratelimit.enforce(
        session,
        key=f"demo_reset:{ip_hash}",
        limit=settings.rate_limit_demo_reset_per_hour,
        window_seconds=_HOUR,
        what="demo resets",
    )
    if await singleflight.is_held(session, _RESET_LOCK):
        raise ResetInProgress("A reset is already running. Wait for it to finish.")

    # The rate limit is counted above and must stick whether or not the defer succeeds, so this
    # commits before the hand-off: a caller who gets `ResetInProgress` has still used an attempt.
    await session.commit()

    from src.jobs.tasks import defer_demo_reset

    job = await defer_demo_reset(ip_hash)
    if job is None:
        raise ResetInProgress("A reset is already running. Wait for it to finish.")
    return {
        "status": "queued",
        "seed_version": SEED_VERSION,
        "status_url": "/v1/demo/state",
    }


async def run_reset(session: AsyncSession, *, ip_hash: str) -> dict[str, Any]:
    """Clear this demo and rebuild it. Runs on the worker, not in a request.

    **The lock is held on a connection this function owns, not on `session`.** That is the whole
    reason `singleflight.hold` exists. A Postgres advisory lock belongs to the backend connection,
    and an `AsyncSession` hands its connection back to the pool on every `commit()` - this function
    commits at least three times - so releasing through the session can run `pg_advisory_unlock` on
    a connection that never held the lock. That returns `false` and raises nothing, leaving the real
    holder idle in the pool still locked, and every later reset answering 409 until the process
    restarts. Which is exactly the wedged-forever failure the advisory lock was chosen over a
    `completed_at is null` check to avoid, reached by a different door. A tester proved the
    mechanism deterministically with two connections on one pooled engine; it did not bite in the
    shipped compose file only because the worker runs at `concurrency=1` and nothing else draws on
    that engine, neither of which is a guarantee.
    """
    async with singleflight.hold(db.engine(), _RESET_LOCK) as acquired:
        if not acquired:
            raise ResetInProgress("A reset is already running. Wait for it to finish.")

        await repo.mark_started(session, seed_id=uuid7(), version=SEED_VERSION)
        # Read before the cascade: these rows are about to go, and with them the only record of
        # which files on disk belong to this demo.
        orphans = await repo.stored_names(session, slug=DEMO_SLUG)
        await repo.clear(session, slug=DEMO_SLUG)
        await session.commit()
        removed = _remove_files(orphans)
        log.info(
            "demo_reset_cleared",
            slug=DEMO_SLUG,
            seed_version=SEED_VERSION,
            files_removed=removed,
        )

        result = await seed(session, slug=DEMO_SLUG, name=DEMO_NAME, ip_hash=ip_hash)
        await repo.mark_complete(session, version=SEED_VERSION)
        await session.commit()
        log.info("demo_reseeded", slug=DEMO_SLUG, runs=len(result["runs"]))

    return {"status": "reseeded", "seed_version": SEED_VERSION, "runs": result["runs"]}
