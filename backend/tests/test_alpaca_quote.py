from app.brokers.base import Quote, Clock
from app.brokers.alpaca import AlpacaBroker


class _Trade:
    price = 142.5


class _StubData:
    def get_stock_latest_trade(self, req):
        return {"AAPL": _Trade()}


class _StubClock:
    is_open = True


class _StubTrading:
    def get_clock(self):
        return _StubClock()


def test_get_quote_uses_latest_trade():
    b = AlpacaBroker(client=_StubTrading(), data_client=_StubData())
    q = b.get_quote("AAPL")
    assert q == Quote(symbol="AAPL", price=142.5)


def test_get_clock_maps_is_open():
    b = AlpacaBroker(client=_StubTrading(), data_client=_StubData())
    assert b.get_clock() == Clock(is_open=True)
