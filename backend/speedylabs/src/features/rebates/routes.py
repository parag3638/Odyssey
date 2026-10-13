"""The rebate routes. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.rebates import service
from src.features.rebates.codes import SKIPPED_REASON_SENTENCES
from src.features.rebates.reference_engine import ENGINE_NAME, ENGINE_VERSION
from src.features.rebates.schemas import (
    BrandCoverageOut,
    CalculateAcceptedEnvelope,
    CalculateAcceptedOut,
    EngineOut,
    MoneyOut,
    ProgramOutcomeOut,
    RebateCalcLineOut,
    RebateCalcLinesEnvelope,
    RebateCalcOut,
    RebateCalcsEnvelope,
    RebateProgramOut,
    RebateProgramsEnvelope,
    RebateSummaryEnvelope,
    RebateSummaryOut,
)
from src.lib import db, idempotency
from src.lib.money import DEFAULT_CURRENCY, Money, serialise

router = APIRouter(prefix="/v1", tags=["rebates"])

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def _limit(requested: int | None) -> int:
    """§3: default 25, max 100. Clamped rather than refused - the runs list's rule."""
    if requested is None:
        return DEFAULT_LIMIT
    return max(1, min(requested, MAX_LIMIT))


def _money(value: Any) -> MoneyOut:
    return MoneyOut(**Money(amount=value, currency=DEFAULT_CURRENCY).to_api())


def _engine(name: Any, version: Any) -> EngineOut:
    return EngineOut(name=str(name), version=str(version))


