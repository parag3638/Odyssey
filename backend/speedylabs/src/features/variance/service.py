"""Variance rules and the gates. No HTTP, no SQL (§2).

The preconditions, and which code each one reuses
-------------------------------------------------
* The rebates must be calculated, checked through **P11's own `rebate_gate`** and refused with
  **P11's own `rebates_not_calculated`**. Not a second code and not a second query: the cost of two
  authorities counting different things is already written down (`docs/progress.md`, the drift
  gate), and P11 wrote that function under that name so this phase could not repeat it.
* The legacy file must belong to this customer, be a `legacy_rebate` file, and have imported.
  Cross-customer is a `404` rather than a `403` - with no sign-in that is the only meaningful
  boundary, and confirming which file ids exist is not information this route owes.
* At least one legacy row must have matched. A comparison over nothing is not an empty table, it
  is a wrong answer: it would report perfect agreement against zero rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.rebates import repo as rebates_repo
from src.features.runs import repo as runs_repo
from src.features.variance import repo
from src.features.variance.codes import LINK_CAUSES, PRESENCE_VALUES, ROOT_CAUSES
from src.jobs.tasks import defer_variances
from src.lib import cursor, idempotency, logger
from src.lib.errors import (
    LegacyNotImported,
    LegacyUnmatched,
    MalformedRequest,
    NotFound,
    RebatesNotCalculated,
    VariancesNotComputed,
)

log = logger.get_logger(__name__)

#: Unmatched legacy rows shown in the warning panel. Bounded for the reason every other list is.
UNMATCHED_CAP: Final = 100
#: Legacy exports offered in the picker.
LEGACY_FILE_CAP: Final = 50

_ROUTE_KEY: Final = "variances.compute"

#: `legacy_rebate_lines.match_status`, for the `?match=` filter.
MATCH_STATUSES: Final = ("matched", "unmatched_program")


async def _run_or_404(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    run = await runs_repo.get_run(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")
    return dict(run)


async def legacy_files(session: AsyncSession, customer_id: UUID) -> list[dict[str, Any]]:
    return await repo.legacy_files(session, customer_id, limit=LEGACY_FILE_CAP)


async def legacy_lines(
    session: AsyncSession,
    source_file_id: UUID,
    *,
    limit: int,
    match: str | None,
    raw_cursor: str | None,
) -> dict[str, Any]:
    """Every row of one legacy export, matched and unmatched alike.

    The unmatched rows also reach the variance summary, but capped and only for the file a
    comparison used. This is the route that answers "what did you actually read out of my file",
    which is a different question and the one a user asks when a number looks wrong.
    """
    owner = await repo.legacy_file_owner(session, source_file_id)
    if owner is None or str(owner["kind"]) != "legacy_rebate":
        raise NotFound("That legacy export does not exist.")
    if match is not None and match not in MATCH_STATUSES:
        raise MalformedRequest(
            "match must be one of " + ", ".join(MATCH_STATUSES) + ".",
            fields={"match": "Unknown value."},
        )
    cursor_row = None if raw_cursor is None else _decode_row(raw_cursor)
    rows = await repo.legacy_lines(
        session, source_file_id, limit=limit + 1, match=match, cursor_row=cursor_row
    )
    shown = rows[:limit]
    next_cursor = (
        cursor.encode(str(shown[-1]["source_row_number"])) if len(rows) > limit and shown else None
    )
    return {"rows": shown, "next_cursor": next_cursor}


def _decode_row(raw: str) -> int:
    return cursor.decode(
        raw,
        parse=lambda parts: int(parts[0]),
        message="That page link is not valid. Go back to the first page of legacy rows.",
    )


async def start_computation(
    session: AsyncSession,
    run_id: UUID,
    *,
    legacy_source_file_id: UUID,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Queue a comparison, at most once per key.

    The claim is here rather than in the route because the service owns the transaction boundary -
    the mistake P11.5 made and had to correct. Order: claim, gates, defer, fulfil, one commit, so a
    request the gates refuse does not burn the key.
    """
    answered = {
        "ingest_run_id": run_id,
        "status": "queued",
        "status_url": f"/v1/ingest-runs/{run_id}",
    }
    # The fingerprint carries **both** inputs: the same key against a different legacy file is a
    # different request, and replaying the first answer would silently compare the wrong export.
    fingerprint = f"{run_id}:{legacy_source_file_id}"
    if idempotency_key is not None:
        claim = await idempotency.claim(
            session, route=_ROUTE_KEY, key=idempotency_key, fingerprint=fingerprint
        )
        if claim.replay_ref is not None:
            log.info("variances_replayed", ingest_run_id=str(run_id))
            return answered

    run = await _run_or_404(session, run_id)

    errored, calculated_at = await rebates_repo.rebate_gate(session, run_id)
    if calculated_at is None or errored:
        raise RebatesNotCalculated(
            "Rebates have not been calculated for this run yet. Start a calculation first."
        )
    # And a **result set** must actually exist. `calculated_at` is a timestamp a hand-edit can set;
    # comparing against zero `rebate_calcs` rows produces a `legacy_only` row for every legacy line
    # and presents a comparison against nothing as ordinary output. Gated on the count, which is the
    # `docs/decisions.md` rule every other gate in this codebase follows.
    if await repo.calculated_period_count(session, run_id) == 0:
        raise RebatesNotCalculated(
            "This run has no calculated rebate periods to compare against. Calculate rebates first."
        )

    owner = await repo.legacy_file_owner(session, legacy_source_file_id)
    if owner is None or str(owner["customer_id"]) != str(run["customer_id"]):
        raise NotFound("That legacy export does not exist.")
    if str(owner["kind"]) != "legacy_rebate":
        # Not a legacy file. A 404 rather than a 422, for the same reason the cross-customer case
        # is one: which file ids are which kind is not information this route owes.
        raise NotFound("That legacy export does not exist.")
    if str(owner["status"]) != "imported":
        raise LegacyNotImported(
            "That legacy export has not been imported yet. Wait for it to finish, or upload it "
            "again if it failed."
        )

    if await repo.matched_count(session, legacy_source_file_id) == 0:
        rows = await repo.unmatched(session, legacy_source_file_id, limit=5)
        refs = ", ".join(repr(str(row["program_external_ref"])) for row in rows) or "none"
        raise LegacyUnmatched(
            "No row in that legacy export matches a rebate program for this customer "
            f"(it names {refs}). Check the program references, then compare again."
        )

    await defer_variances(run_id, legacy_source_file_id)
    if idempotency_key is not None:
        await idempotency.fulfil(
            session, route=_ROUTE_KEY, key=idempotency_key, response_ref=str(run_id)
        )
    await session.commit()
    log.info(
        "variance_computation_queued",
        ingest_run_id=str(run_id),
        legacy_source_file_id=str(legacy_source_file_id),
    )
    return answered


