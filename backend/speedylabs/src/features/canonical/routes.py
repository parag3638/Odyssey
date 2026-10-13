"""The canonical-schema and canonical-output routes. Parse, delegate, shape (§2)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.canonical import export, service
from src.features.canonical.schemas import (
    CanonicalFieldOut,
    CanonicalLineOut,
    CanonicalLinesEnvelope,
    CanonicalSchemaEnvelope,
    CanonicalSchemaOut,
    MaterializationErrorOut,
    MaterializationErrorsEnvelope,
    MaterializeAcceptedEnvelope,
    MaterializeAcceptedOut,
    OutputSummaryEnvelope,
    OutputSummaryOut,
)
from src.lib import db
from src.lib.errors import MalformedRequest

router = APIRouter(prefix="/v1", tags=["canonical"])


@router.get("/canonical-schema")
async def get_canonical_schema() -> CanonicalSchemaEnvelope:
    """The canonical invoice-line contract: version and fields, in CSV column order.

    Public and unauthenticated on purpose - it is a published contract, the same one in
    `canonical/invoice_line.schema.json`, and a customer's IT team needs it before they
    have anything else.
    """
    data = service.canonical_schema()
    return CanonicalSchemaEnvelope(
        data=CanonicalSchemaOut(
            version=data["version"],
            fields=[CanonicalFieldOut(**field) for field in data["fields"]],
        )
    )


@router.post("/ingest-runs/{run_id}/materialize", status_code=202)
async def materialize_run(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> MaterializeAcceptedEnvelope:
    """202: the mapping is accepted and the rows are being written.

    The completeness check has already happened by the time this returns, so a `202` means
    "this will produce rows", not "we will find out later".
    """
    accepted = await service.request_materialization(session, run_id)
    return MaterializeAcceptedEnvelope(
        data=MaterializeAcceptedOut(
            ingest_run_id=run_id,
            status=str(accepted["status"]),
            status_url=f"/v1/ingest-runs/{run_id}",
        )
    )


@router.get("/ingest-runs/{run_id}/output")
async def get_output_summary(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> OutputSummaryEnvelope:
    """Rows written, rows rejected and the schema version the rows were written against."""
    summary = await service.output_summary(session, run_id)
    return OutputSummaryEnvelope(
        data=OutputSummaryOut(
            ingest_run_id=run_id,
            status=str(summary["status"]),
            canonical_schema_version=str(summary["canonical_schema_version"]),
            rows_written=int(summary["rows_written"]),
            rows_rejected=int(summary["rows_rejected"]),
            materialized_at=summary["materialized_at"],
        )
    )


@router.get("/ingest-runs/{run_id}/canonical-lines")
async def list_canonical_lines(
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    row_numbers: Annotated[str | None, Query(alias="rowNumbers")] = None,
) -> CanonicalLinesEnvelope:
    """One page of canonical rows, in source order. Money fields are decimal strings.

    `rowNumbers=12,48,91` is the readiness drill-down: exactly those rows, which is how a
    finding's example rows become the rows themselves. It is **not** combined with `cursor` -
    an explicit list has no next page, and silently ignoring one of the two parameters would
    leave a caller believing they had paginated a filter.
    """
    if row_numbers is not None:
        if cursor is not None:
            raise MalformedRequest(
                "Ask for either a page (cursor) or specific rows (rowNumbers), not both."
            )
        page = await service.canonical_lines_by_number(
            session, run_id, row_numbers=service.parse_row_numbers(row_numbers)
        )
        return CanonicalLinesEnvelope(data=[_line(row) for row in page["rows"]])

    page = await service.canonical_lines(
        session,
        run_id,
        limit=limit,
        cursor=service.decode_cursor(cursor) if cursor else None,
    )
    return CanonicalLinesEnvelope(
        data=[_line(row) for row in page["rows"]], next_cursor=page["next_cursor"]
    )


@router.get("/ingest-runs/{run_id}/materialization-errors")
async def list_materialization_errors(
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> MaterializationErrorsEnvelope:
    """The rows that could not be made to fit, with the field, the value and the reason."""
    page = await service.materialization_errors(
        session,
        run_id,
        limit=limit,
        cursor=service.decode_cursor(cursor) if cursor else None,
    )
    return MaterializationErrorsEnvelope(
        data=[
            MaterializationErrorOut(
                source_row_number=int(row["source_row_number"]),
                canonical_field=str(row["canonical_field"]),
                raw_value=str(row["raw_value"]),
                error_code=str(row["error_code"]),
                message=str(row["message"]),
            )
            for row in page["rows"]
        ],
        next_cursor=page["next_cursor"],
    )


@router.get("/ingest-runs/{run_id}/canonical-file")
async def download_canonical_file(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> StreamingResponse:
    """The canonical CSV, streamed from the database.

    The run is looked up first so a 404 is a 404, with a JSON envelope: once a
    `StreamingResponse` has begun there is no way to change the status code, and a client
    would be handed a `200` with an error inside a CSV.
    """
    summary = await service.download_target(session, run_id)
    return StreamingResponse(
        export.iter_csv(run_id, max_rows=config.load().max_upload_rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{export.filename(str(summary["customer_name"]), run_id)}"'
            ),
            # The export is a snapshot of rows that can be re-materialised; a cached copy
            # would show yesterday's numbers in a tab the user never reloaded.
            "Cache-Control": "no-store",
        },
    )


def _line(row: dict[str, Any]) -> CanonicalLineOut:
    """One row, with every decimal rendered exactly as the CSV renders it.

    The same `export.cell` the download uses, so the preview a user checks and the file they
    hand to Speedy cannot disagree about a value.
    """
    return CanonicalLineOut(
        source_row_number=int(row["source_row_number"]),
        invoice_no=str(row["invoice_no"]),
        invoice_date=row["invoice_date"],
        customer_id=str(row["customer_id"]),
        customer_name=str(row["customer_name"]),
        dist_item_id=str(row["dist_item_id"]),
        mfr_item_code=str(row["mfr_item_code"]),
        brand_manufacturer=str(row["brand_manufacturer"]),
        invoice_vendor=str(row["invoice_vendor"]),
        qty=export.cell("qty", row["qty"]),
        uom=str(row["uom"]),
        pack_size=None if row["pack_size"] is None else int(row["pack_size"]),
        catch_weight_lbs=_optional("catch_weight_lbs", row),
        unit_price=export.cell("unit_price", row["unit_price"]),
        ext_amount=export.cell("ext_amount", row["ext_amount"]),
        is_deviated=bool(row["is_deviated"]),
        deviated_price=_optional("deviated_price", row),
    )


def _optional(name: str, row: dict[str, Any]) -> str | None:
    return None if row[name] is None else export.cell(name, row[name])
