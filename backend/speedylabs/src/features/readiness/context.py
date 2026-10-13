"""Everything the checks need that is not a row.

Assembled by the caller - the job reads it from the database, a test writes it by hand -
so `run_checks` stays a pure function of (rows, context). No connection, no file, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final

from src.lib.manufacturers import ManufacturerLookup

#: How far `ext_amount` may differ from quantity times price before it is a finding. One
#: cent, absolute, and **not** configurable: a deployment-wide environment variable is the
#: wrong axis for what is really a per-customer commercial tolerance, and a number nobody
#: can see in the UI is worse than a published one. It is shown next to every finding.
#:
#: Deliberately *not* P5's rule. `validators._validate_extension_identity` accepts either
#: basis and adds a relative component, because it is asking "is this *mapping* right?" and
#: must not cry wolf on a catch-weight column. This asks "is this *data* right?", where the
#: basis is known from `uom` and a relative tolerance would hide the seeded $3.50 error on
#: any line over $3,500.
EXTENSION_TOLERANCE_USD: Final = Decimal("0.01")

#: Tolerance for the file-level footer comparison. The same cent: a trailer that disagrees
#: by less than a cent is a rounding artefact, not a reconciliation problem.
FOOTER_TOLERANCE_USD: Final = Decimal("0.01")


@dataclass(frozen=True)
class FooterFacts:
    """What the file declared about itself, and whether it declared anything.

    Three states, kept distinct because they are three different things to tell a reader:
    a CSV has no footer record at all; a fixed-width file may have one we could not read;
    and a readable one gives a number to compare.
    """

    has_footer_record: bool = False
    declared_rows: int | None = None
    declared_total: Decimal | None = None


@dataclass(frozen=True)
class ReadinessContext:
    footer: FooterFacts = field(default_factory=FooterFacts)
    manufacturers: ManufacturerLookup = field(default_factory=lambda: ManufacturerLookup(()))
    #: Days with no invoices that are not a gap. Weekends are excluded by rule; a holiday
    #: has to be named, because "no invoices on Thanksgiving" is not a data problem and a
    #: checklist that said so would be ignored wholesale.
    holidays: frozenset[date] = frozenset()
    extension_tolerance_usd: Decimal = EXTENSION_TOLERANCE_USD
    footer_tolerance_usd: Decimal = FOOTER_TOLERANCE_USD
    #: Rows that never became canonical lines. Not a check - `materialization_errors`
    #: already reports them - but the checklist states it, because every count below is a
    #: count over the rows that *did* make it.
    rows_rejected: int = 0