@router.post("/ingest-runs/{run_id}/rebate-calcs", status_code=202)
async def start_calculation(
    run_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> CalculateAcceptedEnvelope:
    """Queue a rebate calculation over this run's canonical rows.

    `202` because the work happens in a job: 48,150 lines is not a request-path amount of work even
    at 0.8 seconds, and the status URL is how a client follows it.

    Honours `Idempotency-Key`. The queueing lock alone already makes a double POST harmless, but a
    client that sent a key believes it is getting idempotency, and §3 says an action with side
    effects honours it.
    """
    data = await service.start_calculation(
        session,
        run_id,
        idempotency_key=idempotency.clean_key(request.headers.get("Idempotency-Key")),
    )
    return CalculateAcceptedEnvelope(
        data=CalculateAcceptedOut(
            ingest_run_id=data["ingest_run_id"],
            status=str(data["status"]),
            status_url=str(data["status_url"]),
        )
    )


@router.get("/ingest-runs/{run_id}/rebate-summary")
async def get_summary(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> RebateSummaryEnvelope:
    """The engine, the outcome per program, and what became of every brand on the file.

    A flat list of amounts cannot carry the three things that make a zero legible - which engine
    computed it, whether each program actually ran, and whether any lines were claimed by nobody -
    so they live here.
    """
    data = await service.summary(session, run_id)
    return RebateSummaryEnvelope(
        data=RebateSummaryOut(
            engine=_engine(data["engine"]["name"], data["engine"]["version"]),
            engine_stale=bool(data["engine_stale"]),
            current_engine=_engine(
                data["current_engine"]["name"], data["current_engine"]["version"]
            ),
            calculated=bool(data["calculated"]),
            calculated_at=data["calculated_at"],
            total_amount=_money(data["total_amount"]),
            contributed_line_count=int(data["contributed_line_count"]),
            skipped_line_count=int(data["skipped_line_count"]),
            out_of_scope_line_count=int(data["out_of_scope_line_count"]),
            programs=[
                ProgramOutcomeOut(
                    program_id=row["program_id"],
                    external_ref=row["external_ref"],
                    program_name=str(row["program_name"]),
                    kind=str(row["kind"]),
                    outcome=row["outcome"],
                    reason=row["reason"],
                    lines_examined=int(row["lines_examined"]),
                    lines_matched=int(row["lines_matched"]),
                    periods_written=int(row["periods_written"]),
                    amount=_money(row["amount"]),
                    engine=_engine(row["engine_name"], row["engine_version"]),
                )
                for row in data["programs"]
            ],
            brands=[
                BrandCoverageOut(
                    brand_value=str(row["brand_value"]),
                    manufacturer_name=row["manufacturer_name"],
                    resolution=row["resolution"],
                    line_count=int(row["line_count"]),
                )
                for row in data["brands"]
            ],
            brands_truncated=bool(data["brands_truncated"]),
        )
    )


@router.get("/ingest-runs/{run_id}/rebate-calcs")
async def list_calcs(
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> RebateCalcsEnvelope:
    """One row per program per calendar month, with the engine that produced each."""
    data = await service.calcs(session, run_id, limit=_limit(limit), raw_cursor=cursor)
    return RebateCalcsEnvelope(
        data=[
            RebateCalcOut(
                rebate_calc_id=row["rebate_calc_id"],
                program_id=row["program_id"],
                external_ref=row["external_ref"],
                program_name=str(row["program_name"]),
                kind=str(row["kind"]),
                period_start=row["period_start"],
                period_end=row["period_end"],
                basis_qty=serialise(row["basis_qty"]),
                basis_uom=str(row["uom_basis"]),
                amount=_money(row["amount"]),
                contributed_line_count=int(row["contributed_line_count"]),
                skipped_line_count=int(row["skipped_line_count"]),
                out_of_scope_line_count=int(row["out_of_scope_line_count"]),
                engine=_engine(row["engine_name"], row["engine_version"]),
            )
            for row in data["rows"]
        ],
        next_cursor=data["next_cursor"],
    )


@router.get("/rebate-calcs/{calc_id}/lines")
async def list_calc_lines(
    calc_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    status: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> RebateCalcLinesEnvelope:
    """The lines behind one period's amount, filterable by status.

    `?status=skipped` exists so the drill-down does not page 2,251 rows to find the 14 that need
    fixing.
    """
    data = await service.lines(
        session, calc_id, limit=_limit(limit), status=status, raw_cursor=cursor
    )
    return RebateCalcLinesEnvelope(
        data=[
            RebateCalcLineOut(
                canonical_line_id=row["canonical_line_id"],
                source_row_number=int(row["source_row_number"]),
                invoice_no=str(row["invoice_no"]),
                invoice_date=row["invoice_date"],
                dist_item_id=str(row["dist_item_id"]),
                qty=serialise(row["qty"]),
                uom=str(row["uom"]),
                pack_size=None if row["pack_size"] is None else int(row["pack_size"]),
                catch_weight_lbs=(
                    None if row["catch_weight_lbs"] is None else serialise(row["catch_weight_lbs"])
                ),
                line_status=row["line_status"],
                skipped_reason=row["skipped_reason"],
                skipped_detail=SKIPPED_REASON_SENTENCES.get(str(row["skipped_reason"]))
                if row["skipped_reason"]
                else None,
                basis_contribution=serialise(row["basis_contribution"]),
                amount_contribution=_money(row["amount_contribution"]),
            )
            for row in data["rows"]
        ],
        next_cursor=data["next_cursor"],
    )


@router.get("/customers/{customer_id}/rebate-programs")
async def list_programs(
    customer_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> RebateProgramsEnvelope:
    """The customer's programs and their commercial terms. Seeded data, never request input."""
    rows = await service.programs(session, customer_id)
    return RebateProgramsEnvelope(
        data=[
            RebateProgramOut(
                program_id=row["program_id"],
                external_ref=row["external_ref"],
                name=str(row["name"]),
                kind=str(row["kind"]),
                brand_manufacturer=str(row["brand_manufacturer"]),
                rate=serialise(row["rate"]),
                rate_currency=DEFAULT_CURRENCY,
                uom_basis=str(row["uom_basis"]),
                effective_from=row["effective_from"],
                effective_to=row["effective_to"],
            )
            for row in rows
        ],
        next_cursor=None,
    )


#: Re-exported so the UI's engine badge and the published document cannot disagree about the name.
CURRENT_ENGINE = (ENGINE_NAME, ENGINE_VERSION)
