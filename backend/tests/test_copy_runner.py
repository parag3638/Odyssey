from app.brokers.fake import FakeBroker
from app.services.copy_runner import run_copy_trade_tick


class _Act:
    def __init__(self, event, detail):
        self.event = event
        self.detail = detail


class _DB:
    def __init__(self, signals, copied):
        self._signals = signals
        self._copied = copied
        self.added = []
        self.committed = 0

    def add(self, o):
        self.added.append(o)

    def commit(self):
        self.committed += 1

    def refresh(self, o):
        pass


def test_copy_runner_buys_new_signal_and_logs_copied():
    signals = [{"hash": "h1", "symbol": "NVDA", "tx_type": "buy", "politician": "M M"}]
    db = _DB(signals, copied=set())
    broker = FakeBroker(cash=100_000, quotes={"NVDA": 100.0})
    res = run_copy_trade_tick(
        db, broker, account_id=1, bot_id=7,
        config={"politician": "M M", "per_trade_notional": 1000.0,
                "follow_buys": True, "follow_sells": True},
        recent_signals=signals, copied_hashes=set(), mode="paper")
    # one NVDA buy placed (10 sh), and a "copied" log for h1
    pos = {p.symbol: p.qty for p in broker.get_positions()}
    assert pos.get("NVDA") == 10
    assert any(getattr(o, "event", None) == "copied" and o.detail.get("hash") == "h1" for o in db.added)
    assert res["actions"] == 1
