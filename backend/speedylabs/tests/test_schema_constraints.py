"""`customers` / `rebate_programs` (migration `0001`) against the real schema from
`docker compose up -d db` + `alembic upgrade head`.

BACKEND.md §5: "Constraints in the DB: not null, unique, foreign keys, check. Code
validation is extra, not a replacement." These tests prove the four `CheckConstraint`s
on `rebate_programs`, the FK cascade, and `customers.slug` uniqueness actually reject
bad data at the database - existing in the migration file is not the same claim as
Postgres enforcing them.

Inserts go through the `speedy` app role (DML only, same path the application takes),
not `speedy_migrator` - this also doubles as confirmation that the app role really can
use tables the migrator creates (the `alter default privileges` grant in
`backend/sql/roles.sql`). Every row created is deleted in the same test, and slugs are
randomised per test so a prior failed run's leftovers can never cause a collision.

**Updated for migration `0023`.** This file predates `rebate_programs_brand_fk`
(`(customer_id, brand_manufacturer) -> customer_manufacturers (customer_id, name)`),
added in P11.1 specifically to make a program keyed to a manufacturer the customer has
no reference row for unrepresentable (`docs/decisions.md`, "P11.1 - programs match
through the reference list, and the database enforces it"). `VALID_PROGRAM`'s
`brand_manufacturer` is `"Acme"`, so `_customer` now also seeds a matching
`customer_manufacturers` row before a program can be inserted against it - a real
reference row, not a relaxation of the constraint under test. The FK itself is correct
and deliberate: fixing the fixture, not arguing the FK is wrong, is the right call here.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from src.lib.ids import uuid7

APP_URL = "postgresql://speedy:speedy@127.0.0.1:5432/speedy_onboard"

VALID_PROGRAM: dict[str, object] = {
    "kind": "per_case_volume",
    "brand_manufacturer": "Acme",
    "rate": Decimal("1.50"),
    "uom_basis": "CS",
    "effective_from": date(2026, 1, 1),
    "effective_to": date(2026, 12, 31),
}


def _slug(label: str) -> str:
    return f"acme-{label}-{uuid.uuid4().hex[:8]}"


def _insert_reference(
    conn: psycopg.Connection[object], customer_id: uuid.UUID, name: str = "Acme"
) -> None:
    """A `customer_manufacturers` row for `name`, the reference row migration `0023`'s
    `rebate_programs_brand_fk` requires before a program can name that manufacturer.
    `VALID_PROGRAM.brand_manufacturer` is `"Acme"`, so every test inserting a program
    against `_customer`'s customer needs this row to exist first - seed order enforced
    by the database, same as `seed_demo.py`'s real two-call order."""
    conn.execute(
        """
        insert into customer_manufacturers
            (id, customer_id, name, brand_values, direct_vendor_values, redistributor_only)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (uuid7(), customer_id, name, [name], [], False),
    )


@contextmanager
def _customer(conn: psycopg.Connection[object], label: str) -> Generator[uuid.UUID]:
    """A customer row that always deletes itself, even if the test body raises.

    Also seeds the `customer_manufacturers` reference row `_insert_program`'s default
    `brand_manufacturer` ("Acme") requires under migration `0023`'s FK - without it every
    program insert in this file would fail on `rebate_programs_brand_fk`, which is a
    fixture gap, not a defect in the constraint under test.
    """
    customer_id = uuid7()
    conn.execute(
        "insert into customers (id, name, slug) values (%s, %s, %s)",
        (customer_id, "Acme Foods", _slug(label)),
    )
    _insert_reference(conn, customer_id)
    conn.commit()
    try:
        yield customer_id
    finally:
        conn.rollback()
        conn.execute("delete from customers where id = %s", (customer_id,))
        conn.commit()


def _insert_program(
    conn: psycopg.Connection[object], customer_id: uuid.UUID, **overrides: object
) -> uuid.UUID:
    fields = {**VALID_PROGRAM, **overrides}
    name = fields.pop("name", "Q1 volume rebate")
    external_ref = fields.pop("external_ref", None)
    program_id = uuid7()
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
            fields["kind"],
            fields["brand_manufacturer"],
            fields["rate"],
            fields["uom_basis"],
            fields["effective_from"],
            fields["effective_to"],
            external_ref,
        ),
    )
    return program_id


def test_a_valid_program_insert_succeeds(require_live_db: None) -> None:
    """Positive control: the constraints below must not be rejecting good data too."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "valid") as cid:
        _insert_program(conn, cid)
        conn.commit()


@pytest.mark.parametrize("bad_kind", ["nonsense", "", "PER_CASE_VOLUME"])
def test_rejects_a_kind_outside_the_allowed_set(require_live_db: None, bad_kind: str) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "kind") as cid:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_program(conn, cid, kind=bad_kind)
        conn.rollback()


@pytest.mark.parametrize("bad_rate", [Decimal("0"), Decimal("-1")])
def test_rejects_a_rate_that_is_not_strictly_positive(
    require_live_db: None, bad_rate: Decimal
) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "rate") as cid:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_program(conn, cid, rate=bad_rate)
        conn.rollback()


def test_rejects_a_uom_basis_outside_the_allowed_set(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "uom") as cid:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_program(conn, cid, uom_basis="EA")
        conn.rollback()


def test_rejects_effective_to_before_effective_from(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "range") as cid:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_program(
                conn, cid, effective_from=date(2026, 6, 1), effective_to=date(2026, 1, 1)
            )
        conn.rollback()


