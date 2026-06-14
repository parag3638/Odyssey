import pytest
from app.brokers.base import BuyOrder, SellOrder
from app.risk import validate_order, RiskRejection


def test_paper_mode_required_for_now():
    with pytest.raises(RiskRejection, match="live trading disabled"):
        validate_order(BuyOrder("AAPL", 1), mode="live", cash=1000, price=100,
                       max_position_notional=10_000)


def test_rejects_insufficient_cash():
    with pytest.raises(RiskRejection, match="insufficient buying power"):
        validate_order(BuyOrder("AAPL", 100), mode="paper", cash=500, price=100,
                       max_position_notional=1_000_000)


def test_rejects_oversized_position():
    with pytest.raises(RiskRejection, match="exceeds max position"):
        validate_order(BuyOrder("AAPL", 100), mode="paper", cash=1_000_000, price=100,
                       max_position_notional=5_000)


def test_rejects_non_positive_qty():
    with pytest.raises(RiskRejection, match="quantity must be positive"):
        validate_order(BuyOrder("AAPL", 0), mode="paper", cash=1000, price=100,
                       max_position_notional=10_000)


def test_valid_buy_passes():
    validate_order(BuyOrder("AAPL", 5), mode="paper", cash=10_000, price=100,
                   max_position_notional=10_000)  # no exception


def test_sell_skips_cash_check():
    validate_order(SellOrder("AAPL", 5), mode="paper", cash=0, price=100,
                   max_position_notional=10_000)  # no exception
