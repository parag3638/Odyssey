"""Rebate rules and the gates. No HTTP, no SQL (§2).

The gates, and the one that is deliberately absent
--------------------------------------------------
Materialised, drift not blocking and not rejected, and at least one program - all required. Each is
checked against a **count**, never a status, because a run hand-edited to `materialized` has no rows
and must still be refused (`docs/decisions.md:203`).

**Readiness clean is deliberately not a precondition**, and a test says so by name. The seeded file
is readiness-`blocked` by construction: `mixed_uom_without_pack_size`, `catch_weight_missing`,
`extension_mismatch` and `duplicate_line` are all blocking and all have findings, so the gate would
make the demo impossible. It is also wrong on the merits - the parallel run exists precisely to show
what those defects cost in rebate dollars, and refusing to calculate until they are fixed would
withhold the only number that justifies fixing them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.canonical import repo as canonical_repo
from src.features.rebates import repo
from src.features.rebates.codes import LINE_STATUSES
from src.features.rebates.reference_engine import ENGINE_NAME, ENGINE_VERSION
from src.features.runs import repo as runs_repo
from src.jobs.tasks import defer_rebates
from src.lib import cursor, idempotency, logger
from src.lib.errors import (
    DriftBlocked,
    FileRejected,
    MalformedRequest,
    NoRebatePrograms,
    NotFound,
    NotMaterialized,
    RebatesNotCalculated,
)
from src.lib.manufacturers import ManufacturerLookup, lookup_from_rows

log = logger.get_logger(__name__)

#: Distinct brand spellings reported in the coverage panel. Bounded for the reason the profiler's
#: `VALUE_COUNT_CAP` is: an unbounded group-by over a hostile file is an unbounded response.
BRAND_COVERAGE_CAP: Final = 200

#: The idempotency route key. One string, so a later caller cannot claim under a near-miss spelling.
_ROUTE_KEY: Final = "rebates.calculate"

_ANSWER: Final[dict[str, Any]] = {"status": "queued"}


async def _run_or_404(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    run = await runs_repo.get_run(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")
    return dict(run)


async def _require_calculable(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """The three preconditions, in the order whose message is most useful first."""
    run = await _run_or_404(session, run_id)

    rows_written, _ = await canonical_repo.counts(session, run_id)
    if rows_written == 0:
        raise NotMaterialized(
            "This run has no canonical rows yet. Materialise it before calculating rebates."
        )

    blocking_drift, drift_decision = await canonical_repo.drift_gate(session, run_id)
    if drift_decision == "rejected":
        raise FileRejected(
            "This file was rejected because it differs from the last approved one. Upload a "
            "corrected export, or accept the change to release it."
        )
    if blocking_drift and drift_decision != "accepted":
        raise DriftBlocked(
            f"This file has {blocking_drift} change"
            f"{'s' if blocking_drift != 1 else ''} or unfinished comparison"
            f"{'s' if blocking_drift != 1 else ''} since the last approved file. Review them and "
            "accept or reject the file before calculating rebates."
        )

    if not await repo.programs(session, UUID(str(run["customer_id"]))):
        raise NoRebatePrograms(
            "No rebate programs are set up for this customer. Add a program before calculating."
        )
    return run


async def start_calculation(
    session: AsyncSession, run_id: UUID, *, idempotency_key: str | None = None
) -> dict[str, Any]:
    """Queue a calculation, at most once per key.

    The claim lives here rather than in the route because **the service owns the transaction
    boundary** (`lib/db.session`: "callers own the transaction"). A first attempt put it in the
    route with no commit, so every claim was rolled back when the session closed and the table
    stayed empty - the route looked idempotent and was not. Found by reusing one key against two
    different runs and getting the second run's gate error instead of a `409 conflict`.

    Order is deliberate: claim, then the gates, then defer, then fulfil, then one commit. A request
    the gates refuse raises before that commit, so a rejected call does not burn the key.
    """
    answered = _ANSWER | {"ingest_run_id": run_id, "status_url": f"/v1/ingest-runs/{run_id}"}
    if idempotency_key is not None:
        claim = await idempotency.claim(
            session, route=_ROUTE_KEY, key=idempotency_key, fingerprint=str(run_id)
        )
        if claim.replay_ref is not None:
            # Already answered. No second job, and no second gate check either: the answer this key
            # produced is the answer it keeps producing.
            log.info("rebate_calculation_replayed", ingest_run_id=str(run_id))
            return answered

    await _require_calculable(session, run_id)
    await defer_rebates(run_id)
    if idempotency_key is not None:
        await idempotency.fulfil(
            session, route=_ROUTE_KEY, key=idempotency_key, response_ref=str(run_id)
        )
    await session.commit()
    log.info("rebate_calculation_queued", ingest_run_id=str(run_id))
    return answered


async def summary(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """The honesty surface: the engine, the outcome per program, and brand coverage."""
    run = await _run_or_404(session, run_id)
    head = await repo.summary(session, run_id)
    if int(head["attempts"]) == 0:
        # Not a 200 of zeros: "nothing has been calculated" and "we calculated and found nothing"
        # are different answers and only one of them is about rebates.
        raise RebatesNotCalculated(
            "Rebates have not been calculated for this run yet. Start a calculation first."
        )

    rows = await repo.attempts(session, run_id)
    stored_name = str(head["engine_name"]) if head["engine_name"] else ENGINE_NAME
    stored_version = str(head["engine_version"]) if head["engine_version"] else ENGINE_VERSION
    coverage = await repo.brand_coverage(session, run_id, limit=BRAND_COVERAGE_CAP + 1)
    truncated = len(coverage) > BRAND_COVERAGE_CAP
    # Classified in Python with the engine's own `normalise`, not in SQL. `lower(trim(...))` is a
    # near-miss for whitespace-collapsed case-folding, and a coverage report that disagreed with
    # the matcher about what counts as the same spelling would be worse than no report.
    lookup = lookup_from_rows(await repo.reference_rows(session, UUID(str(run["customer_id"]))))
    return {
        "engine": {"name": stored_name, "version": stored_version},
        # The stored rows keep reporting the version that produced them; this flag is how the UI
        # offers a recalculation without the label ever lying about which engine made the number.
        "engine_stale": stored_name != ENGINE_NAME or stored_version != ENGINE_VERSION,
        "current_engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION},
        "calculated": head["calculated_at"] is not None,
        "calculated_at": head["calculated_at"],
        "total_amount": head["total_amount"],
        "contributed_line_count": int(head["contributed"]),
        "skipped_line_count": int(head["skipped"]),
        "out_of_scope_line_count": int(head["out_of_scope"]),
        "programs": rows,
        "brands": [_resolution(entry, lookup) for entry in coverage[:BRAND_COVERAGE_CAP]],
        "brands_truncated": truncated,
    }


def _resolution(entry: dict[str, Any], lookup: ManufacturerLookup) -> dict[str, Any]:
    """What became of one brand spelling, and which of two different problems it is.

    `claimed` comes from the anti-join: a spelling is claimed when some program produced a line
    result for a canonical row carrying it. The two unclaimed cases need separate names because they
    need separate actions:

    * `no_program` - the manufacturer list recognises this spelling, and no program covers it. A
      commercial gap: somebody has to add the program, or accept that those lines earn nothing.
    * `unknown_brand` - nothing in `brand_values` matches this spelling at all. Almost always a
      mapping fault, and the reason a file whose brand column went through the wrong transform
      produces a clean, well-formed, entirely zero rebate table.
    """
    value = str(entry["brand_value"])
    entry_match = lookup.find(value)
    if entry["claimed"]:
        resolution = "program"
    elif entry_match is not None:
        resolution = "no_program"
    else:
        resolution = "unknown_brand"
    return {
        "brand_value": value,
        "manufacturer_name": None if entry_match is None else entry_match.name,
        "line_count": int(entry["line_count"]),
        "resolution": resolution,
    }


async def calcs(
    session: AsyncSession, run_id: UUID, *, limit: int, raw_cursor: str | None
) -> dict[str, Any]:
    await _run_or_404(session, run_id)
    head = await repo.summary(session, run_id)
    if int(head["attempts"]) == 0:
        raise RebatesNotCalculated(
            "Rebates have not been calculated for this run yet. Start a calculation first."
        )

    period, program = _decode_calc_cursor(raw_cursor)
    rows = await repo.calcs_page(
        session, run_id, limit=limit + 1, cursor_period=period, cursor_program=program
    )
    page, next_cursor = _paginate(rows, limit, _encode_calc_cursor)
    return {"rows": page, "next_cursor": next_cursor}


async def lines(
    session: AsyncSession,
    calc_id: UUID,
    *,
    limit: int,
    status: str | None,
    raw_cursor: str | None,
) -> dict[str, Any]:
    owner = await repo.calc_owner(session, calc_id)
    if owner is None:
        raise NotFound("That rebate calculation does not exist.")
    if status is not None and status not in LINE_STATUSES:
        raise MalformedRequest(
            "status must be one of " + ", ".join(LINE_STATUSES) + ".",
            fields={"status": "Unknown value."},
        )

    cursor_row = None if raw_cursor is None else _decode_row_cursor(raw_cursor)
    rows = await repo.lines_page(
        session, calc_id, limit=limit + 1, status=status, cursor_row=cursor_row
    )
    page = rows[:limit]
    next_cursor = (
        cursor.encode(str(page[-1]["source_row_number"])) if len(rows) > limit and page else None
    )
    return {"rows": page, "next_cursor": next_cursor, "uom_basis": str(owner["uom_basis"])}


async def programs(session: AsyncSession, customer_id: UUID) -> list[dict[str, Any]]:
    if not await repo.customer_exists(session, customer_id):
        raise NotFound("That customer does not exist.")
    return await repo.programs(session, customer_id)


#: The calc list is keyed on `(period_start, program_id)` - the order the table is read in - so a
#: page boundary inside one month is stable even if a program is added between requests.
@dataclass(frozen=True)
class CalcCursor:
    period_start: date
    program_id: UUID

    def encode(self) -> str:
        return cursor.encode(self.period_start.isoformat(), str(self.program_id))


def _parse_calc(parts: list[str]) -> CalcCursor:
    return CalcCursor(period_start=date.fromisoformat(parts[0]), program_id=UUID(parts[1]))


def _decode_calc_cursor(raw: str | None) -> tuple[date | None, UUID | None]:
    if raw is None:
        return None, None
    decoded = cursor.decode(
        raw,
        parse=_parse_calc,
        message="That page link is not valid. Go back to the first page of rebate periods.",
    )
    return decoded.period_start, decoded.program_id


def _encode_calc_cursor(row: dict[str, Any]) -> str:
    return CalcCursor(
        period_start=row["period_start"], program_id=UUID(str(row["program_id"]))
    ).encode()


def _decode_row_cursor(raw: str) -> int:
    return cursor.decode(
        raw,
        parse=lambda parts: int(parts[0]),
        message="That page link is not valid. Go back to the first page of lines.",
    )


def _paginate(
    rows: list[dict[str, Any]],
    limit: int,
    encode: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], str | None]:
    page = rows[:limit]
    return page, (encode(page[-1]) if len(rows) > limit and page else None)
