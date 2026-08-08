"""Phase 1 research panels — bull-vs-bear synthesis + congressional-trade context."""
from app.routers import ai as ai_router
from app.services import ai as ai_service

NEWS = [{
    "headline": "Apple services revenue jumps", "summary": "Growth accelerates.",
    "source": "Reuters", "url": "https://ex.com/a1", "datetime": "2026-08-01T00:00:00+00:00",
    "image": "", "symbols": ["AAPL"],
}]


class FakeMD:
    def news(self, syms, limit=15):
        return list(NEWS)


class FakeFinnhub:
    enabled = True

    def recommendation(self, s):
        return [{
            "period": "2026-08-01", "strongBuy": 6, "buy": 12,
            "hold": 4, "sell": 1, "strongSell": 0,
        }]

    def metrics(self, s):
        return {"peTTM": 34.0, "epsTTM": 6.4, "revenueGrowthTTMYoy": 9.0}


class FakeLLM:
    """complete_json picks its shape from the schema it's handed."""
    enabled = True
    model = "fake-model"

    def complete_json(self, *, system, user, schema, model=None, max_tokens=700):
        if "bull" in schema.get("properties", {}):
            return {
                "bull": ["Services growth accelerating [n1]", "Analysts mostly positive [r1]"],
                "bear": ["Rich valuation at 34x [f1]"],
                "crux": "Growth vs valuation.",
                "citation_ids": ["n1", "r1", "f1"],
            }
        return {
            "text": "Two lawmakers bought AAPL within days [s1][s2].",
            "citation_ids": ["s1", "s2"],
        }


def _seed(with_signals=True):
    from app.db import get_sessionmaker
    from app.models import Signal, Ticker

    db = get_sessionmaker()()
    try:
        db.add(Ticker(
            symbol="AAPL", name="Apple Inc.",
            sector="Technology", industry="Consumer Electronics",
        ))
        if with_signals:
            db.add(Signal(
                politician="Jane Doe", symbol="AAPL", tx_type="buy", tx_date="2026-05-01",
                disclosed_date="2026-05-03", amount_range="$1-15K",
                source_url="https://ex.com/s1", hash="h1",
            ))
            db.add(Signal(
                politician="John Roe", symbol="AAPL", tx_type="buy", tx_date="2026-05-10",
                disclosed_date="2026-05-12", amount_range="$15-50K",
                source_url="https://ex.com/s2", hash="h2",
            ))
        db.commit()
    finally:
        db.close()


def _patch(llm=None):
    ai_router.get_market_data = lambda db: FakeMD()
    ai_router.get_finnhub = lambda: FakeFinnhub()
    ai_router.get_llm = lambda: (llm if llm is not None else FakeLLM())


def test_bull_bear(client):
    _seed()
    _patch()
    d = client.get("/ai/stocks/AAPL/bull-bear").json()
    assert d["available"] is True
    assert len(d["bull"]) >= 1 and len(d["bear"]) >= 1
    assert d["crux"] == "Growth vs valuation."
    # only n1 has a linkable source; r1/f1 are dropped.
    assert [c["url"] for c in d["citations"]] == ["https://ex.com/a1"]


def test_bull_bear_no_key(client):
    _seed()

    class Off:
        enabled = False
        model = "gpt-5.6"

    _patch(Off())
    assert client.get("/ai/stocks/AAPL/bull-bear").json()["available"] is False


def test_congress_context(client):
    _seed()
    _patch()
    d = client.get("/ai/signals/AAPL/context").json()
    assert d["available"] is True
    assert "lawmakers" in d["text"] or "bought" in d["text"]
    urls = {c["url"] for c in d["citations"]}
    assert urls == {"https://ex.com/s1", "https://ex.com/s2"}


def test_congress_context_no_signals(client):
    _seed(with_signals=False)
    _patch()
    assert client.get("/ai/signals/AAPL/context").json()["available"] is False


def test_cluster_stats_detects_cluster():
    signals = [
        {"politician": "Jane Doe", "tx_type": "buy", "tx_date": "2026-05-01"},
        {"politician": "John Roe", "tx_type": "buy", "tx_date": "2026-05-10"},
    ]
    stats = ai_service.cluster_stats(signals)
    assert stats["politicians"] == 2 and stats["cluster"] is True and stats["span_days"] == 9

    far = [
        {"politician": "Jane Doe", "tx_type": "buy", "tx_date": "2026-01-01"},
        {"politician": "John Roe", "tx_type": "sell", "tx_date": "2026-08-01"},
    ]
    assert ai_service.cluster_stats(far)["cluster"] is False
