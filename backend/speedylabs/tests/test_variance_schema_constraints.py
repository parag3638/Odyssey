"""`legacy_rebate_lines` (migration `0025`) and `variances` (migration `0026`) against the real
schema - step 12.1's own denial/acceptance criteria. Same convention as
`tests/test_schema_constraints.py`: inserts through the `speedy` app role, every row
self-deleting via its customer, bad data proven rejected rather than merely absent from the
migration file (BACKEND.md §5).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Json

from src.lib.ids import uuid7

# Reuses `test_schema_constraints.py`'s own "insert a valid customer/program" fixtures rather
# than a second copy (BACKEND.md §9, rule of two): that file already builds and proves the
# `customer_manufacturers` reference row `rebate_programs_brand_fk` (migration 0023) requires.
# Both names are private to signal "test-module-internal, not a public helper" - reusing them
# here is deliberate, not an accident pyright should flag.
from tests.test_schema_constraints import (
    _insert_program,  # pyright: ignore[reportPrivateUsage]
    _insert_reference,  # pyright: ignore[reportPrivateUsage]
)

APP_URL = "postgresql://speedy:speedy@127.0.0.1:5432/speedy_onboard"


def _slug(label: str) -> str:
    return f"p12-{label}-{uuid.uuid4().hex[:8]}"


@contextmanager
def _customer(conn: psycopg.Connection[object], label: str) -> Generator[uuid.UUID]:
    customer_id = uuid7()
    conn.execute(
        "insert into customers (id, name, slug) values (%s, %s, %s)",
        (customer_id, "P12 Test Co", _slug(label)),
    )
    _insert_reference(conn, customer_id)
    conn.commit()
    try:
        yield customer_id
    finally:
        conn.rollback()
        conn.execute("delete from customers where id = %s", (customer_id,))
        conn.commit()


def _insert_source_file(
    conn: psycopg.Connection[object], customer_id: uuid.UUID, *, kind: str = "legacy_rebate"
) -> uuid.UUID:
    file_id = uuid7()
    conn.execute(
        """
        insert into source_files
            (id, customer_id, kind, original_filename, stored_name, byte_size, sha256,
             sniffed_type, format, row_count, uploader_ip_hash)
        values (%s, %s, %s, 'test.csv', %s, 10, %s, 'text/csv', 'csv', 1, 'test-hash')
        """,
        (file_id, customer_id, kind, f"test-{uuid.uuid4().hex}.csv", uuid.uuid4().hex),
    )
    return file_id


def _insert_ingest_run(
    conn: psycopg.Connection[object], customer_id: uuid.UUID, source_file_id: uuid.UUID
) -> uuid.UUID:
    run_id = uuid7()
    conn.execute(
        "insert into ingest_runs (id, source_file_id, customer_id, status) "
        "values (%s, %s, %s, 'queued')",
        (run_id, source_file_id, customer_id),
    )
    return run_id


VALID_PERIOD_START = date(2026, 1, 1)
VALID_PERIOD_END = date(2026, 1, 31)


def _insert_legacy_line(
    conn: psycopg.Connection[object],
    *,
    customer_id: uuid.UUID,
    source_file_id: uuid.UUID,
    program_id: uuid.UUID | None,
    match_status: str = "matched",
    period_start: date = VALID_PERIOD_START,
    period_end: date = VALID_PERIOD_END,
    source_row_number: int = 2,
    amount: Decimal = Decimal("10.00"),
    program_external_ref: str = "PG-TEST-1",
) -> uuid.UUID:
    line_id = uuid7()
    conn.execute(
        """
        insert into legacy_rebate_lines
            (id, customer_id, source_file_id, source_row_number, program_external_ref,
             program_name_text, program_id, period_start, period_end, amount, raw, match_status)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            line_id,
            customer_id,
            source_file_id,
            source_row_number,
            program_external_ref,
            None,
            program_id,
            period_start,
            period_end,
            amount,
            Json({}),
            match_status,
        ),
    )
    return line_id


