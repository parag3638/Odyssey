from datetime import datetime, timezone

from app.routers import research as research_router


def _seed_research():
    from app.db import get_sessionmaker
    from app.models import MarketCache, Signal, Ticker

    db = get_sessionmaker()()
    try:
        db.add(Ticker(symbol="NVDA", name="NVIDIA", sector="Technology", market_cap=3_000_000))
        db.add(
            Signal(
                politician="Jane Doe",
                symbol="NVDA",
                tx_type="buy",
                tx_date="2026-08-01",
                disclosed_date="2026-08-02",
                amount_range="$1-15K",
                source_url="",
                hash="research-nvda",
            )
        )
        now = datetime.now(timezone.utc)
        db.add_all(
            [
                MarketCache(
                    key="quotes_all",
                    data={"NVDA": {"price": 180.0, "change": 2.0, "change_pct": 1.12}},
                    fetched_at=now,
                ),
                MarketCache(
                    key="metrics:NVDA",
                    data={"peTTM": 30.0, "epsTTM": 4.5},
                    fetched_at=now,
                ),
                MarketCache(
                    key="bars:NVDA:1M",
                    data=[{"t": "2026-08-01T00:00:00Z", "price": 180.0, "volume": 1000}],
                    fetched_at=now,
                ),
                MarketCache(
                    key="news:NVDA",
                    data=[{"headline": "NVIDIA update", "summary": "", "source": "Wire"}],
                    fetched_at=now,
                ),
                MarketCache(
                    key="ai_research:NVDA",
                    data={
                        "summary": {"available": True, "text": "Cached summary", "citations": []},
                        "bull_bear": {
                            "available": True,
                            "bull": ["Growth"],
                            "bear": ["Valuation"],
                            "crux": "Execution",
                            "citations": [],
                        },
                    },
                    fetched_at=now,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_research_bootstrap_combines_cached_page_data(client, monkeypatch):
    _seed_research()
    monkeypatch.setattr(research_router, "refresh_research_symbol", lambda *args: None)

    response = client.get("/research/NVDA/bootstrap?range=1M")

    assert response.status_code == 200
    body = response.json()
    assert body["stock"]["symbol"] == "NVDA"
    assert body["stock"]["price"] == 180.0
    assert body["stock"]["fundamentals"]["peTTM"] == 30.0
    assert body["history"][0]["price"] == 180.0
    assert body["signals"][0]["politician"] == "Jane Doe"
    assert body["news"][0]["headline"] == "NVIDIA update"
    assert body["ai_summary"]["text"] == "Cached summary"
    assert body["bull_bear"]["crux"] == "Execution"
    assert body["ai_pending"] is False
    etag = response.headers["ETag"]
    assert client.get(
        "/research/NVDA/bootstrap?range=1M",
        headers={"If-None-Match": etag},
    ).status_code == 304


def test_combined_research_ai_uses_one_model_call(client, monkeypatch):
    from app.db import get_sessionmaker
    from app.models import MarketCache
    from app.routers import ai as ai_router
    from app.services.research import _generate_combined_ai

    _seed_research()
    db = get_sessionmaker()()
    try:
        cached = db.get(MarketCache, "ai_research:NVDA")
        db.delete(cached)
        db.add(
            MarketCache(
                key="reco:NVDA",
                data=[{"period": "2026-08", "buy": 20, "hold": 5, "sell": 1}],
                fetched_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    class FakeLlm:
        enabled = True
        model = "test-model"

        def __init__(self):
            self.calls = 0

        def complete_json(self, **kwargs):
            self.calls += 1
            return {
                "summary": "Combined summary",
                "summary_citation_ids": [],
                "bull": ["Demand"],
                "bear": ["Valuation"],
                "crux": "Growth durability",
                "bull_bear_citation_ids": [],
            }

    llm = FakeLlm()
    monkeypatch.setattr(ai_router, "get_llm", lambda: llm)

    assert _generate_combined_ai("NVDA") is True
    assert llm.calls == 1
