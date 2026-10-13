"""The distributor, its trading partners, and 200 SKUs — deterministic from one seed.

Everything here is fictional and labelled as such. The names are plausible rather than
whimsical because the demo has to read as real foodservice data to someone who works in
it, but no real distributor, manufacturer or redistributor is referenced.

Determinism is the point. Every draw comes from a `random.Random(seed)` passed in
explicitly, never the global `random` module, and nothing iterates a `set` — Python
randomises string hashing per process, so set order would differ between runs and the
"byte-identical across processes" property would quietly fail.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from random import Random
from typing import Any, Final, cast

from src.generator.seed import SEED

#: 200 SKUs, split so catch-weight items are a meaningful minority rather than an
#: afterthought - they are where the interesting readiness and variance issues live.
#: Operator accounts the distributor invoices.
CUSTOMER_COUNT: Final = 25

CASE_SKU_COUNT: Final = 160
CATCH_WEIGHT_SKU_COUNT: Final = 40
TOTAL_SKU_COUNT: Final = CASE_SKU_COUNT + CATCH_WEIGHT_SKU_COUNT


@dataclass(frozen=True)
class Distributor:
    number: str
    name: str
    erp: str


@dataclass(frozen=True)
class Manufacturer:
    code: str
    name: str
    #: The vendor number this manufacturer bills under when it invoices direct.
    vendor_number: str
    #: True when the distributor only ever buys this brand through a redistributor.
    #: A line for such a brand carrying a direct vendor number is provably misattributed,
    #: which is what makes the seeded vendor defect detectable from the file alone.
    redistributor_only: bool = False


@dataclass(frozen=True)
class Redistributor:
    number: str
    name: str


@dataclass(frozen=True)
class RebateProgram:
    """A rebate the distributor earns. Only the two kinds the spec allows.

    `per_case_volume` pays a rate for every case bought; `per_lb_billback` pays a rate
    for every pound of a catch-weight item. Both are keyed to a brand, which is why a
    line attributed to the wrong vendor can be priced under the wrong rate.
    """

    program_id: str
    name: str
    kind: str
    brand_code: str
    rate: Decimal
    uom_basis: str


@dataclass(frozen=True)
class Customer:
    """An operator account. `number` is what the ERP's BILTNO column carries."""

    number: str
    name: str


@dataclass(frozen=True)
class Sku:
    dist_item_id: str
    mfr_item_code: str
    brand_code: str
    description: str
    uom: str
    pack_size: int
    list_price: Decimal
    catch_weight: bool
    weight_min_lbs: Decimal | None
    weight_max_lbs: Decimal | None


@dataclass(frozen=True)
class Catalog:
    distributor: Distributor
    programs: tuple[RebateProgram, ...]
    manufacturers: tuple[Manufacturer, ...]
    redistributors: tuple[Redistributor, ...]
    customers: tuple[Customer, ...]
    skus: tuple[Sku, ...]

    def to_json(self) -> str:
        """Canonical serialisation: sorted keys, Decimals as strings.

        Used by the determinism test, so it must be stable rather than merely readable.
        """
        return json.dumps(_as_plain(self), sort_keys=True, indent=2, ensure_ascii=True)


# --- fictional trading partners -------------------------------------------------------

DISTRIBUTOR: Final = Distributor(
    number="100",
    name="Harbor Point Foodservice",
    erp="In-house AS/400 (IBM i) - custom record layout, no native parser",
)

MANUFACTURERS: Final = (
    Manufacturer(code="NVF", name="Northvale Foods", vendor_number="300145"),
    Manufacturer(code="CPC", name="Cascade Provision Co", vendor_number="300292"),
    # The creamery sells only through redistribution - a real and common arrangement,
    # and the one that makes misattributed rebate dollars findable.
    Manufacturer(
        code="BLC", name="Bayline Creamery", vendor_number="300388", redistributor_only=True
    ),
)


#: Lookup helpers. Tuples, not sets - iteration order must be stable across processes.
def manufacturer_by_code(code: str) -> Manufacturer | None:
    for m in MANUFACTURERS:
        if m.code == code:
            return m
    return None