async def summary(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    await _run_or_404(session, run_id)
    head = await repo.summary(session, run_id)
    if int(head["variance_count"]) == 0:
        raise VariancesNotComputed(
            "This run has not been compared against a legacy export yet. Pick one and compare."
        )
    legacy_file_id = head["legacy_source_file_id"]
    unmatched = (
        await repo.unmatched(session, UUID(str(legacy_file_id)), limit=UNMATCHED_CAP)
        if legacy_file_id
        else []
    )
    computed_at, calculated_at = head["computed_at"], head["calculated_at"]
    return {
        **head,
        "variances_stale": bool(
            computed_at is not None and calculated_at is not None and calculated_at > computed_at
        ),
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
    }


async def page(
    session: AsyncSession,
    run_id: UUID,
    *,
    limit: int,
    cause: str | None,
    presence: str | None,
    link_cause: str | None,
    raw_cursor: str | None,
) -> dict[str, Any]:
    await _run_or_404(session, run_id)
    if cause is not None and cause not in ROOT_CAUSES:
        raise MalformedRequest(
            "cause must be one of " + ", ".join(ROOT_CAUSES) + ".",
            fields={"cause": "Unknown value."},
        )
    if presence is not None and presence not in PRESENCE_VALUES:
        raise MalformedRequest(
            "presence must be one of " + ", ".join(PRESENCE_VALUES) + ".",
            fields={"presence": "Unknown value."},
        )
    if link_cause is not None and link_cause not in LINK_CAUSES:
        raise MalformedRequest(
            "linkCause must be one of " + ", ".join(LINK_CAUSES) + ".",
            fields={"linkCause": "Unknown value."},
        )
    period, program = _decode(raw_cursor)
    rows = await repo.page(
        session,
        run_id,
        limit=limit + 1,
        cause=cause,
        presence=presence,
        link_cause=link_cause,
        cursor_period=period,
        cursor_program=program,
    )
    shown = rows[:limit]
    next_cursor = (
        _Cursor(shown[-1]["period_start"], UUID(str(shown[-1]["program_id"]))).encode()
        if len(rows) > limit and shown
        else None
    )
    return {"rows": shown, "next_cursor": next_cursor}


@dataclass(frozen=True)
class _Cursor:
    period_start: date
    program_id: UUID

    def encode(self) -> str:
        return cursor.encode(self.period_start.isoformat(), str(self.program_id))


def _parse(parts: list[str]) -> _Cursor:
    return _Cursor(date.fromisoformat(parts[0]), UUID(parts[1]))


def _decode(raw: str | None) -> tuple[date | None, UUID | None]:
    if raw is None:
        return None, None
    decoded = cursor.decode(
        raw,
        parse=_parse,
        message="That page link is not valid. Go back to the first page of variances.",
    )
    return decoded.period_start, decoded.program_id
