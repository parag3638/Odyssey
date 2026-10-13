"""Dashboard rules. No HTTP, no SQL (§2).

**Two numbers, named apart, and this is the whole point of the phase.**

`elapsedMsToFirstVariance` is the metric the spec names - time from raw upload to first variance
report - and it is **wall clock**. Measured on the demo run it is 34,766 ms, of which **3,535 ms is
this software working**. The rest is a human deciding: mapping the columns, approving the spec,
materialising, calculating, choosing a legacy file. Both numbers are true, they are not the same
claim, and presenting the wall clock as a machine budget would be the dishonest reading.

So the dashboard reports both and labels each. `machineMs` is the sum of the stages, and it is
**null rather than partial** when any stage in the window is missing - a partial sum presented as a
total is exactly the kind of number this project refuses elsewhere.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.dashboard import repo
from src.features.demo.service import DEMO_SLUG
from src.lib.timing import STAGE_NAMES

RUN_LIMIT: Final = 10

#: The stages that sit inside the upload-to-first-variance window. `attribution` runs *after* the
#: first variance report, so it is excluded from the machine total by definition - it is timed and
#: shown, but it is not part of this measurement.
WINDOW_STAGES: Final = (
    "parse",
    "profile",
    "materialize",
    "readiness",
    "rebate_calc",
    "legacy_import",
    "variance",
)


def _machine_ms(stage_timings: dict[str, Any], legacy: dict[str, Any] | None) -> int | None:
    """The sum of the window's stages, or `None` if any of them is missing.

    Never a partial sum: "we measured six of seven stages" and "the pipeline took this long" are
    different statements, and only one of them is a total.
    """
    merged = {**stage_timings, **(legacy or {})}
    if any(stage not in merged for stage in WINDOW_STAGES):
        return None
    return sum(int(merged[stage]["ms"]) for stage in WINDOW_STAGES)


async def dashboard(session: AsyncSession) -> dict[str, Any]:
    # Every read here is scoped to the demo customer. There is no sign-in, so this is the only
    # tenant boundary there is, and an unscoped aggregate would be a cross-customer read.
    latest = await repo.latest_run(session, slug=DEMO_SLUG)
    rows = await repo.runs(session, slug=DEMO_SLUG, limit=RUN_LIMIT)
    queue = await repo.queue(session)

    if latest is None:
        return {
            "latest_run": None,
            "rows_processed": 0,
            "blocking_findings": 0,
            "explained_pct": None,
            "absolute_variance": Decimal("0"),
            "engine": None,
            "runs": rows,
            "queue": _queue(queue),
        }

    run_id = UUID(str(latest["ingest_run_id"]))
    totals = await repo.totals(session, run_id)
    legacy = await repo.legacy_stage_timings(session, run_id)
    stage_timings: dict[str, Any] = latest["stage_timings"] or {}

    absolute = Decimal(totals["absolute_variance"])
    unexplained = Decimal(totals["unexplained_amount"])
    return {
        "latest_run": {
            **latest,
            "stage_timings": {k: v for k, v in stage_timings.items() if k in STAGE_NAMES},
            "machine_ms": _machine_ms(stage_timings, legacy),
        },
        "rows_processed": int(latest["row_count"] or 0),
        "blocking_findings": int(totals["blocking_findings"]),
        # Restated from the same links P13's summary reads, never recomputed.
        "explained_pct": (
            f"{((absolute - unexplained) / absolute):.4f}"
            if absolute and int(totals["link_count"])
            else None
        ),
        "absolute_variance": absolute,
        "engine": (
            {"name": str(totals["engine_name"]), "version": str(totals["engine_version"])}
            if totals["engine_name"]
            else None
        ),
        "runs": rows,
        "queue": _queue(queue),
    }


#: procrastinate's own default, rather than an invented one.
WORKER_STALE_SECONDS: Final = 30


def _queue(row: dict[str, Any]) -> dict[str, Any]:
    """`draining`, `stale` or `unknown` - and never `stuck`.

    mvp.md's `stuck` would be a judgement about work nobody is doing, which cannot be told apart
    from work that is simply queued behind a long job. A heartbeat is a fact.
    """
    # `extract(epoch from ...)` comes back from psycopg as a `Decimal`, so there is nothing to
    # convert: the float conversion that used to be here was a no-op that happened to break the
    # blanket no-float rule. The rule is blanket on purpose - an age in seconds is not money, but
    # "this one is safe" is the argument that lets the next one in.
    raw = row["worker_last_heartbeat_seconds"]
    heartbeat = None if raw is None else Decimal(raw)
    if heartbeat is None:
        status = "unknown"
    elif heartbeat > WORKER_STALE_SECONDS:
        status = "stale"
    else:
        status = "draining"
    return {
        "status": status,
        "worker_last_heartbeat_seconds": (None if heartbeat is None else int(heartbeat)),
        "waiting_count": int(row["waiting_count"]),
        "running_count": int(row["running_count"]),
        "worker_stale_threshold_seconds": WORKER_STALE_SECONDS,
    }