def redistributor_numbers() -> tuple[str, ...]:
    return tuple(r.number for r in REDISTRIBUTORS)


REDISTRIBUTORS: Final = (
    Redistributor(number="400771", name="Midland Redistribution"),
    Redistributor(number="400882", name="Keystone Supply Partners"),
)

# --- SKU vocabulary -------------------------------------------------------------------

PROGRAMS: Final = (
    RebateProgram(
        program_id="PG-NVF-CS",
        name="Northvale case volume",
        kind="per_case_volume",
        brand_code="NVF",
        rate=Decimal("1.250000"),
        uom_basis="CS",
    ),
    RebateProgram(
        program_id="PG-CPC-CS",
        name="Cascade case volume",
        kind="per_case_volume",
        brand_code="CPC",
        rate=Decimal("0.900000"),
        uom_basis="CS",
    ),
    RebateProgram(
        program_id="PG-BLC-LB",
        name="Bayline pound billback",
        kind="per_lb_billback",
        brand_code="BLC",
        rate=Decimal("0.180000"),
        uom_basis="LB",
    ),
)

#: The rate the legacy system used when it looked a program up by vendor number and
#: landed on a redistributor agreement instead of the brand's own. Lower than every
#: brand rate, so a misattributed line is always underpaid - a one-directional error is
#: easier to reason about when scoring attribution.
REDISTRIBUTOR_RATE: Final = Decimal("0.650000")


def program_for_brand(brand_code: str) -> RebateProgram | None:
    for program in PROGRAMS:
        if program.brand_code == brand_code:
            return program
    return None


_CUSTOMER_FIRST: Final = (
    "Lakeside",
    "Cedar Grove",
    "Harborview",
    "Pinecrest",
    "Fairmount",
    "Stonebridge",
    "Riverbend",
    "Oakhollow",
    "Brightwater",
    "Kingsfield",
    "Summit Park",
    "Elmwood",
    "Gracehill",
    "Northgate",
    "Willowmere",
    "Ashford",
    "Brookline",
    "Clearfield",
    "Dunmore",
    "Eastvale",
    "Fallbrook",
    "Glenarbor",
    "Havenwood",
    "Ironridge",
    "Juniper Flats",
)
_CUSTOMER_KIND: Final = (
    "Dining Group",
    "Hospitality",
    "School District",
    "Senior Living",
    "Cafe Group",
    "Hotel Group",
    "Medical Center",
    "Catering Co",
)

_CASE_NOUNS: Final = (
    "Green Beans",
    "Diced Tomatoes",
    "Long Grain Rice",
    "Elbow Macaroni",
    "Canola Oil",
    "Cane Sugar",
    "Table Salt",
    "Black Pepper",
    "Yellow Mustard",
    "Dill Pickle Chips",
    "Tomato Ketchup",
    "Ranch Dressing",
    "Hot Sauce",
    "Chicken Base",
    "Beef Base",
    "All Purpose Flour",
    "Baking Soda",
    "Vanilla Extract",
    "Cocoa Powder",
    "Rolled Oats",
)
_CASE_QUALIFIERS: Final = ("Fancy", "Choice", "Standard", "Premium", "Value")
_CASE_PACKS: Final = ("6/10 oz", "12/16 oz", "24/8 oz", "4/1 gal", "1/25 lb", "2/5 lb")

_MEAT_NOUNS: Final = (
    "Beef Brisket",
    "Beef Short Rib",
    "Pork Butt",
    "Pork Loin",
    "Chicken Thigh",
    "Chicken Breast",
    "Ground Beef 80/20",
    "Beef Chuck Roll",
    "Pork Belly",
    "Turkey Breast",
)
_CHEESE_NOUNS: Final = (
    "Cheddar Block",
    "Mozzarella Loaf",
    "Provolone Loaf",
    "Swiss Block",
    "Monterey Jack Block",
    "Parmesan Wheel",
    "Pepper Jack Block",
    "Colby Block",
    "Gouda Wheel",
    "Asiago Wedge",
)
_CW_QUALIFIERS: Final = ("Fresh", "Frozen", "Aged", "Smoked")


