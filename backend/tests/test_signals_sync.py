from app.services.signals_sync import upsert_signals


class _Q:
    def __init__(self, existing):
        self._existing = existing

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._existing


class _DB:
    def __init__(self, existing_hashes):
        self._existing = existing_hashes
        self.added = []
        self.committed = 0

    def query(self, model):
        class _Query:
            def __init__(self, hashes):
                self._h = hashes
            def with_entities(self, *a):
                return self
            def all(self):
                return [(h,) for h in self._h]
        return _Query(self._existing)

    def add(self, o):
        self.added.append(o)

    def commit(self):
        self.committed += 1


def test_upsert_adds_only_new():
    signals = [
        {"politician": "M M", "symbol": "NVDA", "tx_type": "buy", "tx_date": "2026-05-30",
         "disclosed_date": "2026-06-01", "amount_range": "250K–500K", "source_url": "u", "hash": "h1"},
        {"politician": "M M", "symbol": "AMZN", "tx_type": "sell", "tx_date": "2026-05-27",
         "disclosed_date": "2026-05-28", "amount_range": "15K–50K", "source_url": "u", "hash": "h2"},
    ]
    db = _DB(existing_hashes={"h1"})  # h1 already stored
    added = upsert_signals(db, signals)
    assert added == 1
    assert len(db.added) == 1
    assert db.added[0].hash == "h2"
    assert db.committed == 1
