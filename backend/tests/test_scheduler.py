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