def _as_plain(value: Any) -> Any:
    """Recursively convert to JSON-safe primitives, Decimals as strings."""
    if isinstance(value, Decimal):
        return f"{value:f}"
    if isinstance(value, dict):
        # `value` is Any, so isinstance narrows only to dict[Unknown, Unknown]; the cast
        # tells the checker what the dataclass walk actually produces.
        return {k: _as_plain(v) for k, v in cast("dict[str, Any]", value).items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain(v) for v in cast("Sequence[Any]", value)]
    if hasattr(value, "__dataclass_fields__"):
        return _as_plain(asdict(value))
    return value


def _cents(rng: Random, low: int, high: int) -> Decimal:
    """A price in exact cents. Never float arithmetic - see lib/money."""
    return Decimal(rng.randrange(low, high)).scaleb(-2)


def build_catalog(seed: int = SEED) -> Catalog:
    """Build the catalog. Same seed in, same catalog out, in any process."""
    rng = Random(seed)

    customers = tuple(
        Customer(
            # Six-digit account numbers, as the ERP's 8-character BILTNO field carries.
            number=f"{200000 + index * 13:06d}",
            name=f"{_CUSTOMER_FIRST[index]} {_CUSTOMER_KIND[rng.randrange(len(_CUSTOMER_KIND))]}",
        )
        for index in range(CUSTOMER_COUNT)
    )

    skus: list[Sku] = []

    for index in range(CASE_SKU_COUNT):
        mfr = MANUFACTURERS[index % len(MANUFACTURERS)]
        noun = _CASE_NOUNS[rng.randrange(len(_CASE_NOUNS))]
        qualifier = _CASE_QUALIFIERS[rng.randrange(len(_CASE_QUALIFIERS))]
        pack_label = _CASE_PACKS[rng.randrange(len(_CASE_PACKS))]
        # Eaches per case. Always >= 1; a case of one is a real thing (a 25 lb sack).
        pack_size = rng.choice([1, 2, 4, 6, 12, 24])
        skus.append(
            Sku(
                dist_item_id=f"{100000 + index * 7:06d}",
                mfr_item_code=f"{mfr.code}-{rng.randrange(1000, 9999)}",
                brand_code=mfr.code,
                description=f"{qualifier} {noun} {pack_label}",
                uom="CS",
                pack_size=pack_size,
                list_price=_cents(rng, 1200, 9800),
                catch_weight=False,
                weight_min_lbs=None,
                weight_max_lbs=None,
            )
        )

    for index in range(CATCH_WEIGHT_SKU_COUNT):
        # Meat from the provision house, cheese from the creamery - the brand split a
        # real distributor would have.
        is_meat = index % 2 == 0
        mfr = MANUFACTURERS[1] if is_meat else MANUFACTURERS[2]
        nouns = _MEAT_NOUNS if is_meat else _CHEESE_NOUNS
        noun = nouns[rng.randrange(len(nouns))]
        qualifier = _CW_QUALIFIERS[rng.randrange(len(_CW_QUALIFIERS))]
        low = _cents(rng, 600, 1400)
        spread = _cents(rng, 200, 900)
        skus.append(
            Sku(
                dist_item_id=f"{500000 + index * 11:06d}",
                mfr_item_code=f"{mfr.code}-{rng.randrange(1000, 9999)}",
                brand_code=mfr.code,
                description=f"{qualifier} {noun}",
                # Catch-weight items are priced and invoiced by the pound, which is the
                # whole reason a missing weight makes a line uncomputable.
                uom="LB",
                pack_size=1,
                list_price=_cents(rng, 180, 1250),
                catch_weight=True,
                weight_min_lbs=low,
                weight_max_lbs=low + spread,
            )
        )

    return Catalog(
        distributor=DISTRIBUTOR,
        programs=PROGRAMS,
        manufacturers=MANUFACTURERS,
        redistributors=REDISTRIBUTORS,
        customers=customers,
        skus=tuple(skus),
    )
