import pytest
from app.brokers.fake import FakeBroker
from app.strategies.trailing_stop import TrailingState
from app.services.runner import run_trailing_stop_tick


class _Pos:
    def __init__(self):
        self.qty = 0
        self.avg_entry_price = None
        self.stop_floor = None
        self.triggered_rungs = []


class _DB:
    def __init__(self, pos):
        self._pos = pos
        self.committed = 0
        self.added = []

    def add(self, o):
        self.added.append(o)

    def commit(self):
        self.committed += 1

    def refresh(self, o):
        pass


CFG = {"symbol": "TSLA", "initial_shares": 10, "stop_pct": 0.10, "trail_pct": 0.05, "ladder": []}


def test_tick_opens_position_and_records_floor():
    pos = _Pos()
    db = _DB(pos)
    broker = FakeBroker(cash=100_000, quotes={"TSLA": 250.0})
    result = run_trailing_stop_tick(db, broker, account_id=1, bot_id=1, config=CFG, position=pos, mode="paper")
    # engine wanted an initial buy of 10; fake broker fills it
    assert pos.qty == 10
    assert round(float(pos.stop_floor), 2) == 225.0  # 250 * 0.9
    assert result["actions"] >= 1
    assert db.committed >= 1
