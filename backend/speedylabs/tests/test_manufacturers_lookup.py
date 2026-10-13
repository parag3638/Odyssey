"""Direct coverage of `src.lib.manufacturers`, moved verbatim from `features/readiness/reference.py`
in P11.0 with no test importing the module either before or after the move
(`docs/decisions.md`, "P11.0 - the manufacturer lookup moves to lib/manufacturers.py").

The rebate calculator now depends on `normalise`'s whitespace-collapsed, case-folded equivalence to
match a program to a canonical row: a program is keyed to a manufacturer's *name*
(`"Bayline Creamery"`), while a canonical row may carry any spelling that customer's mapping
produced (`"BLC"`, `"  bayline   creamery "`). If `normalise` disagreed with itself about which
spellings are "the same manufacturer", the rebate engine would silently stop matching real lines -
exactly the zero-dollar failure mode `docs/decisions.md` describes for P11.1. This file is that
missing direct test, pinned against the module's own docstring example.
"""

from __future__ import annotations

from src.lib.manufacturers import ManufacturerEntry, ManufacturerLookup, lookup_from_rows, normalise


def test_normalise_collapses_internal_whitespace_and_case() -> None:
    assert normalise("  bayline   creamery ") == normalise("Bayline Creamery")
    assert normalise("BLC") == "blc"


def test_normalise_is_idempotent() -> None:
    once = normalise("  Bayline   Creamery ")
    assert normalise(once) == once


def test_find_returns_the_same_entry_for_every_spelling_of_one_brand() -> None:
    """The exact rule step 11.0 pins: `find("  bayline   creamery ")` and `find("BLC")` must
    return the same entry - this is what lets a program keyed to the manufacturer's *name* match
    a canonical row spelled as the ERP's brand code."""
    lookup = lookup_from_rows(
        [
            {
                "name": "Bayline Creamery",
                "brand_values": ["BLC", "Bayline Creamery"],
                "direct_vendor_values": ["300388"],
                "redistributor_only": True,
            }
        ]
    )
    by_code = lookup.find("BLC")
    by_messy_name = lookup.find("  bayline   creamery ")
    by_exact_name = lookup.find("Bayline Creamery")

    assert by_code is not None
    assert by_code is by_messy_name is by_exact_name
    assert by_code.name == "Bayline Creamery"


def test_find_is_case_and_whitespace_insensitive_on_every_listed_spelling() -> None:
    lookup = lookup_from_rows(
        [
            {
                "name": "Northvale Foods",
                "brand_values": ["NVF", "Northvale Foods"],
                "direct_vendor_values": [],
                "redistributor_only": False,
            }
        ]
    )
    assert lookup.find("nvf") is lookup.find("  NVF  ") is lookup.find("NORTHVALE   FOODS")


def test_find_returns_none_for_a_spelling_no_entry_lists() -> None:
    lookup = lookup_from_rows(
        [
            {
                "name": "Cascade Provision Co",
                "brand_values": ["CPC"],
                "direct_vendor_values": [],
                "redistributor_only": False,
            }
        ]
    )
    assert lookup.find("GHOST_BRAND") is None


def test_an_empty_lookup_reports_itself_unloaded() -> None:
    lookup = lookup_from_rows([])
    assert lookup.loaded is False
    assert lookup.find("anything") is None


def test_a_loaded_lookup_reports_itself_loaded() -> None:
    lookup = lookup_from_rows(
        [
            {
                "name": "X",
                "brand_values": ["X"],
                "direct_vendor_values": [],
                "redistributor_only": False,
            }
        ]
    )
    assert lookup.loaded is True


def test_lookup_from_rows_tolerates_null_array_columns() -> None:
    """`customer_manufacturers.brand_values` / `direct_vendor_values` are nullable arrays at the
    database level; `_strings` must not raise on `None`."""
    lookup = lookup_from_rows(
        [
            {
                "name": "Y",
                "brand_values": None,
                "direct_vendor_values": None,
                "redistributor_only": False,
            }
        ]
    )
    assert lookup.find("Y") is None  # no spelling listed, so nothing indexes to it
    assert lookup.loaded is True  # the entry still exists; it just lists no spellings


def test_bills_direct_as_matches_through_normalise_too() -> None:
    entry = ManufacturerEntry(
        name="Bayline Creamery",
        brand_values=("BLC",),
        direct_vendor_values=("300388",),
        redistributor_only=True,
    )
    assert entry.bills_direct_as("  300388 ") is True
    assert entry.bills_direct_as("999999") is False


def test_two_lookups_built_from_the_same_entries_compare_equal() -> None:
    """The `_index` field is excluded from equality (`compare=False`) precisely so this holds -
    otherwise two lookups built from identical reference rows would never compare equal."""
    rows = [
        {
            "name": "Z",
            "brand_values": ["Z", "ZED"],
            "direct_vendor_values": [],
            "redistributor_only": False,
        }
    ]
    assert lookup_from_rows(rows) == lookup_from_rows(rows)


def test_first_spelling_wins_when_two_entries_claim_the_same_normalised_value() -> None:
    """`ManufacturerLookup.__post_init__` uses `setdefault`, so entry order is load-bearing for a
    (hypothetical, reference-data-error) collision. Documented here rather than left implicit."""
    lookup = ManufacturerLookup(
        entries=(
            ManufacturerEntry(
                name="First",
                brand_values=("DUP",),
                direct_vendor_values=(),
                redistributor_only=False,
            ),
            ManufacturerEntry(
                name="Second",
                brand_values=("DUP",),
                direct_vendor_values=(),
                redistributor_only=False,
            ),
        )
    )
    found = lookup.find("DUP")
    assert found is not None
    assert found.name == "First"
