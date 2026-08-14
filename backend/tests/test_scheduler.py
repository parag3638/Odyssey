from app.services.scheduler import should_run_tick


def test_skips_when_market_closed():
    assert should_run_tick(market_open=False, bot_status="active") is False


def test_skips_when_paused():
    assert should_run_tick(market_open=True, bot_status="paused") is False


def test_runs_when_open_and_active():
    assert should_run_tick(market_open=True, bot_status="active") is True


def test_copied_hashes_for_bot_reads_activity_log():
    from app.services.scheduler import copied_hashes_for_bot

    class _Row:
        def __init__(self, h):
            self.detail = {"hash": h}

    class _Q:
        def filter(self, *a, **k):
            return self
        def all(self):
            return [_Row("h1"), _Row("h2")]

    class _DB:
        def query(self, *a, **k):
            return _Q()

    assert copied_hashes_for_bot(_DB(), bot_id=1) == {"h1", "h2"}


def test_fundamentals_warmer_prioritizes_uncached_largest_companies(client, monkeypatch):
    from datetime import datetime, timezone

    from app.db import get_sessionmaker
    from app.models import MarketCache, Ticker
    from app.services import fundamentals
    from app.services.scheduler import refresh_fundamentals_cache_job

    db = get_sessionmaker()()
    try:
        db.add_all(
            [
                Ticker(symbol=f"T{i:02}", name=f"Ticker {i}", market_cap=(10 - i) * 1_000)
                for i in range(10)
            ]
        )
        db.add(
            MarketCache(
                key="metrics:T00",
                data={"peTTM": 20},
                fetched_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    captured = []
    monkeypatch.setattr(
        fundamentals,
        "refresh_fundamentals_symbols",
        lambda symbols: captured.extend(symbols),
    )

    refresh_fundamentals_cache_job()

    assert captured == [f"T{i:02}" for i in range(1, 9)]
