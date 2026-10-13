"""Attribution rules and the gate. No HTTP, no SQL (§2).

**The explained percentage is over absolute dollars.** `date_cutoff` relocates money between
periods, so it nets to zero: a net denominator both understates the work done and can be `0/0` on a
run whose variances happen to cancel. Measured over the demo, the net is `4,035.14` while the
absolute is `9,539.64` - and the absolute is already the fourth totals card P12 put on screen, so
the denominator the headline divides by is one the reader can see.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.attribution import repo
from src.features.attribution.glass_export import GLASS_EXPORT_VERSION, MAX_GLASS_SOURCE_LINES
from src.features.canonical.model import CANONICAL_SCHEMA_VERSION
from src.features.rebates.reference_engine import ENGINE_NAME, ENGINE_VERSION
from src.features.runs import repo as runs_repo
from src.features.variance import repo as variance_repo
from src.features.variance.codes import LINK_CAUSES, UNEXPLAINED
from src.jobs.tasks import defer_attribution
from src.lib import cursor, idempotency, logger
from src.lib.errors import MalformedRequest, NotFound, VariancesNotComputed

log = logger.get_logger(__name__)

_ROUTE_KEY: Final = "variances.attribute"


async def _run_or_404(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    run = await runs_repo.get_run(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")
    return dict(run)


async def start_attribution(
    session: AsyncSession, run_id: UUID, *, idempotency_key: str | None = None
) -> dict[str, Any]:
    """Queue an attribution pass, at most once per key."""
    answered = {
        "ingest_run_id": run_id,
        "status": "queued",
        "status_url": f"/v1/ingest-runs/{run_id}",
    }
    if idempotency_key is not None:
        claim = await idempotency.claim(
            session, route=_ROUTE_KEY, key=idempotency_key, fingerprint=str(run_id)
        )
        if claim.replay_ref is not None:
            return answered

    await _run_or_404(session, run_id)
    # Gated on a **count** of variance rows, not on a status or a timestamp - the rule every gate
    # in this codebase follows, and the one P12's own result-set guard had to be given.
    count, _computed_at, _calculated_at = await variance_repo.variance_gate(session, run_id)
    if count == 0:
        raise VariancesNotComputed(
            "This run has not been compared against a legacy export yet. Compare it first, then "
            "explain the differences."
        )

    await defer_attribution(run_id)
    if idempotency_key is not None:
        await idempotency.fulfil(
            session, route=_ROUTE_KEY, key=idempotency_key, response_ref=str(run_id)
        )
    await session.commit()
    log.info("attribution_queued", ingest_run_id=str(run_id))
    return answered


async def summary(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    await _run_or_404(session, run_id)
    head = await repo.summary(session, run_id)
    if int(head["variance_count"]) == 0:
        raise VariancesNotComputed(
            "This run has not been compared against a legacy export yet. Compare it first."
        )
    # Explained is what the residue does not account for, over the whole. Not the sum of link
    # magnitudes: a relocation is two links naming one line, so that sum double-counts it.
    absolute = Decimal(head["absolute_variance"])
    unexplained = Decimal(head["unexplained_amount"])
    explained = absolute - unexplained
    attributed_at, computed_at = await variance_repo.attribution_freshness(session, run_id)
    # The same pattern P11's `engineStale` and P12's `variancesStale` use: a flag with an action,
    # not a refusal. A recomputed comparison leaves the old explanation describing numbers that
    # have moved, and a confident "99.8% explained" over stale links is worse than a warning.
    stale = attributed_at is not None and computed_at is not None and computed_at > attributed_at
    failures = await repo.rule_failures(session, run_id)
    return {
        **head,
        "explained_amount": explained,
        "attributed_at": attributed_at,
        "attribution_stale": stale,
        "rule_failures": failures,
        "attributed": int(head["link_count"]) > 0,
        # Zero links means nothing has run, which is not the same as "nothing could be explained".
        # Reported as null rather than as 0% or 100%, both of which would be a claim.
        "explained_pct": (
            f"{(explained / absolute):.4f}" if absolute and head["link_count"] else None
        ),
        "by_cause": await repo.by_cause(session, run_id),
    }


async def lines(
    session: AsyncSession,
    variance_id: UUID,
    *,
    limit: int,
    cause: str | None,
    raw_cursor: str | None,
) -> dict[str, Any]:
    owner = await repo.variance_owner(session, variance_id)
    if owner is None:
        raise NotFound("That variance does not exist.")
    if cause is not None and cause not in LINK_CAUSES:
        raise MalformedRequest(
            "cause must be one of " + ", ".join(LINK_CAUSES) + ".",
            fields={"cause": "Unknown value."},
        )
    cursor_row = (
        None
        if raw_cursor is None
        else cursor.decode(
            raw_cursor,
            parse=lambda parts: int(parts[0]),
            message="That page link is not valid. Go back to the first page of lines.",
        )
    )
    rows = await repo.lines(
        session, variance_id, limit=limit + 1, cause=cause, cursor_row=cursor_row
    )
    shown = rows[:limit]
    next_cursor = (
        cursor.encode(str(shown[-1]["source_row_number"] or 0))
        if len(rows) > limit and shown
        else None
    )
    return {"rows": shown, "next_cursor": next_cursor}


def _evidence(link: dict[str, Any]) -> dict[str, Any]:
    raw: dict[str, Any] | None = link["evidence"]
    return raw or {}


async def glass_export(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """The attributed-variance document, assembled in three queries rather than N+1.

    Conservation survives serialisation: each variance's `sourceLines` contributions plus its
    residue sum to its `varianceAmount`, because both come from the same links the job wrote.
    """
    await _run_or_404(session, run_id)
    head = await repo.export_head(session, run_id)
    if head is None:
        raise VariancesNotComputed(
            "This run has not been compared against a legacy export yet. Compare it first."
        )
    summary_row = await repo.summary(session, run_id)
    explained = Decimal(summary_row["explained_amount"])
    unexplained = Decimal(summary_row["unexplained_amount"])
    total = explained + unexplained

    lines_by_variance: dict[Any, list[dict[str, Any]]] = {}
    for row in await repo.export_lines(session, run_id):
        # The residue link names no line, so it is not a source line - it is reported once as
        # `residueAmount`. Including it here as well double-counted it, which cost conservation on
        # exactly the six variances that have one: 12 of 18 survived serialisation instead of 18.
        if str(row["root_cause"]) == UNEXPLAINED:
            continue
        lines_by_variance.setdefault(row["variance_id"], []).append(row)

    variances: list[dict[str, Any]] = []
    for row in await repo.export_variances(session, run_id):
        links = lines_by_variance.get(row["variance_id"], [])
        shown = links[:MAX_GLASS_SOURCE_LINES]
        detail: dict[str, Any] = row["detail"] or {}
        variances.append(
            {
                "programExternalRef": row["external_ref"],
                "programName": str(row["program_name"]),
                "periodStart": row["period_start"].isoformat(),
                "periodEnd": row["period_end"].isoformat(),
                "legacyAmount": f"{row['legacy_amount']:f}",
                "calculatedAmount": f"{row['calculated_amount']:f}",
                "varianceAmount": f"{row['variance_amount']:f}",
                "presence": str(row["presence"]),
                "rootCause": str(row["root_cause"]),
                "byCause": detail.get("byCause", []),
                "residueAmount": str(detail.get("residueAmount", "0.00")),
                "sourceLineCount": len(links),
                "sourceLinesTruncated": len(links) > MAX_GLASS_SOURCE_LINES,
                "sourceLines": [
                    {
                        "invoiceNo": link["invoice_no"],
                        "invoiceDate": (
                            None
                            if link["invoice_date"] is None
                            else link["invoice_date"].isoformat()
                        ),
                        "distItemId": link["dist_item_id"],
                        "sourceRowNumber": link["source_row_number"],
                        "rootCause": str(link["root_cause"]),
                        "contributionAmount": f"{link['contribution_amount']:f}",
                        "evidence": _evidence(link).get("note"),
                        "estimated": bool(_evidence(link).get("estimated", False)),
                    }
                    for link in shown
                ],
            }
        )

    return {
        "glassExportVersion": GLASS_EXPORT_VERSION,
        "customerName": str(head["customer_name"]),
        "ingestRunId": str(run_id),
        "legacySourceFile": head["legacy_source_file"],
        "periodStart": head["period_start"].isoformat() if head["period_start"] else None,
        "periodEnd": head["period_end"].isoformat() if head["period_end"] else None,
        "canonicalSchemaVersion": CANONICAL_SCHEMA_VERSION,
        "engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION, "standIn": True},
        "explainedPct": (f"{(explained / total):.4f}" if total else None),
        "totalVarianceAmount": f"{head['absolute_variance']:f}",
        "netVarianceAmount": f"{head['net_variance']:f}",
        "unexplainedAmount": f"{unexplained:f}",
        "currency": "USD",
        "variances": variances,
    }
