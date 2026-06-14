from app.brokers.base import BuyOrder, SellOrder, Position, Quote
from app.brokers.fake import FakeBroker


def test_fake_broker_places_and_tracks_orders():
    b = FakeBroker(cash=10_000, quotes={"AAPL": 100.0})
    res = b.submit(BuyOrder(symbol="AAPL", qty=5, reason="test"))
    assert res.status == "filled"
    assert res.filled_price == 100.0
    positions = {p.symbol: p for p in b.get_positions()}
    assert positions["AAPL"].qty == 5
    assert b.get_quote("AAPL") == Quote(symbol="AAPL", price=100.0)


def test_fake_broker_sell_reduces_position():
    b = FakeBroker(cash=10_000, quotes={"AAPL": 100.0})
    b.submit(BuyOrder(symbol="AAPL", qty=5, reason="x"))
    b.submit(SellOrder(symbol="AAPL", qty=2, reason="x"))
    positions = {p.symbol: p for p in b.get_positions()}
    assert positions["AAPL"].qty == 3
