"""Global assistant (chat) — streaming + non-streaming, grounding, citations, wall.

Mirrors test_api_ai.py: fake the llm/market-data/finnhub/broker seams on the router,
seed a ticker + signal, and assert on grounded answers, SSE framing, and the wall.
"""
import json

from app.brokers.base import BuyOrder
from app.brokers.fake import FakeBroker
from app.routers import ai as ai_router
from app.services import ai as ai_service

NEWS = [{
    "headline": "Apple ships M5 chip", "summary": "Faster and cooler.", "source": "Reuters",
    "url": "https://ex.com/a1", "datetime": "2026-08-01T00:00:00+00:00",
    "image": "", "symbols": ["AAPL"],
}]


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


class FakeChatLLM:
    enabled = True
    model = "fake-model"

    def __init__(self, text="Apple shipped the M5 chip [n1].", tokens=None):
        self._text = text
        self._tokens = tokens if tokens is not None else ["Hello ", "world ", "[n1]"]

    def chat(self, *, messages, system, model=None, max_tokens=900):
        return self._text

    def stream_messages(self, *, messages, system, model=None, max_tokens=900):
        yield from self._tokens


def _seed():
    from app.db import get_sessionmaker
    from app.models import Signal, Ticker

    db = get_sessionmaker()()
    try:
        db.add(Ticker(
            symbol="AAPL", name="Apple Inc.",
            sector="Technology", industry="Consumer Electronics",
        ))
        db.add(Signal(
            politician="Jane Doe", symbol="AAPL", tx_type="buy", tx_date="2026-05-01",
            disclosed_date="2026-05-03", amount_range="$1-15K",
            source_url="https://ex.com/s1", hash="h1",
        ))
        db.commit()
    finally:
        db.close()


def _patch(llm, broker=None):
    ai_router.get_market_data = lambda db: FakeMD()
    ai_router.get_finnhub = lambda: FakeFinnhub()
    ai_router.get_llm = lambda: llm
    ai_router.get_broker = lambda db: broker


def _frames(body: str):
    return [ln[len("data: "):] for ln in body.splitlines() if ln.startswith("data: ")]


def test_chat_nonstream_grounded_and_cited(client):
    _seed()
    fake = FakeBroker(cash=5000, quotes={"AAPL": 100.0}, prev_closes={"AAPL": 95.0})
    fake.submit(BuyOrder("AAPL", 10))  # seed a holding
    answer = "Apple shipped the M5 chip [n1]. You hold 10 AAPL [pf]."
    _patch(FakeChatLLM(text=answer), broker=fake)
    r = client.post("/ai/chat", json={
        "messages": [{"role": "user", "content": "what's up with apple?"}],
        "context": {"symbol": "AAPL"},
    })
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert "M5" in d["text"]
    # n1 → the news source; pf (portfolio) has no linkable source so it's dropped.
    assert [c["url"] for c in d["citations"]] == ["https://ex.com/a1"]


def test_chat_stream_frames(client):
    _seed()
    _patch(FakeChatLLM(tokens=["Hello ", "world ", "[n1]"]), broker=FakeBroker())
    r = client.post("/ai/chat/stream", json={
        "messages": [{"role": "user", "content": "hi"}],
        "context": {"symbol": "AAPL"},
    })
    assert r.status_code == 200
    frames = _frames(r.text)
    assert frames[-1] == "[DONE]"
    parsed = [json.loads(f) for f in frames if f != "[DONE]"]
    tokens = [p["v"] for p in parsed if p["type"] == "token"]
    assert "".join(tokens) == "Hello world [n1]"
    cites = [p for p in parsed if p["type"] == "citations"]
    assert cites and cites[0]["v"][0]["url"] == "https://ex.com/a1"


def test_chat_no_key_degrades(client):
    _seed()

    class Off:
        enabled = False
        model = "gpt-5.6"

    _patch(Off(), broker=FakeBroker())
    r = client.post("/ai/chat", json={
        "messages": [{"role": "user", "content": "hi"}], "context": {},
    })
    assert r.status_code == 200 and r.json()["available"] is False


def test_build_chat_bundle_no_account():
    b = ai_service.build_chat_bundle(None, None, {"connected": False})
    assert "pnone" in [f["id"] for f in b["facts"]]
    assert b["sources"] == []


def test_ai_layer_does_not_import_order_path():
    """The AI layer must not import the order/risk path (the wall, other direction).
    Scans import statements only, so docstrings that merely name those modules are fine."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    forbidden = ("app.risk", "app.services.orders",
                 "app.services.runner", "app.services.copy_runner")
    for rel in ["routers/ai.py", "services/ai.py", "services/llm.py"]:
        for line in (app_dir / rel).read_text().splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")):
                for mod in forbidden:
                    assert mod not in s, f"{rel} imports the order path: {line!r}"
