"""The canonical CSV download.

Three properties, each of which is a requirement rather than a nicety:

* **Generated from the database, never from the upload directory.** There is no code path
  by which a user-supplied file is served back, so there is nothing to traverse to.
* **Streamed.** Rows arrive from a server-side cursor in chunks, so a 250,000-row export
  does not become 250,000 rows of resident memory, and a client that disconnects stops the
  query instead of paying for the rest of it.
* **Inert in a spreadsheet.** Every *text* cell goes through `csv_safe.safe_cell`, so a
  `customer_name` of `=HYPERLINK(...)` opens as text. Numeric, date and boolean cells are
  rendered here from typed values and cannot carry a formula - which is why they are
  deliberately *not* passed through `safe_cell`: it would prefix a negative amount with an
  apostrophe and break the decimal-string contract `FORMAT.md` publishes.

The session is opened here rather than taken from the request, because FastAPI closes a
dependency's session before a `StreamingResponse` body runs - the generator would be
iterating over a closed connection.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text

from src.features.canonical.model import CANONICAL_FIELD_NAMES
from src.features.mapping.validators import DECIMAL_SCALES
from src.lib import db, logger
from src.lib.csv_safe import safe_cell

log = logger.get_logger(__name__)

#: Rows per flush. The response is chunked at roughly this granularity, which is what keeps
#: time-to-first-byte low without one write syscall per row.
CHUNK_ROWS: Final = 500

#: Rows fetched per round trip from the server-side cursor.
FETCH_ROWS: Final = 1_000

#: Fields whose value is arbitrary text from the source file, and therefore the only ones a
#: formula can hide in.
TEXT_FIELDS: Final = frozenset(
    {
        "invoice_no",
        "customer_id",
        "customer_name",
        "dist_item_id",
        "mfr_item_code",
        "brand_manufacturer",
        "invoice_vendor",
        "uom",
    }
)

_STREAM = text(
    """
    select source_row_number, invoice_no, invoice_date, customer_id, customer_name,
           dist_item_id, mfr_item_code, brand_manufacturer, invoice_vendor,
           qty, uom, pack_size, catch_weight_lbs, unit_price, ext_amount,
           is_deviated, deviated_price
    from canonical_lines
    where ingest_run_id = :run_id
    order by source_row_number
    limit :max_rows
    """
)


def header_line() -> str:
    """The header row, exactly as `FORMAT.md` publishes it.

    A function rather than a constant so the test that asserts the documented header can
    call the same code the download uses, and the doc cannot drift from the file.
    """
    return ",".join(CANONICAL_FIELD_NAMES) + "\n"


def cell(name: str, value: object) -> str:
    """One canonical value as `FORMAT.md` writes it.

    Absent is the empty field - no `NULL`, no `\\N`. Decimals are quantised to the
    documented scale rather than printed at the column's wider scale: the table stores more
    precision than we promise to re-export (see migration 0009), and `numeric(18,4)` would
    otherwise render a 3-decimal quantity as `3.0000`.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        scale = DECIMAL_SCALES.get(name)
        quantised = value if scale is None else value.quantize(Decimal(1).scaleb(-scale))
        return f"{quantised:f}"
    text_value = str(value)
    return safe_cell(text_value) if name in TEXT_FIELDS else text_value


def _row_cells(row: dict[str, Any]) -> list[str]:
    return [cell(name, row.get(name)) for name in CANONICAL_FIELD_NAMES]


def filename(customer_name: str, run_id: UUID) -> str:
    """`canonical_<customer>_<runid>.csv`, with the customer name reduced to a slug.

    Slugged, not escaped: the name reaches a `Content-Disposition` header, and a value
    containing a quote, a newline or a non-ASCII byte is a header-injection problem rather
    than a formatting one. Anything outside `[A-Za-z0-9-]` becomes a dash.
    """
    slug = "".join(char if char.isascii() and char.isalnum() else "-" for char in customer_name)
    slug = "-".join(part for part in slug.split("-") if part).lower()[:48]
    return f"canonical_{slug or 'customer'}_{run_id}.csv"


async def iter_csv(run_id: UUID, *, max_rows: int) -> AsyncIterator[str]:
    """Yield the canonical CSV in chunks, header first.

    `max_rows` is the upload ceiling: a run cannot hold more canonical rows than its file
    had source rows, so the cap is a belt-and-braces bound on an unbounded response rather
    than a limit a real export meets.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    yield header_line()

    written = 0
    async with db.session() as session:
        result = await session.stream(_STREAM, {"run_id": run_id, "max_rows": max_rows})
        try:
            async for row in result.mappings().yield_per(FETCH_ROWS):
                writer.writerow(_row_cells(dict(row)))
                written += 1
                if written % CHUNK_ROWS == 0:
                    yield _drain(buffer)
            remainder = _drain(buffer)
            if remainder:
                yield remainder
        finally:
            # A client disconnecting raises inside the generator. Closing the result closes
            # the server-side cursor, and the `async with` returns the connection to the
            # pool - without this a cancelled download leaks one connection per attempt.
            await result.close()
    log.info("canonical_csv_streamed", ingest_run_id=str(run_id), rows=written)


def _drain(buffer: io.StringIO) -> str:
    chunk = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return chunk
