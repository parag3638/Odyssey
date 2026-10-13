"""`features/rebates/engine.py` + `reference_engine.py`, pure - no database, no HTTP.

Covers the plan's step 11.2 acceptance criteria plus three gaps the implementer flagged
explicitly for the tester to cover, because nothing else in the suite would catch them:

1. `qty / pack_size` for a *contributed* `EA` line under a case-basis program has zero oracle
   coverage - every `EA` line in the committed 48,150-line fixture happens to be missing its
   pack size (`docs/decisions.md`, "P11.1 - the seeded rebate programs..."). The test below
   named for the contributed `EA` case is that deliberate test.
2. The plan's own "no double rounding" example (`0.180000 x 12.3456`) gives `2.22` whether or not
   an operand is pre-rounded, so it proves nothing. The two cases that do bite - found and stated
   in `docs/decisions.md` ("P11.2 - the engine seam...") - are reproduced here as denial tests
   against a deliberately-wrong pre-rounding implementation, so a future "simplification" that
   reintroduces pre-rounding fails loudly.
3. `normalise`'s whitespace/case-fold rule reaching the engine (not just the lookup) is a named
   11.2 criterion.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID, uuid4

from src.features.rebates.codes import (
    CONTRIBUTED,
    MISSING_CATCH_WEIGHT,
    MISSING_PACK_SIZE,
    OUT_OF_BASIS_SCOPE,
)
from src.features.rebates.engine import LineRebate, RebateEngine, RebateLine, RebateProgramInput
from src.features.rebates.reference_engine import ReferenceRebateEngine

ENGINE = ReferenceRebateEngine()


def _line(
    *,
    uom: str,
    qty: str = "1",
    pack_size: int | None = None,
    catch_weight_lbs: str | None = None,
    brand: str = "Bayline Creamery",
    invoice_date: date = date(2026, 3, 15),
    line_id: UUID | None = None,
) -> RebateLine:
    return RebateLine(
        canonical_line_id=line_id or uuid4(),
        invoice_date=invoice_date,
        brand_manufacturer=brand,
        uom=uom,
        qty=Decimal(qty),
        pack_size=pack_size,
        catch_weight_lbs=None if catch_weight_lbs is None else Decimal(catch_weight_lbs),
    )


def _cs_program(
    *,
    rate: str = "0.750000",
    brand_values: tuple[str, ...] = ("Bayline Creamery", "BLC"),
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = None,
) -> RebateProgramInput:
    return RebateProgramInput(
        program_id=uuid4(),
        kind="per_case_volume",
        uom_basis="CS",
        rate=Decimal(rate),
        brand_values=brand_values,
        effective_from=effective_from,
        effective_to=effective_to,
    )


def _lb_program(
    *,
    rate: str = "0.100000",
    brand_values: tuple[str, ...] = ("Bayline Creamery", "BLC"),
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = None,
) -> RebateProgramInput:
    return RebateProgramInput(
        program_id=uuid4(),
        kind="per_lb_billback",
        uom_basis="LB",
        rate=Decimal(rate),
        brand_values=brand_values,
        effective_from=effective_from,
        effective_to=effective_to,
    )


# --- basic basis/status combinations, stated by hand (11.2) ----------------------------------


def test_cs_line_under_a_case_program_contributes_qty_as_basis() -> None:
    program = _cs_program(rate="0.750000")
    [result] = ENGINE.calculate([_line(uom="CS", qty="10")], [program])
    assert result.line_status == CONTRIBUTED
    assert result.skipped_reason is None
    assert result.basis_contribution == Decimal("10.0000")
    assert result.amount_contribution == Decimal("7.50")


def test_ea_line_with_a_pack_size_contributes_the_case_equivalent() -> None:
    """The deliberate gap: 24 eaches of a 12-pack is 2 cases, and this path has zero coverage in
    the committed oracle because every seeded EA line is missing its pack size."""
    program = _cs_program(rate="0.750000")
    [result] = ENGINE.calculate([_line(uom="EA", qty="24", pack_size=12)], [program])
    assert result.line_status == CONTRIBUTED
    assert result.basis_contribution == Decimal("2.0000")
    assert result.amount_contribution == Decimal("1.50")


def test_ea_line_with_no_pack_size_is_skipped_as_missing_pack_size() -> None:
    program = _cs_program()
    [result] = ENGINE.calculate([_line(uom="EA", qty="24", pack_size=None)], [program])
    assert result.line_status == "skipped"
    assert result.skipped_reason == MISSING_PACK_SIZE
    assert result.basis_contribution == Decimal("0.0000")
    assert result.amount_contribution == Decimal("0.00")


def test_ea_line_with_pack_size_zero_is_also_skipped() -> None:
    """`pack_size == 0` is the same uncomputable case as `None`, not a division by zero."""
    program = _cs_program()
    [result] = ENGINE.calculate([_line(uom="EA", qty="24", pack_size=0)], [program])
    assert result.line_status == "skipped"
    assert result.skipped_reason == MISSING_PACK_SIZE


def test_lb_line_with_a_catch_weight_contributes_it_as_basis() -> None:
    program = _lb_program(rate="0.100000")
    [result] = ENGINE.calculate([_line(uom="LB", catch_weight_lbs="18.4")], [program])
    assert result.line_status == CONTRIBUTED
    assert result.basis_contribution == Decimal("18.4000")
    assert result.amount_contribution == Decimal("1.84")


def test_lb_line_with_no_catch_weight_is_skipped_as_missing_catch_weight() -> None:
    program = _lb_program()
    [result] = ENGINE.calculate([_line(uom="LB", catch_weight_lbs=None)], [program])
    assert result.line_status == "skipped"
    assert result.skipped_reason == MISSING_CATCH_WEIGHT


def test_lb_line_under_a_cs_basis_program_is_out_of_basis_scope_not_skipped() -> None:
    program = _cs_program()
    [result] = ENGINE.calculate([_line(uom="LB", catch_weight_lbs="18.4")], [program])
    assert result.line_status == OUT_OF_BASIS_SCOPE
    assert result.skipped_reason is None
    assert result.basis_contribution == Decimal("0.0000")
    assert result.amount_contribution == Decimal("0.00")


def test_cs_line_under_an_lb_basis_program_is_also_out_of_basis_scope() -> None:
    program = _lb_program()
    [result] = ENGINE.calculate([_line(uom="CS", qty="10")], [program])
    assert result.line_status == OUT_OF_BASIS_SCOPE
    assert result.skipped_reason is None


def test_a_line_whose_brand_matches_no_program_gets_no_result_at_all() -> None:
    """Absence, not a zero row: a program never about this line should not appear in its
    provenance."""
    program = _cs_program(brand_values=("Northvale Foods", "NVF"))
    results = ENGINE.calculate([_line(uom="CS", qty="10", brand="Bayline Creamery")], [program])
    assert results == []


# --- the lookup's normalise rule, reaching the engine (11.2) ----------------------------------


def test_a_messy_brand_spelling_matches_through_normalise() -> None:
    program = _cs_program(brand_values=("Bayline Creamery",))
    [result] = ENGINE.calculate(
        [_line(uom="CS", qty="10", brand="  bayline   creamery ")], [program]
    )
    assert result.line_status == CONTRIBUTED


def test_a_brand_code_spelling_matches_through_the_programs_brand_values() -> None:
    program = _cs_program(brand_values=("Bayline Creamery", "BLC"))
    [result] = ENGINE.calculate([_line(uom="CS", qty="10", brand="BLC")], [program])
    assert result.line_status == CONTRIBUTED


# --- effective-date window (11.2) --------------------------------------------------------------


def test_a_line_before_effective_from_gets_no_result() -> None:
    program = _cs_program(effective_from=date(2026, 2, 1))
    results = ENGINE.calculate(
        [_line(uom="CS", qty="10", invoice_date=date(2026, 1, 15))], [program]
    )
    assert results == []


def test_a_line_after_a_non_null_effective_to_gets_no_result() -> None:
    program = _cs_program(effective_to=date(2026, 3, 31))
    results = ENGINE.calculate(
        [_line(uom="CS", qty="10", invoice_date=date(2026, 4, 1))], [program]
    )
    assert results == []


def test_a_null_effective_to_covers_every_later_line() -> None:
    program = _cs_program(effective_to=None)
    results = ENGINE.calculate(
        [_line(uom="CS", qty="10", invoice_date=date(2099, 1, 1))], [program]
    )
    assert len(results) == 1
    assert results[0].line_status == CONTRIBUTED


# --- rounding: ROUND_HALF_EVEN at the exact boundary (11.2) -------------------------------------


def test_round_half_even_rounds_an_exact_half_cent_down_to_even() -> None:
    # 0.005 at scale 2 is an exact tie; ROUND_HALF_EVEN takes the even neighbour, "0.00".
    program = _cs_program(rate="0.001000")
    [result] = ENGINE.calculate([_line(uom="CS", qty="5")], [program])
    assert result.amount_contribution == Decimal("0.005").quantize(Decimal("0.01"), ROUND_HALF_EVEN)
    assert result.amount_contribution == Decimal("0.00")


def test_round_half_even_rounds_the_next_exact_half_cent_up_to_even() -> None:
    # 0.015 at scale 2 is also an exact tie; the even neighbour above it is "0.02".
    program = _cs_program(rate="0.003000")
    [result] = ENGINE.calculate([_line(uom="CS", qty="5")], [program])
    assert result.amount_contribution == Decimal("0.02")


# --- no double rounding: the two cases that actually bite (P11.2 decision log) ------------------


def _wrong_prerounded_rate(line: RebateLine, program: RebateProgramInput) -> Decimal:
    """What the engine would compute if it pre-rounded the *rate* to 2dp before multiplying -
    the mistake `lib/money`'s docstring forbids. Reimplemented locally, not imported from
    production code, so this test cannot pass by accident if the real engine were ever broken
    the same way."""
    from src.lib.money import quantize_amount

    rounded_rate = program.rate.quantize(Decimal("0.01"))
    return quantize_amount(rounded_rate * line.catch_weight_lbs)  # type: ignore[operator]


def test_pre_rounding_the_rate_would_have_moved_a_fifty_cent_line() -> None:
    """Measured and logged: `0.185000 x 100 lb` is `18.50` correctly, but `18.00` if the rate is
    pre-rounded to 2dp first - a 50-cent miss on one ordinary line."""
    program = _lb_program(rate="0.185000")
    line = _line(uom="LB", catch_weight_lbs="100")
    [result] = ENGINE.calculate([line], [program])
    assert result.amount_contribution == Decimal("18.50")
    assert _wrong_prerounded_rate(line, program) == Decimal("18.00")
    assert result.amount_contribution != _wrong_prerounded_rate(line, program)


def _wrong_prerounded_basis(line: RebateLine, program: RebateProgramInput) -> Decimal:
    """What the engine would compute if it pre-rounded the *basis* (the qty/pack_size quotient)
    to 4dp before multiplying by the rate, rather than multiplying the unquantised quotient."""
    from src.lib.money import quantize_amount, quantize_qty

    basis = line.qty / Decimal(line.pack_size)  # type: ignore[arg-type]
    pre_rounded_basis = quantize_qty(basis)
    return quantize_amount(program.rate * pre_rounded_basis)


def test_pre_rounding_the_basis_would_have_moved_an_ordinary_each_line() -> None:
    """Measured and logged: one each of a 12-pack under a `0.900000` rate is `0.08` from the
    unquantised 1/12 basis, but `0.07` if the quotient is rounded to 4dp first - an ordinary
    invoice line, not a contrived boundary."""
    program = _cs_program(rate="0.900000")
    line = _line(uom="EA", qty="1", pack_size=12)
    [result] = ENGINE.calculate([line], [program])
    assert result.amount_contribution == Decimal("0.08")
    assert _wrong_prerounded_basis(line, program) == Decimal("0.07")
    assert result.amount_contribution != _wrong_prerounded_basis(line, program)


def test_basis_contribution_is_quantised_to_4dp_for_the_record_only() -> None:
    """`basis_contribution` must be the stored, quantised figure - but it must not be the number
    the money was computed from. Quantising first would be exactly the mistake the two tests
    above exist to catch."""
    program = _cs_program(rate="0.900000")
    [result] = ENGINE.calculate([_line(uom="EA", qty="1", pack_size=12)], [program])
    assert result.basis_contribution == Decimal("0.0833")  # 1/12 to 4dp


# --- floats are refused, not coerced (11.2) -----------------------------------------------------


def test_lib_money_raises_typeerror_on_a_float_qty_rather_than_coercing() -> None:
    import pytest

    from src.lib.money import quantize_qty

    with pytest.raises(TypeError):
        quantize_qty(10.5)  # type: ignore[arg-type]


# --- the seam is real: a fake engine satisfies the Protocol with no inheritance (11.2) ----------


class _SevenLineFakeEngine:
    """Inherits nothing. If this satisfies `RebateEngine` under pyright strict, a real HTTP
    client can too."""

    engine_name = "fake-for-test"
    engine_version = "0.0.1"

    def calculate(
        self,
        lines: Sequence[RebateLine],
        programs: Sequence[RebateProgramInput],
    ) -> Sequence[LineRebate]:
        return [
            LineRebate(
                canonical_line_id=line.canonical_line_id,
                program_id=programs[0].program_id,
                line_status=CONTRIBUTED,
                skipped_reason=None,
                basis_contribution=Decimal("1.0000"),
                amount_contribution=Decimal("1.00"),
            )
            for line in lines[:7]
        ]


def test_a_fake_engine_with_no_inheritance_satisfies_the_protocol_structurally() -> None:
    fake: RebateEngine = _SevenLineFakeEngine()
    program = _cs_program()
    results = fake.calculate([_line(uom="CS", qty="1") for _ in range(7)], [program])
    assert len(results) == 7
    assert fake.engine_name == "fake-for-test"


def test_engine_identity_is_read_only_not_a_plain_writable_attribute() -> None:
    """P11.2's pyright finding: a plain `engine_name: str` annotation in the Protocol declares a
    *writable* attribute, which `ReferenceRebateEngine` (a frozen dataclass) cannot satisfy -
    this is why the Protocol declares them as read-only `@property`. Asserted at runtime: a
    frozen dataclass really does refuse the assignment a writable attribute would allow."""
    import dataclasses

    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        ENGINE.engine_name = "mutated"  # type: ignore[misc]
