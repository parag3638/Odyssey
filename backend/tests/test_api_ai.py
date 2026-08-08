"""AI advisory layer — Phase 0 (per-stock summary).

Mirrors test_api_stocks.py: fake the LLM/market-data/finnhub seams on the router,
seed a ticker, hit the endpoint, assert on grounding, citation validation, caching,
graceful no-key degradation, and the read-only "wall" between AI and the order path.
"""
from app.routers import ai as ai_router

NEWS = [
    {
        "headline": "Apple unveils new chip",
        "summary": "Faster and more efficient.",
        "source": "Reuters",
        "url": "https://example.com/a1",
        "datetime": "2026-08-01T00:00:00+00:00",
        "image": "",
        "symbols": ["AAPL"],
    },
    {
        "headline": "Analysts weigh in on Apple",
        "summary": "Views are mixed heading into earnings.",
        "source": "Bloomberg",
        "url": "https://example.com/a2",
        "datetime": "2026-08-02T00:00:00+00:00",
        "image": "",
        "symbols": ["AAPL"],
    },
]


class FakeMD:
    def news(self, syms, limit=15):
        return list(NEWS)


class FakeFinnhub:
    enabled = True

    def recommendation(self, s):
        return [{
            "period": "2026-08-01", "strongBuy": 5, "buy": 10,
            "hold": 3, "sell": 1, "strongSell": 0,
        }]

    def metrics(self, s):
        return {"peTTM": 30.0, "epsTTM": 6.1}


class FakeLLM:
    enabled = True
    model = "fake-model"

    def __init__(self, text="Apple looks solid heading into earnings.", citation_ids=None):
        self._text = text
        self._cids = citation_ids if citation_ids is not None else ["n1", "n2"]

    def complete_json(self, *, system, user, schema, model=None, max_tokens=700):
        return {"text": self._text, "citation_ids": list(self._cids)}


def _seed():
    from app.db import get_sessionmaker
    from app.models import Ticker

    db = get_sessionmaker()()
    try:
        db.add(Ticker(
            symbol="AAPL", name="Apple Inc.",
            sector="Technology", industry="Consumer Electronics",
        ))
        db.commit()
    finally:
        db.close()


def _patch(llm=None):
    ai_router.get_market_data = lambda db: FakeMD()
    ai_router.get_finnhub = lambda: FakeFinnhub()
    ai_router.get_llm = lambda: (llm if llm is not None else FakeLLM())


def test_ai_summary_grounded_and_cited(client):
    _seed()
    _patch(FakeLLM(text="Apple summary.", citation_ids=["n1", "bogus", "n2"]))
    r = client.get("/ai/stocks/AAPL/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert d["text"] == "Apple summary."
    # The fabricated "bogus" id is dropped; only real news sources survive, in order.
    assert [c["url"] for c in d["citations"]] == ["https://example.com/a1", "https://example.com/a2"]
    assert "not personalized investment advice" in d["disclaimer"]


def test_ai_summary_drops_uncited(client):
    # r1/f1 are facts with no URL source, "nope" is unknown → all dropped.
    _seed()
    _patch(FakeLLM(text="x", citation_ids=["nope", "r1", "f1"]))
    d = client.get("/ai/stocks/AAPL/summary").json()
    assert d["available"] is True
    assert d["citations"] == []


def test_ai_summary_no_key_degrades(client):
    _seed()

    class Off:
        enabled = False
        model = "gpt-4o-mini"

    _patch(Off())
    r = client.get("/ai/stocks/AAPL/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is False and d["text"] is None


def test_ai_summary_is_cached(client):
    """Second identical request serves from market_cache — the LLM is hit once."""
    _seed()

    class CountingLLM(FakeLLM):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def complete_json(self, **k):
            self.calls += 1
            return {"text": "cached", "citation_ids": ["n1"]}

    llm = CountingLLM()
    _patch(llm)
    client.get("/ai/stocks/AAPL/summary")
    client.get("/ai/stocks/AAPL/summary")
    assert llm.calls == 1


def test_ai_layer_not_imported_by_order_path():
    """The wall: the advisory AI layer must never be importable from the order/risk
    path, so it can never sit in a trading decision."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    forbidden_tokens = ["llm", "services.ai", "services import ai", "routers.ai"]
    for rel in ["risk.py", "services/orders.py", "services/runner.py", "services/copy_runner.py"]:
        src = (app_dir / rel).read_text()
        for tok in forbidden_tokens:
            assert tok not in src, f"{rel} must not reference the AI layer (found '{tok}')"
