"""Seed one demo customer and upload the month-1 fixtures through the real API.

Why a script and not a test fixture: the route tests create a customer and delete it on
teardown, so a `make check` leaves the database with no customer and no runs - and every
browser check after it starts by hand-rolling an upload. This is the same path a user
takes (`POST /v1/uploads`), so what the browser then shows is not a hand-built row that
only looks like the real thing.

Idempotent on the customer (`on conflict (slug)`), additive on uploads: running it twice
gives one customer and two runs, which is itself useful for the runs list.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import time
from pathlib import Path

import httpx
import psycopg

from src.lib.ids import uuid7

API_BASE = "http://127.0.0.1:8000"
ADMIN_DB_URL = "postgresql://speedy_admin:speedy@127.0.0.1:5432/speedy_onboard"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sample"
#: Where `FULL=1` decompresses a fixture to. Gitignored - see `_full_fixture`.
SCRATCH = Path(__file__).resolve().parent.parent / ".seed-cache"
_REFERENCE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "reference"
REFERENCE = _REFERENCE_DIR / "customer_manufacturers.json"
REBATE_PROGRAMS = _REFERENCE_DIR / "rebate_programs.json"
DEMO_SLUG = "demo-distributor"
DEMO_NAME = "Harbor Point Foodservice"


def ensure_customer() -> str:
    """Return the demo customer's id, creating it only if the slug is new."""
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn:
        row = conn.execute(
            """
            insert into customers (id, name, slug) values (%s, %s, %s)
            on conflict (slug) do update set name = excluded.name
            returning id
            """,
            (str(uuid7()), DEMO_NAME, DEMO_SLUG),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _full_fixture(name: str) -> Path:
    """The gzipped full-scale fixture, decompressed next to the samples.

    `FULL=1 make demo` seeds the 48,150-row month, which is what the readiness and variance
    screens have to be looked at with: the 500-row sample carries only a handful of each
    seeded defect, and its trailer declares the *whole* file's total, so the footer check on it
    reports a ~$30.1 M delta that is correct and useless to look at.

    Written to a gitignored scratch directory rather than into `fixtures/`, because
    `speedy-generate --verify` compares that directory byte for byte against the seed.
    """
    stem = name.rsplit(".", 1)[0]
    suffix = name.rsplit(".", 1)[1]
    source = FIXTURES.parent / "full" / f"{stem}.{suffix}.gz"
    if not source.exists():
        raise SystemExit(f"no full fixture for {name} (looked for {source})")
    target = SCRATCH / name
    if not target.exists():
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with gzip.open(source, "rb") as handle, target.open("wb") as out:
            shutil.copyfileobj(handle, out)
    return target


def load_manufacturer_reference(customer_id: str) -> int:
    """Load the customer's manufacturer list from the generated fixture.

    Reference data, not demo *content*: without it the two vendor checks can only report
    `not_applicable`, because nothing in an export says which brands are bought through a
    redistributor. The fixture is emitted by `speedy-generate` from the same seed as the
    invoices - so the reference world and the invoice world agree - and read here as JSON.
    Production code never imports `src.generator`; this script is not production code, and it
    still does not, because the generator already wrote the file.

    Idempotent on `(customer_id, name)`, so re-seeding updates rather than doubling.
    """
    document = json.loads(REFERENCE.read_text(encoding="utf-8"))
    entries = document["manufacturers"]
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn:
        for entry in entries:
            conn.execute(
                """
                insert into customer_manufacturers (
                    id, customer_id, name, brand_values, direct_vendor_values,
                    redistributor_only
                ) values (%s, %s, %s, %s, %s, %s)
                on conflict (customer_id, name) do update set
                    brand_values = excluded.brand_values,
                    direct_vendor_values = excluded.direct_vendor_values,
                    redistributor_only = excluded.redistributor_only,
                    updated_at = now()
                """,
                (
                    str(uuid7()),
                    customer_id,
                    entry["name"],
                    entry["brandValues"],
                    entry["directVendorValues"],
                    entry["redistributorOnly"],
                ),
            )
    return len(entries)


def load_rebate_programs(customer_id: str) -> int:
    """Load the customer's rebate programs from the generated fixture.

    Commercial data, not demo *content*: a rate comes from a signed agreement and never from an
    uploaded file. `rebate_programs` has existed since migration 0001 with no writer, so without
    this the calculator has nothing to calculate and would report `422 no_rebate_programs`.

    **Must run after `load_manufacturer_reference`.** `rebate_programs_brand_fk` points
    `(customer_id, brand_manufacturer)` at `customer_manufacturers (customer_id, name)`, so a
    program loaded first fails on the foreign key naming the manufacturer. That ordering is
    enforced by the database rather than by the order of these two calls, which is the point of
    the key.

    Idempotent on `(customer_id, name)`. A renamed program keeping its external reference raises
    on `rebate_programs_external_ref_key` instead of inserting a second row - loud, not silent.
    """
    document = json.loads(REBATE_PROGRAMS.read_text(encoding="utf-8"))
    programs = document["programs"]
    with psycopg.connect(ADMIN_DB_URL, autocommit=True) as conn:
        # The legacy system's period cutoff, which P13 attributes `date_cutoff` dollars with.
        conn.execute(
            "update customers set legacy_period_cutoff_day = %s where id = %s",
            (document.get("legacyPeriodCutoffDay"), customer_id),
        )
        for program in programs:
            conn.execute(
                """
                insert into rebate_programs (
                    id, customer_id, external_ref, name, kind, brand_manufacturer, rate,
                    redistributor_rate, uom_basis, effective_from, effective_to
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (customer_id, name) do update set
                    external_ref = excluded.external_ref,
                    redistributor_rate = excluded.redistributor_rate,
                    kind = excluded.kind,
                    brand_manufacturer = excluded.brand_manufacturer,
                    rate = excluded.rate,
                    uom_basis = excluded.uom_basis,
                    effective_from = excluded.effective_from,
                    effective_to = excluded.effective_to,
                    updated_at = now()
                """,
                (
                    str(uuid7()),
                    customer_id,
                    program["externalRef"],
                    program["name"],
                    program["kind"],
                    program["brandManufacturer"],
                    program["rate"],
                    program.get("redistributorRate"),
                    program["uomBasis"],
                    program["effectiveFrom"],
                    program["effectiveTo"],
                ),
            )
    return len(programs)


def upload(customer_id: str, path: Path) -> str:
    with path.open("rb") as handle:
        response = httpx.post(
            f"{API_BASE}/v1/uploads",
            data={"customerId": customer_id, "kind": "erp_invoice_lines"},
            files={"file": (path.name, handle, "text/plain")},
            timeout=60,
        )
    # 202, not 201: the route accepts the bytes and defers the parse, so there is no
    # finished resource to point a Location at yet.
    if response.status_code != 202:
        raise SystemExit(f"upload of {path.name} failed: {response.status_code} {response.text}")
    return str(response.json()["data"]["ingestRunId"])


def wait_until_settled(run_id: str, budget_s: float = 60) -> dict[str, object]:
    """Poll `isSettled` - never a status list. The server owns that vocabulary."""
    deadline = time.monotonic() + budget_s
    run: dict[str, object] = {}
    while time.monotonic() < deadline:
        run = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}", timeout=10).json()["data"]
        if run["isSettled"]:
            return run
        time.sleep(0.4)
    raise SystemExit(f"run {run_id} did not settle in {budget_s}s: {run}")


def main() -> int:
    # CSV only by default. `profile_source_file` still returns early for fixed-width
    # ("fixed_width_awaits_p4"), which leaves that run resting on `parsed` - a status
    # Phase 3 demoted from terminal - so it never settles and this script would block on
    # it. Pass filenames explicitly to seed the fixed-width file once P4 profiles it.
    names = sys.argv[1:] or ["month1_invoices.csv"]
    full = os.environ.get("FULL") == "1"
    customer_id = ensure_customer()
    print(f"customer {DEMO_NAME} ({DEMO_SLUG}) = {customer_id}")
    loaded = load_manufacturer_reference(customer_id)
    print(f"  manufacturer reference  -> {loaded} entries")
    # After the reference rows, not before: rebate_programs_brand_fk requires them.
    programs = load_rebate_programs(customer_id)
    print(f"  rebate programs         -> {programs} programs")
    for name in names:
        path = _full_fixture(name) if full else FIXTURES / name
        run = wait_until_settled(upload(customer_id, path), budget_s=600 if full else 60)
        print(f"  {path.name:24} -> {run['status']!s:9} {run['id']!s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