def _insert_variance(
    conn: psycopg.Connection[object],
    *,
    ingest_run_id: uuid.UUID,
    program_id: uuid.UUID,
    legacy_source_file_id: uuid.UUID,
    legacy_amount: Decimal = Decimal("10.00"),
    calculated_amount: Decimal = Decimal("4.00"),
    variance_amount: Decimal | None = None,
    presence: str = "both",
    period_start: date = VALID_PERIOD_START,
    period_end: date = VALID_PERIOD_END,
    explained: bool = False,
    root_cause: str = "unattributed",
) -> uuid.UUID:
    row_id = uuid7()
    if variance_amount is None:
        variance_amount = legacy_amount - calculated_amount
    conn.execute(
        """
        insert into variances
            (id, ingest_run_id, program_id, legacy_source_file_id, period_start, period_end,
             legacy_amount, calculated_amount, variance_amount, presence, root_cause, explained)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            ingest_run_id,
            program_id,
            legacy_source_file_id,
            period_start,
            period_end,
            legacy_amount,
            calculated_amount,
            variance_amount,
            presence,
            root_cause,
            explained,
        ),
    )
    return row_id


# --- legacy_rebate_lines --------------------------------------------------------------------------


def test_a_valid_matched_legacy_line_is_accepted(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "legline-ok") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        file_id = _insert_source_file(conn, cid)
        _insert_legacy_line(conn, customer_id=cid, source_file_id=file_id, program_id=program_id)
        conn.commit()


def test_rejects_matched_with_a_null_program_id(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "legline-m") as cid:
        file_id = _insert_source_file(conn, cid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_legacy_line(
                conn,
                customer_id=cid,
                source_file_id=file_id,
                program_id=None,
                match_status="matched",
            )
        conn.rollback()


def test_rejects_unmatched_with_a_non_null_program_id(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "legline-u") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        file_id = _insert_source_file(conn, cid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_legacy_line(
                conn,
                customer_id=cid,
                source_file_id=file_id,
                program_id=program_id,
                match_status="unmatched_program",
            )
        conn.rollback()


def test_rejects_a_match_status_outside_the_allowed_set(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "legline-s") as cid:
        file_id = _insert_source_file(conn, cid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_legacy_line(
                conn,
                customer_id=cid,
                source_file_id=file_id,
                program_id=None,
                match_status="ambiguous",
            )
        conn.rollback()


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 1, 15), date(2026, 1, 31)),  # start not month-start
        (date(2026, 1, 1), date(2026, 1, 30)),  # end not month-end
    ],
)
def test_rejects_a_period_that_is_not_a_whole_calendar_month(
    require_live_db: None, start: date, end: date
) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "legline-p") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        file_id = _insert_source_file(conn, cid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_legacy_line(
                conn,
                customer_id=cid,
                source_file_id=file_id,
                program_id=program_id,
                period_start=start,
                period_end=end,
            )
        conn.rollback()


def test_rejects_a_zero_source_row_number(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "legline-r") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        file_id = _insert_source_file(conn, cid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_legacy_line(
                conn,
                customer_id=cid,
                source_file_id=file_id,
                program_id=program_id,
                source_row_number=0,
            )
        conn.rollback()


def test_a_negative_amount_is_accepted(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "legline-neg") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        file_id = _insert_source_file(conn, cid)
        _insert_legacy_line(
            conn,
            customer_id=cid,
            source_file_id=file_id,
            program_id=program_id,
            amount=Decimal("-100.00"),
        )
        conn.commit()


def test_the_unique_key_rejects_a_second_row_for_the_same_file_program_period(
    require_live_db: None,
) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "legline-dup") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        file_id = _insert_source_file(conn, cid)
        _insert_legacy_line(conn, customer_id=cid, source_file_id=file_id, program_id=program_id)
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_legacy_line(
                conn, customer_id=cid, source_file_id=file_id, program_id=program_id
            )
        conn.rollback()


# --- variances --------------------------------------------------------------------------------


def test_a_valid_variance_row_is_accepted(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-ok") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        _insert_variance(
            conn, ingest_run_id=run_id, program_id=program_id, legacy_source_file_id=legacy_file
        )
        conn.commit()


def test_rejects_an_inconsistent_arithmetic_row(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-math") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_variance(
                conn,
                ingest_run_id=run_id,
                program_id=program_id,
                legacy_source_file_id=legacy_file,
                legacy_amount=Decimal("10.00"),
                calculated_amount=Decimal("4.00"),
                variance_amount=Decimal("7.00"),  # should be 6.00
            )
        conn.rollback()


def test_a_negative_variance_is_accepted_the_real_demo_row(require_live_db: None) -> None:
    """The exact `PG-NVF-CS 2026-03` shape, pinned so nobody adds a non-negativity constraint."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-neg") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-NVF-CS")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        _insert_variance(
            conn,
            ingest_run_id=run_id,
            program_id=program_id,
            legacy_source_file_id=legacy_file,
            legacy_amount=Decimal("24746.25"),
            calculated_amount=Decimal("26495.00"),
            variance_amount=Decimal("-1748.75"),
        )
        conn.commit()


