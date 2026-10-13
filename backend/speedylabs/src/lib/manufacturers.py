"""The customer's manufacturer reference list, as a lookup any feature can ask.

Three callers need knowledge no file contains. Two readiness checks ask whether a brand is one
this distributor only ever buys through a redistributor, and whether a brand is known at all.
The rebate calculator asks which manufacturer a line's brand spelling *is*, because a program
is keyed to a manufacturer's name and the canonical row carries whatever that customer's
mapping produced. That is customer reference data, so it lives in `customer_manufacturers` and
is read per customer.

It sits in `lib/` rather than under `features/readiness/` because of that second caller
(BACKEND.md §9's rule of two, moved at the second copy, not in advance).

**Matching is by value, not by id, and a brand has several spellings.** The canonical row
carries whatever the customer's mapping produced: `tests/mappings.py` maps both the brand and
the vendor column through `map_values`, so one customer's rows say `BLC` and another's say
`Bayline Creamery`. Each entry therefore lists every spelling, and lookup is trimmed and
case-folded, because a mapping may also `upper` a column.

Pure: rows in, lookup out, no session. An empty lookup reports itself absent, so the two
checks that depend on it say `not_applicable` with a reason rather than flagging every line
on a customer whose reference list has not been loaded.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any


def normalise(value: str) -> str:
    """The form both sides of a comparison are reduced to."""
    return " ".join(value.split()).casefold()


@dataclass(frozen=True)
class ManufacturerEntry:
    name: str
    #: Every spelling that may appear in `canonical_lines.brand_manufacturer`.
    brand_values: tuple[str, ...]
    #: Every spelling of the vendor this manufacturer bills under when it invoices direct.
    direct_vendor_values: tuple[str, ...]
    #: This distributor only ever buys the brand through a redistributor, so a line billed
    #: under the brand's own vendor is provably misattributed from the file alone.
    redistributor_only: bool

    def bills_direct_as(self, vendor_value: str) -> bool:
        target = normalise(vendor_value)
        return any(normalise(value) == target for value in self.direct_vendor_values)


@dataclass(frozen=True)
class ManufacturerLookup:
    entries: tuple[ManufacturerEntry, ...]
    #: Derived from `entries`, built once: a scan per row would be 48,150 scans. `init=False`
    #: so it is never an input, and excluded from equality so two lookups built from the same
    #: entries compare equal. Frozen forbids *rebinding* the attribute, not filling the dict
    #: it already points at, which is why this needs no `object.__setattr__`.
    _index: dict[str, ManufacturerEntry] = field(
        init=False, repr=False, compare=False, default_factory=dict["str", "ManufacturerEntry"]
    )

    def __post_init__(self) -> None:
        for entry in self.entries:
            for spelling in entry.brand_values:
                self._index.setdefault(normalise(spelling), entry)

    @property
    def loaded(self) -> bool:
        """Whether this customer has a reference list at all."""
        return bool(self.entries)

    def find(self, brand_value: str) -> ManufacturerEntry | None:
        return self._index.get(normalise(brand_value))


def lookup_from_rows(rows: Iterable[dict[str, Any]]) -> ManufacturerLookup:
    """Build the lookup from `customer_manufacturers` rows."""
    return ManufacturerLookup(
        tuple(
            ManufacturerEntry(
                name=str(row["name"]),
                brand_values=_strings(row["brand_values"]),
                direct_vendor_values=_strings(row["direct_vendor_values"]),
                redistributor_only=bool(row["redistributor_only"]),
            )
            for row in rows
        )
    )


def _strings(values: Sequence[Any] | None) -> tuple[str, ...]:
    return tuple(str(value) for value in (values or ()))
