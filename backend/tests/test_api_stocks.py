from app.routers import stocks as stocks_router


class FakeMD:
    def snapshots(self, syms):
        return {
            s.upper(): {"price": 100.0, "prev_close": 95.0, "change": 5.0, "change_pct": 5.26}
            for s in syms
        }

    def bars(self, sym, rng="1M"):
        return [{"t": "2026-06-01T00:00:00+00:00", "price": 100.0, "volume": 1000.0}]

    def news(self, syms, limit=15):
        return []

    def movers(self):
        return {"gainers": [{"symbol": "AAPL", "price": 100.0, "change_pct": 3.0}], "losers": []}

    def dividends(self, sym):
        return []


class FakeFinnhub:
    enabled = True

    def metrics(self, s):
        return {"peBasicExclExtraTTM": 30.0, "marketCapitalization": 2_500_000.0}

    def earnings(self, s):
        return [{"period": "2026-03-31", "actual": 1.2, "estimate": 1.1}]

    def recommendation(self, s):
        return [{"period": "2026-06-01", "buy": 10, "hold": 3, "sell": 1}]


def _seed():
    from app.db import get_sessionmaker
    from app.models import Signal, Ticker

    db = get_sessionmaker()()
    try:
        db.add(Ticker(symbol="AAPL", name="Apple Inc.", sector="Technology", industry="Consumer Electronics"))
        db.add(Ticker(symbol="JPM", name="JPMorgan", sector="Financials", industry="Banks"))
        db.add(
            Signal(
                politician="Jane Doe", symbol="AAPL", tx_type="buy", tx_date="2026-05-01",
                disclosed_date="2026-05-03", amount_range="$1-15K", source_url="", hash="h-aapl-1",
            )
        )
        db.commit()
    finally:
        db.close()


def _patch():
    stocks_router.get_market_data = lambda db: FakeMD()
    stocks_router.get_finnhub = lambda: FakeFinnhub()


def test_list_stocks(client):
    _patch(); _seed()
    r = client.get("/stocks")
    assert r.status_code == 200
    rows = r.json()
    syms = {x["symbol"] for x in rows}
    assert {"AAPL", "JPM"} <= syms
    aapl = next(x for x in rows if x["symbol"] == "AAPL")
    assert aapl["price"] == 100.0 and aapl["sector"] == "Technology"


def test_filter_by_industry(client):
    _patch(); _seed()
    r = client.get("/stocks?industry=Banks")
    assert [x["symbol"] for x in r.json()] == ["JPM"]


def test_industries(client):
    _patch(); _seed()
    inds = {x["industry"] for x in client.get("/stocks/industries").json()}
    assert "Banks" in inds and "Consumer Electronics" in inds


def test_stock_detail(client):
    _patch(); _seed()
    d = client.get("/stocks/AAPL").json()
    assert d["symbol"] == "AAPL" and d["price"] == 100.0
    assert d["fundamentals"]["peBasicExclExtraTTM"] == 30.0


def test_stock_history(client):
    _patch(); _seed()
    r = client.get("/stocks/AAPL/history?range=1M")
    assert r.status_code == 200 and len(r.json()) == 1


def test_stock_signals(client):
    _patch(); _seed()
    sigs = client.get("/stocks/AAPL/signals").json()
    assert any(s["politician"] == "Jane Doe" for s in sigs)


class FakeFinnhubMetrics:
    enabled = True

    def metrics(self, s):
        # Finnhub /stock/metric "metric" dict shape (subset).
        return {
            "peTTM": 28.5,
            "epsTTM": 6.13,
            "revenueGrowthTTMYoy": 12.4,
            "psTTM": 7.0,
            "marketCapitalization": 2_800_000.0,  # Finnhub reports $M
        }

    def earnings_calendar(self, *a, **k):
        return {"earningsCalendar": [{"symbol": "AAPL", "date": "2026-07-30"}]}


def test_stock_metrics_batch(client):
    stocks_router.get_finnhub = lambda: FakeFinnhubMetrics()
    stocks_router.get_market_data = lambda db: FakeMD()
    _seed()
    r = client.get("/stocks/metrics?symbols=AAPL,JPM")
    assert r.status_code == 200
    data = r.json()
    a = data["AAPL"]
    assert a["pe"] == 28.5
    assert a["eps"] == 6.13
    assert a["revYoY"] == 12.4
    assert a["marketCap"] == 2_800_000.0 * 1e6
    # revenue ($) = marketCap / (P/S)
    assert abs(a["revenue"] - (2_800_000.0 * 1e6 / 7.0)) < 1.0
    # earnings calendar marks AAPL upcoming → "Pending"; JPM absent → None
    assert a["earnings"] == "Pending"
    assert data["JPM"]["earnings"] is None


def test_stock_metrics_no_key_degrades(client):
    """With a Finnhub client that returns None, every field is null, no error."""
    class _Empty:
        enabled = False
        def metrics(self, s):
            return None
        def earnings_calendar(self, *a, **k):
            return None

    stocks_router.get_finnhub = lambda: _Empty()
    stocks_router.get_market_data = lambda db: FakeMD()
    _seed()
    r = client.get("/stocks/metrics?symbols=AAPL")
    assert r.status_code == 200
    a = r.json()["AAPL"]
    assert a["pe"] is None and a["revenue"] is None and a["earnings"] is None
