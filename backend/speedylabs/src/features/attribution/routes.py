"""The attribution routes. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.attribution import service
from src.features.attribution.schemas import (
    AttributeAcceptedEnvelope,
    AttributeAcceptedOut,
    AttributedLineOut,
    AttributedLinesEnvelope,
    AttributionSummaryEnvelope,
    AttributionSummaryOut,
    CauseShareOut,
    MoneyOut,
    RuleFailureOut,
)
from src.features.variance.codes import LINK_CAUSE_TITLES
from src.lib import db, idempotency
from src.lib.money import DEFAULT_CURRENCY, Money, serialise

router = APIRouter(prefix="/v1", tags=["attribution"])

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def _limit(requested: int | None) -> int:
    if requested is None:
        return DEFAULT_LIMIT
    return max(1, min(requested, MAX_LIMIT))


def _money(value: Any) -> MoneyOut:
    return MoneyOut(**Money(amount=value, currency=DEFAULT_CURRENCY).to_api())


@router.post("/ingest-runs/{run_id}/variances/attribute", status_code=202)
async def attribute(
    run_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> AttributeAcceptedEnvelope:
    """Explain this run's variances, line by line."""
    data = await service.start_attribution(
        session,
        run_id,
        idempotency_key=idempotency.clean_key(request.headers.get("Idempotency-Key")),
    )
    return AttributeAcceptedEnvelope(
        data=AttributeAcceptedOut(
            ingest_run_id=data["ingest_run_id"],
            status=str(data["status"]),
            status_url=str(data["status_url"]),
        )
    )


@router.get("/ingest-runs/{run_id}/variance-summary/attribution")
async def get_attribution_summary(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> AttributionSummaryEnvelope:
    """How much of the variance is explained, and by what.

    `byCause` is grouped on the **link**, never on `variances.root_cause`: `duplicate_line` is the
    dominant cause of zero variances and appears in fourteen, so grouping on the variance would
    lose a whole cause from the chart.
    """
    data = await service.summary(session, run_id)
    return AttributionSummaryEnvelope(
        data=AttributionSummaryOut(
            attributed=bool(data["attributed"]),
            variance_count=int(data["variance_count"]),
            link_count=int(data["link_count"]),
            explained_pct=data["explained_pct"],
            explained_amount=_money(data["explained_amount"]),
            unexplained_amount=_money(data["unexplained_amount"]),
            absolute_variance=_money(data["absolute_variance"]),
            attributed_at=data["attributed_at"],
            attribution_stale=bool(data["attribution_stale"]),
            rule_failures=[
                RuleFailureOut(rule=str(f["rule"]), line_count=int(f["lineCount"]))
                for f in data["rule_failures"]
            ],
            by_cause=[
                CauseShareOut(
                    root_cause=str(row["root_cause"]),
                    title=LINK_CAUSE_TITLES.get(str(row["root_cause"]), str(row["root_cause"])),
                    net_amount=_money(row["net_amount"]),
                    absolute_amount=_money(row["absolute_amount"]),
                    link_count=int(row["link_count"]),
                    line_count=int(row["line_count"]),
                )
                for row in data["by_cause"]
            ],
        )
    )


@router.get("/variances/{variance_id}/lines")
async def list_variance_lines(
    variance_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    cause: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> AttributedLinesEnvelope:
    """The source lines behind one variance, each with its contribution and the evidence."""
    data = await service.lines(
        session, variance_id, limit=_limit(limit), cause=cause, raw_cursor=cursor
    )
    return AttributedLinesEnvelope(
        data=[
            AttributedLineOut(
                link_id=row["link_id"],
                root_cause=str(row["root_cause"]),
                title=LINK_CAUSE_TITLES.get(str(row["root_cause"]), str(row["root_cause"])),
                contribution_amount=_money(row["contribution_amount"]),
                evidence=row["evidence"],
                canonical_line_id=row["canonical_line_id"],
                source_row_number=row["source_row_number"],
                invoice_no=row["invoice_no"],
                invoice_date=row["invoice_date"],
                dist_item_id=row["dist_item_id"],
                qty=None if row["qty"] is None else serialise(row["qty"]),
                uom=row["uom"],
            )
            for row in data["rows"]
        ],
        next_cursor=data["next_cursor"],
    )


@router.get("/ingest-runs/{run_id}/parallel-run/glass-export.json")
async def glass_export(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> JSONResponse:
    """The attributed variances, in the documented export shape.

    Served as an attachment so a browser saves it rather than rendering it. The shape is
    **proposed**, not a contract agreed with any downstream tool - `canonical/GLASS_EXPORT.md`
    says so, and so does the About page.
    """
    document = await service.glass_export(session, run_id)
    return JSONResponse(
        content=document,
        headers={
            "Content-Disposition": (f'attachment; filename="variance-attribution-{run_id}.json"')
        },
    )
