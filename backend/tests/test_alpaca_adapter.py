from app.brokers.base import BuyOrder, OrderResult
from app.brokers.alpaca import AlpacaBroker


class _StubOrder:
    id = "alp-123"
    status = "accepted"
    filled_avg_price = None


class _StubClient:
    def __init__(self):
        self.submitted = []

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return _StubOrder()


def test_alpaca_submit_maps_buy_order():
    client = _StubClient()
    broker = AlpacaBroker(client=client)
    res = broker.submit(BuyOrder(symbol="AAPL", qty=2, reason="manual"))
    assert isinstance(res, OrderResult)
    assert res.broker_order_id == "alp-123"
    assert res.status == "pending"          # 'accepted' maps to pending
    assert client.submitted[0].symbol == "AAPL"
    assert client.submitted[0].qty == 2