def test_a_null_effective_to_is_allowed(require_live_db: None) -> None:
    """The range check (`effective_to is null or effective_to >= effective_from`) must
    not reject an open-ended program - that is a legitimate case, not an edge."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "openended") as cid:
        _insert_program(conn, cid, effective_to=None)
        conn.commit()


def test_customer_slug_uniqueness_is_enforced(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "dup") as cid:
        slug_row = conn.execute("select slug from customers where id = %s", (cid,)).fetchone()
        assert slug_row is not None
        taken_slug = slug_row[0]

        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "insert into customers (id, name, slug) values (%s, %s, %s)",
                (uuid7(), "Acme Foods Inc", taken_slug),
            )
        conn.rollback()


def test_deleting_a_customer_cascades_to_its_programs(require_live_db: None) -> None:
    del require_live_db
    customer_id = uuid7()
    with psycopg.connect(APP_URL, connect_timeout=5) as conn:
        conn.execute(
            "insert into customers (id, name, slug) values (%s, %s, %s)",
            (customer_id, "Acme Foods", _slug("cascade")),
        )
        _insert_reference(conn, customer_id)
        program_id = _insert_program(conn, customer_id)
        conn.commit()

        # Deletes the customer (and, via ON DELETE CASCADE, the program) in one go -
        # no separate cleanup of either row needed afterwards.
        conn.execute("delete from customers where id = %s", (customer_id,))
        conn.commit()

        row = conn.execute("select 1 from rebate_programs where id = %s", (program_id,)).fetchone()
        assert row is None, "the program survived its customer's deletion"


# --- migration 0023: rebate_programs_brand_fk, basis_agrees_check, external_ref_key -------------


def test_rejects_a_program_whose_brand_manufacturer_has_no_reference_row(
    require_live_db: None,
) -> None:
    """The FK argued in `docs/decisions.md` ("P11.1 - programs match through the reference
    list"): a program keyed to a manufacturer this customer has no `customer_manufacturers`
    row for is unrepresentable, because a string-match against the wrong spelling would
    otherwise match zero lines and report $0.00 in silence."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "nofk") as cid:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _insert_program(conn, cid, brand_manufacturer="Ghost Manufacturer")
        conn.rollback()


def test_rejects_a_brand_manufacturer_spelled_as_a_code_not_a_name(
    require_live_db: None,
) -> None:
    """`rebate_programs.brand_manufacturer` stores the manufacturer's *name*
    (`customer_manufacturers.name`), never a code - a program spelling it `"BLC"` has no
    matching reference row by that same FK, even though `"BLC"` might appear inside some
    other manufacturer's `brand_values` array. The code spelling is not a name."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "codespell") as cid:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _insert_program(conn, cid, brand_manufacturer="BLC")
        conn.rollback()


def test_rejects_a_kind_and_uom_basis_that_disagree(require_live_db: None) -> None:
    """`rebate_programs_basis_agrees_check`: `kind` and `uom_basis` are redundant and
    migration 0023 makes disagreement impossible, rather than letting `per_case_volume`
    and `LB` both land in the same row."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "basisagree") as cid:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_program(conn, cid, kind="per_case_volume", uom_basis="LB")
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_program(conn, cid, kind="per_lb_billback", uom_basis="CS")
        conn.rollback()


def test_a_program_with_an_external_ref_round_trips(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "extref") as cid:
        program_id = _insert_program(conn, cid, external_ref="PG-ACME-CS")
        conn.commit()
        row = conn.execute(
            "select external_ref from rebate_programs where id = %s", (program_id,)
        ).fetchone()
        assert row is not None
        assert row[0] == "PG-ACME-CS"


def test_rejects_a_duplicate_external_ref_for_the_same_customer(require_live_db: None) -> None:
    """`rebate_programs_external_ref_key`: P12's importer matches
    `legacy_rebate_lines.program_external_ref` against exactly one program, so two programs
    sharing a reference for one customer would make that match ambiguous."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "dupref") as cid:
        _insert_program(conn, cid, name="First program", external_ref="PG-DUP")
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_program(conn, cid, name="Second program", external_ref="PG-DUP")
        conn.rollback()


def test_a_null_external_ref_is_allowed(require_live_db: None) -> None:
    """Nullable: a real customer's programs may carry no external reference at all."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "noref") as cid:
        _insert_program(conn, cid, external_ref=None)
        conn.commit()


def test_a_null_external_ref_does_not_collide_with_another_null(require_live_db: None) -> None:
    """A unique constraint never treats two `null`s as equal in Postgres - confirmed rather
    than assumed, because a customer with two programs that simply have no external
    reference yet must not be refused."""
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5) as conn, _customer(conn, "twonulls") as cid:
        _insert_program(conn, cid, name="Program A", external_ref=None)
        _insert_program(conn, cid, name="Program B", external_ref=None)
        conn.commit()


def test_a_uuid7_round_trips_through_the_uuid_column(require_live_db: None) -> None:
    """`customers.id` has no server default (see migration `0001`'s comment: ids come
    from `src.lib.ids.uuid7`, not `gen_random_uuid()`). Confirms the value actually
    stored and read back is bit-for-bit the same UUID, still recognisable as v7."""
    del require_live_db
    original_id = uuid7()
    with psycopg.connect(APP_URL, connect_timeout=5) as conn:
        conn.execute(
            "insert into customers (id, name, slug) values (%s, %s, %s)",
            (original_id, "Acme Foods", _slug("uuid7")),
        )
        conn.commit()
        try:
            row = conn.execute("select id from customers where id = %s", (original_id,)).fetchone()
            assert row is not None
            round_tripped = row[0]
            assert round_tripped == original_id
            assert isinstance(round_tripped, uuid.UUID)
            assert round_tripped.version == 7
        finally:
            conn.execute("delete from customers where id = %s", (original_id,))
            conn.commit()
