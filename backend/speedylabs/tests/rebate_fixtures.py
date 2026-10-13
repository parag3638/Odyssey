"""Shared synthetic scaffolding for rebate tests that need a customer, canonical rows and
programs smaller and more targeted than the 48,150-row demo file - mid-run failure, effective-date
clipping, brand resolution, negative periods, `MAX_CALC_LINES`. One place, not copy-pasted into
every test module that needs a customer (`tests/live.py`'s own convention, extended here because
those tests need `customer_manufacturers` and `rebate_programs` rows too, which `tests/live.py`'s
plain `customer` fixture does not create).

Every row is inserted through the admin role directly, bypassing the upload/materialise pipeline
on purpose: these tests are about `run_calculation`'s own behaviour, not about re-proving ingest
works (that is Phases 1-9's job).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import sql

from src.lib.ids import uuid7

ADMIN_DB_URL = "postgresql://speedy_admin:speedy@127.0.0.1:5432/speedy_onboard"


def admin_conn() -> psycopg.Connection[tuple[Any, ...]]:
    return psycopg.connect(ADMIN_DB_URL, autocommit=True)


@pytest.fixture
def synthetic_customer() -> Generator[str]:
    """A customer with no programs and no runs yet - self-deleting, cascades to everything."""
    customer_id = str(uuid7())
    with admin_conn() as conn:
        conn.execute(
            "insert into customers (id, name, slug) values (%s, %s, %s)",
            (customer_id, "Synthetic Test Co", f"synthetic-{uuid.uuid4().hex[:8]}"),
        )
    try:
        yield customer_id
    finally:
        with admin_conn() as conn:
            conn.execute("delete from customers where id = %s", (customer_id,))


def make_reference(
    customer_id: str,
    name: str,
    *,
    brand_values: list[str] | None = None,
    direct_vendor_values: list[str] | None = None,
    redistributor_only: bool = False,
) -> None:
    with admin_conn() as conn:
        conn.execute(
            """
            insert into customer_manufacturers
                (id, customer_id, name, brand_values, direct_vendor_values, redistributor_only)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid7()),
                customer_id,
                name,
                brand_values if brand_values is not None else [name],
                direct_vendor_values or [],
                redistributor_only,
            ),
        )


def make_program(
    customer_id: str,
    *,
    name: str,
    brand_manufacturer: str,
    kind: str = "per_case_volume",
    uom_basis: str = "CS",
    rate: str = "1.000000",
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = None,
    external_ref: str | None = None,
) -> str:
    program_id = str(uuid7())
    with admin_conn() as conn:
        conn.execute(
            """
            insert into rebate_programs
                (id, customer_id, name, kind, brand_manufacturer, rate, uom_basis,
                 effective_from, effective_to, external_ref)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                program_id,
                customer_id,
                name,
                kind,
                brand_manufacturer,
                Decimal(rate),
                uom_basis,
                effective_from,
                effective_to,
                external_ref,
            ),
        )
    return program_id


def make_source_file(customer_id: str) -> str:
    source_file_id = str(uuid7())
    with admin_conn() as conn:
        conn.execute(
            """
            insert into source_files
                (id, customer_id, kind, original_filename, stored_name, byte_size, sha256,
                 sniffed_type, format, row_count, uploader_ip_hash)
            values (%s, %s, 'erp_invoice_lines', 'synthetic.csv', %s, 10, %s,
                    'text/csv', 'csv', 0, 'test-hash')
            """,
            (source_file_id, customer_id, f"synthetic-{uuid.uuid4().hex}.csv", uuid.uuid4().hex),
        )
    return source_file_id


def make_ingest_run(customer_id: str, *, status: str = "materialized") -> str:
    run_id = str(uuid7())
    source_file_id = make_source_file(customer_id)
    with admin_conn() as conn:
        conn.execute(
            """
            insert into ingest_runs (id, source_file_id, customer_id, status)
            values (%s, %s, %s, %s)
            """,
            (run_id, source_file_id, customer_id, status),
        )
    return run_id


_row_counter = 0


def canonical_line(
    run_id: str,
    *,
    brand_manufacturer: str,
    uom: str,
    qty: str,
    pack_size: int | None = None,
    catch_weight_lbs: str | None = None,
    invoice_date: date = date(2026, 1, 15),
    source_row_number: int | None = None,
) -> dict[str, Any]:
    """One `canonical_lines` row, defaulted for everything the rebate engine never reads."""
    global _row_counter
    _row_counter += 1
    row_number = source_row_number if source_row_number is not None else _row_counter
    return {
        "id": str(uuid7()),
        "ingest_run_id": run_id,
        "source_row_number": row_number,
        "invoice_no": f"INV-{row_number:06d}",
        "invoice_date": invoice_date,
        "customer_id": "C100",
        "customer_name": "Synthetic Operator",
        "dist_item_id": "500000",
        "mfr_item_code": "MFR-1",
        "brand_manufacturer": brand_manufacturer,
        "invoice_vendor": "300000",
        "qty": Decimal(qty),
        "uom": uom,
        "pack_size": pack_size,
        "catch_weight_lbs": None if catch_weight_lbs is None else Decimal(catch_weight_lbs),
        "unit_price": Decimal("10.000000"),
        "ext_amount": Decimal("100.00"),
        "is_deviated": False,
        "deviated_price": None,
    }


def insert_canonical_lines(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    # Identifiers composed, not an f-string: the column list is internal (this module's own
    # `canonical_line` dict keys), but every query in this codebase is built this way regardless
    # (BACKEND.md §11, "string-built SQL"), and `run.py`'s `_copy_statement` is the precedent.
    statement = sql.SQL("insert into canonical_lines ({}) values ({})").format(
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
    )
    with admin_conn() as conn, conn.cursor() as cur:
        cur.executemany(statement, rows)
