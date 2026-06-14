import pytest
from app.brokers.base import BuyOrder
from app.brokers.fake import FakeBroker
from app.risk import RiskRejection
from app.services.orders import place_order


class _Recorder:
    """Stand-in for a DB session: records what would be persisted."""
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def test_place_order_persists_filled_order_and_log():
    db = _Recorder()
    broker = FakeBroker(cash=10_000, quotes={"AAPL": 100.0})
    order = place_order(db, broker, account_id=1, mode="paper",
                        action=BuyOrder("AAPL", 5, reason="manual"),
                        max_position_notional=10_000)
    assert order.status == "filled"
    assert order.symbol == "AAPL"
    assert db.committed is True
    kinds = sorted(type(o).__name__ for o in db.added)
    assert kinds == ["ActivityLog", "Order"]


def test_place_order_rejected_by_risk_logs_and_raises():
    db = _Recorder()
    broker = FakeBroker(cash=100, quotes={"AAPL": 100.0})
    with pytest.raises(RiskRejection):
        place_order(db, broker, account_id=1, mode="paper",
                    action=BuyOrder("AAPL", 100, reason="manual"),
                    max_position_notional=1_000_000)
    assert any(type(o).__name__ == "ActivityLog" and o.level == "warn" for o in db.added)