def test_an_all_zero_both_row_is_accepted(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-zero") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        _insert_variance(
            conn,
            ingest_run_id=run_id,
            program_id=program_id,
            legacy_source_file_id=legacy_file,
            legacy_amount=Decimal("0.00"),
            calculated_amount=Decimal("0.00"),
            variance_amount=Decimal("0.00"),
            presence="both",
        )
        conn.commit()


def test_rejects_legacy_only_carrying_a_nonzero_calculated_amount(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-lo") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_variance(
                conn,
                ingest_run_id=run_id,
                program_id=program_id,
                legacy_source_file_id=legacy_file,
                legacy_amount=Decimal("10.00"),
                calculated_amount=Decimal("1.00"),
                variance_amount=Decimal("9.00"),
                presence="legacy_only",
            )
        conn.rollback()


def test_rejects_calculated_only_carrying_a_nonzero_legacy_amount(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-co") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_variance(
                conn,
                ingest_run_id=run_id,
                program_id=program_id,
                legacy_source_file_id=legacy_file,
                legacy_amount=Decimal("1.00"),
                calculated_amount=Decimal("10.00"),
                variance_amount=Decimal("-9.00"),
                presence="calculated_only",
            )
        conn.rollback()


def test_rejects_explained_true_with_root_cause_still_unattributed(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-ex") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_variance(
                conn,
                ingest_run_id=run_id,
                program_id=program_id,
                legacy_source_file_id=legacy_file,
                explained=True,
                root_cause="unattributed",
            )
        conn.rollback()


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 1, 1), date(2026, 1, 30)),
    ],
)
def test_rejects_a_variance_period_that_is_not_a_whole_month(
    require_live_db: None, start: date, end: date
) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-p") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_variance(
                conn,
                ingest_run_id=run_id,
                program_id=program_id,
                legacy_source_file_id=legacy_file,
                period_start=start,
                period_end=end,
            )
        conn.rollback()


def test_the_unique_key_rejects_a_second_row_for_the_same_run_program_period(
    require_live_db: None,
) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "var-dup") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-TEST-1")
        erp_file = _insert_source_file(conn, cid, kind="erp_invoice_lines")
        legacy_file = _insert_source_file(conn, cid, kind="legacy_rebate")
        run_id = _insert_ingest_run(conn, cid, erp_file)
        _insert_variance(
            conn, ingest_run_id=run_id, program_id=program_id, legacy_source_file_id=legacy_file
        )
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_variance(
                conn, ingest_run_id=run_id, program_id=program_id, legacy_source_file_id=legacy_file
            )
        conn.rollback()


# --- the imported status and TERMINAL_STATUSES (migration 0024) ----------------------------------


def test_ingest_runs_status_check_accepts_imported(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "status-ok") as cid:
        file_id = _insert_source_file(conn, cid)
        run_id = _insert_ingest_run(conn, cid, file_id)
        conn.execute("update ingest_runs set status = 'imported' where id = %s", (run_id,))
        conn.commit()


def test_run_status_badge_and_run_status_schema_agree_on_imported() -> None:
    from src.features.runs.schemas import TERMINAL_STATUSES

    assert "imported" in TERMINAL_STATUSES
