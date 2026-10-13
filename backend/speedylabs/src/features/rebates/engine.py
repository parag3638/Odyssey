"""The seam a real rebate engine replaces. Types only - no arithmetic, no database, no HTTP.

Speedy has a rebate engine. This project does not, and must never look as though it does, so the
calculation sits behind an interface and `reference_engine.py` is one implementation of it, labelled
a stand-in everywhere its numbers appear.

**A `Protocol`, not a base class.** The replacement is most likely an HTTP client to Speedy's own
API, written in another module, importing nothing from here. A structural protocol is the only seam
that permits that: an ABC would require the real engine to inherit from our code, which is exactly
the coupling this seam exists to avoid. `features/readiness/checks` already proves the pattern holds
under pyright strict with no inheritance anywhere.

**Lines in, lines out - not aggregates.** The engine returns one result per line per program it
claims, and aggregation into program-periods is ours. Three reasons, all structural rather than
stylistic:

* `rebate_calcs.amount` is then the sum of its own `rebate_calc_lines.amount_contribution` by
  construction, instead of being a property we hope a third party's totals satisfy. A headline that
  disagrees with its own drill-down is not representable.
* 48,150 lines arrive in bounded batches rather than as one list.
* A remote engine has to be called in batches anyway, so the seam matches the thing it exists for.

**Plain frozen dataclasses, never ORM rows or dicts.** An engine on the far side of HTTP cannot
receive a SQLAlchemy row, and BACKEND.md §9 forbids untyped dicts crossing layers.

**The two identity attributes are read-only properties, not plain annotations.** A plain
`engine_name: str` in a Protocol declares a *writable* attribute, which a frozen dataclass does not
satisfy - pyright rejected `ReferenceRebateEngine` against this very seam until these became
properties. Read-only is also the honest requirement: the job reads the pair to store it and has no
business assigning to it. A frozen dataclass field, a plain attribute and a property all satisfy it.

**Every spelling travels on the program.** The engine matches lines to programs from the data it was
handed and holds no lookup object, so it stays pure and a remote engine gets everything it needs in
one request body.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.features.rebates.codes import LineStatus


@dataclass(frozen=True)
class RebateLine:
    """One canonical line, as the engine sees it."""

    canonical_line_id: UUID
    invoice_date: date
    #: The customer's spelling, exactly as `canonical_lines` stores it - the engine resolves it
    #: against each program's `brand_values` rather than assuming a canonical form.
    brand_manufacturer: str
    uom: str
    qty: Decimal
    pack_size: int | None
    catch_weight_lbs: Decimal | None


@dataclass(frozen=True)
class RebateProgramInput:
    """One program, with everything needed to match and price a line."""

    program_id: UUID
    kind: str
    #: 'CS' or 'LB'. The calculator keys off this rather than `kind` because it names the basis
    #: directly; migration 0023 constrains the two to agree.
    uom_basis: str
    #: Unquantised, 6dp as stored. Quantising a rate would change a commercial term.
    rate: Decimal
    #: Every spelling of this program's manufacturer, from `customer_manufacturers.brand_values`.
    brand_values: tuple[str, ...]
    effective_from: date
    effective_to: date | None


@dataclass(frozen=True)
class LineRebate:
    """What one program made of one line."""

    canonical_line_id: UUID
    program_id: UUID
    line_status: LineStatus
    #: Set for exactly the skipped lines, and names the field to fix.
    skipped_reason: str | None
    #: 4dp. Zero unless contributed.
    basis_contribution: Decimal
    #: 2dp, quantised once from the unquantised rate and basis. Zero unless contributed.
    amount_contribution: Decimal


class RebateEngine(Protocol):
    """What the calculation job depends on. A one-way door: see the plan's final section."""

    @property
    def engine_name(self) -> str:
        """Stored on every result row, so a number keeps saying which engine produced it."""
        ...

    @property
    def engine_version(self) -> str:
        """Bumped by any change to the rules. Stored, never re-derived at display time."""
        ...

    def calculate(
        self,
        lines: Sequence[RebateLine],
        programs: Sequence[RebateProgramInput],
    ) -> Sequence[LineRebate]:
        """Price a batch of lines against every program.

        Returns a result per (line, program) the program claims - and **no** result for a line no
        program is about, because a program that was never relevant to a line should not appear in
        that line's provenance.
        """
        ...
