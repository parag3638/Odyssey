"""Rebuilding the demo from the committed fixtures, in process.

**In process, not a shell-out to `scripts/seed_demo.py`.** That script talks to the API over HTTP
and carries a hardcoded admin password; the reset route is already inside the application with a
session in hand, and reading a committed fixture needs neither.

**Reference data first, then the files.** `rebate_programs_brand_fk` points at
`customer_manufacturers`, so a program loaded before its manufacturer is refused by the database -
which is the point of that key, and why the order here is not arbitrary.

The uploads go through **the real upload service**, so the seeded runs are produced by exactly the
path a visitor's own upload takes: same caps, same sniffing, same parse and profile jobs. A seed
that wrote rows directly would produce a state the product cannot reproduce.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.uploads.service import accept_upload
from src.lib import logger
from src.lib.ids import uuid7
from src.lib.storage import LocalStorage

log = logger.get_logger(__name__)

FIXTURES: Final = Path(__file__).resolve().parents[3] / "fixtures"
REFERENCE: Final = FIXTURES / "reference"
SAMPLES: Final = FIXTURES / "sample"
#: The 48,150-row month, gzipped. **The demo must use this and not the 500-row sample**, because
#: `legacy_rebates.csv` summarises the whole month: seeding the sample against it compares 500
#: invoice lines with a legacy total covering 48,150, and the explained figure collapses from
#: 99.8% to 0.02% - measured, after the first version of this seeder used the sample.
FULL: Final = FIXTURES / "full"

#: The five runs a fresh demo starts with, in upload order: filename, declared kind, and **which
#: scale of that fixture**, because two of these are the same file at two sizes and that is the
#: whole point of the list.
#:
#: Runs 1 and 2 are the 500-row samples and stop at `profiled`. They exist so the e2e suite has a
#: profiled CSV *and* a profiled fixed-width run to work against - without both, 21 of its tests
#: fail in setup rather than in a test body - and so the profile and mapping screens have something
#: to show a visitor who wants to see the mapping step rather than the finished answer.
#:
#: Run 3 is the **full** 48,150-row CSV and is the demo: `finish_demo_seed` drives it to `blocked`
#: with rebates calculated, variances computed and attributed. It has to be a separate upload from
#: run 1 rather than the same one reused, because a run cannot be both `profiled` and `blocked`.
#:
#: Run 5 is the drifted month 2. See `DEFERRED_INGEST`.
SEED_FILES: Final = (
    ("month1_invoices.csv", "erp_invoice_lines", "sample"),
    ("month1_invoices.txt", "erp_invoice_lines", "sample"),
    ("month1_invoices.csv", "erp_invoice_lines", "full"),
    ("legacy_rebates.csv", "legacy_rebate", "sample"),
    ("month2_invoices.csv", "erp_invoice_lines", "full"),
)

#: Uploaded here but **not** ingested here, keyed by `(filename, scale)` like `SEED_FILES`.
#: `month2_invoices.csv` carries the drifted layout, and
#: drift is only detectable against an *approved* spec - which does not exist until
#: `finish_demo_seed` has month 1 profiled and can approve one from it. Deferring this file's parse
#: now would race that: it would profile against no baseline, record a `no_baseline` drift
#: decision and settle at `profiled`, leaving the demo without the one run that shows drift work.
#: `finish_demo_seed` defers it in the right order instead.
DEFERRED_INGEST: Final = frozenset({("month2_invoices.csv", "full")})

_CUSTOMER = text(
    """
    insert into customers (id, name, slug, legacy_period_cutoff_day)
    values (:id, :name, :slug, :cutoff)
    on conflict (slug) do update set name = excluded.name,
      legacy_period_cutoff_day = excluded.legacy_period_cutoff_day
    returning id
    """
)

_MANUFACTURER = text(
    """
    insert into customer_manufacturers (id, customer_id, name, brand_values,
      direct_vendor_values, redistributor_only)
    values (:id, :customer_id, :name, :brand_values, :direct_vendor_values, :redistributor_only)
    on conflict (customer_id, name) do update set brand_values = excluded.brand_values,
      direct_vendor_values = excluded.direct_vendor_values,
      redistributor_only = excluded.redistributor_only, updated_at = now()
    """
)

_PROGRAM = text(
    """
    insert into rebate_programs (id, customer_id, external_ref, name, kind, brand_manufacturer,
      rate, redistributor_rate, uom_basis, effective_from, effective_to)
    values (:id, :customer_id, :external_ref, :name, :kind, :brand, :rate, :redistributor_rate,
      :uom_basis, :effective_from, :effective_to)
    on conflict (customer_id, name) do update set external_ref = excluded.external_ref,
      rate = excluded.rate, redistributor_rate = excluded.redistributor_rate,
      updated_at = now()
    """
)


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _fixture_bytes(filename: str, scale: str) -> bytes | None:
    """The named scale of a fixture, or `None` when it is not committed at that scale.

    The caller **names** the scale rather than this guessing it. The previous version preferred the
    full file whenever one existed, which is why `legacy_rebates.csv` has the warning above it: the
    full/sample choice decides whether the explained figure is 99.8% or 0.02%, and a seeder that
    silently picks for you cannot be read for that bug. It also made run 1 and run 3 - the same
    filename at two scales, deliberately - impossible to express.
    """
    if scale == "full":
        packed = FULL / f"{filename}.gz"
        if packed.exists():
            with gzip.open(packed, "rb") as handle:
                return handle.read()
        return None
    path = SAMPLES / filename
    return path.read_bytes() if path.exists() else None


async def seed(session: AsyncSession, *, slug: str, name: str, ip_hash: str) -> dict[str, Any]:
    """Rebuild the demo customer, its reference data, and its files."""
    settings = config.load()
    programs_doc = json.loads((REFERENCE / "rebate_programs.json").read_text(encoding="utf-8"))
    manufacturers_doc = json.loads(
        (REFERENCE / "customer_manufacturers.json").read_text(encoding="utf-8")
    )

    row = (
        await session.execute(
            _CUSTOMER,
            {
                "id": uuid7(),
                "name": name,
                "slug": slug,
                "cutoff": programs_doc.get("legacyPeriodCutoffDay"),
            },
        )
    ).first()
    assert row is not None
    customer_id = UUID(str(row[0]))

    for entry in manufacturers_doc["manufacturers"]:
        await session.execute(
            _MANUFACTURER,
            {
                "id": uuid7(),
                "customer_id": customer_id,
                "name": entry["name"],
                "brand_values": entry["brandValues"],
                "direct_vendor_values": entry["directVendorValues"],
                "redistributor_only": entry["redistributorOnly"],
            },
        )
    # After the reference rows: the brand foreign key refuses a program whose manufacturer is not
    # there yet, which is exactly what that key is for.
    for program in programs_doc["programs"]:
        await session.execute(
            _PROGRAM,
            {
                "id": uuid7(),
                "customer_id": customer_id,
                "external_ref": program["externalRef"],
                "name": program["name"],
                "kind": program["kind"],
                "brand": program["brandManufacturer"],
                "rate": program["rate"],
                "redistributor_rate": program.get("redistributorRate"),
                "uom_basis": program["uomBasis"],
                "effective_from": program["effectiveFrom"],
                "effective_to": program["effectiveTo"],
            },
        )
    await session.commit()

    storage = LocalStorage(settings.upload_dir)
    runs: list[str] = []
    uploaded: dict[tuple[str, str], Any] = {}
    for filename, kind, scale in SEED_FILES:
        content = _fixture_bytes(filename, scale)
        if content is None:
            log.warning("demo_seed_missing_fixture", filename=filename, scale=scale)
            continue
        accepted = await accept_upload(
            session,
            storage,
            customer_id=customer_id,
            kind=kind,
            original_filename=filename,
            chunks=_chunks(content),
            max_bytes=settings.max_upload_bytes,
            ip_hash=ip_hash,
            # The seed is not a visitor; its own uploads must not be refused by the visitor limit.
            rate_limit_per_hour=len(SEED_FILES) + 1,
        )
        if (filename, scale) not in DEFERRED_INGEST:
            from src.jobs.tasks import defer_ingest

            await defer_ingest(accepted.ingest_run_id, kind)
        # Keyed by `(filename, scale)`: `month1_invoices.csv` appears twice, and the run the
        # pipeline is driven over is specifically the full one.
        uploaded[filename, scale] = accepted
        runs.append(str(accepted.ingest_run_id))
    await session.commit()

    # Upload alone leaves three runs at `profiled` and `imported`, which is an empty demo: no rows,
    # no findings, a null explained percentage. One run is driven all the way through, in the
    # worker, so a visitor who presses Reset lands on the state the README describes.
    erp = uploaded.get(("month1_invoices.csv", "full"))
    legacy = uploaded.get(("legacy_rebates.csv", "sample"))
    month2 = uploaded.get(("month2_invoices.csv", "full"))
    if erp is not None and legacy is not None:
        from src.jobs.tasks import defer_finish_demo_seed

        await defer_finish_demo_seed(
            erp.ingest_run_id,
            legacy.source_file_id,
            None if month2 is None else month2.ingest_run_id,
        )

    log.info("demo_seeded", slug=slug, runs=len(runs), customer_id=str(customer_id))
    return {"customer_id": str(customer_id), "runs": runs}
