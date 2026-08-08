"""Phase 2 — portfolio health, risk-check explainer, natural-language screener."""
from app.brokers.base import BuyOrder
from app.brokers.fake import FakeBroker
from app.routers import ai as ai_router
from app.services import ai as ai_service


class FakeLLM:
    """Returns a shape matching whichever schema it's handed."""
    enabled = True
    model = "fake-model"

    def complete_json(self, *, system, user, schema, model=None, max_tokens=700):
        props = schema.get("properties", {})
        if "filters" in props:
            return {
                "filters": [
                    {"field": "revenue", "op": ">", "value": "10000000000"},
                    {"field": "revYoY", "op": ">", "value": "10"},
                    {"field": "bogus_field", "op": ">", "value": "1"},  # must be dropped
                ],
                "sort_field": "pe",
                "sort_dir": "asc",
                "note": "Congressional buying isn't a screenable field here.",
            }
        return {"text": "Your portfolio leans heavily on one name."}


def _seed():
    from app.db import get_sessionmaker
    from app.models import Ticker

    db = get_sessionmaker()()
    try:
        db.add(Ticker(symbol="AAPL", name="Apple", sector="Technology",
                      industry="Consumer Electronics"))
        db.add(Ticker(symbol="JPM", name="JPMorgan", sector="Financials", industry="Banks"))
        db.commit()
    finally:
        db.close()


def _patch(llm=None, broker=None):
    ai_router.get_llm = lambda: (llm if llm is not None else FakeLLM())
    ai_router.get_broker = lambda db: broker


def test_portfolio_health(client):
    _seed()
    fake = FakeBroker(cash=1000, quotes={"AAPL": 100.0, "JPM": 50.0})
    fake.submit(BuyOrder("AAPL", 30))   # $3000
    fake.submit(BuyOrder("JPM", 20))    # $1000
    _patch(broker=fake)
    d = client.get("/ai/portfolio/health").json()
    assert d["available"] is True
    assert d["text"]
    labels = {c["label"]: c["weight_pct"] for c in d["concentrations"]}
    assert labels["AAPL"] == 75.0 and labels["JPM"] == 25.0
    assert labels["Technology"] == 75.0


def test_portfolio_health_no_account(client):
    _seed()
    _patch(broker=None)
    assert client.get("/ai/portfolio/health").json()["available"] is False


def test_risk_explain(client):
    _seed()
    _patch(broker=FakeBroker())
    r = client.post("/ai/risk/explain", json={
        "reason": "insufficient buying power: need 5000.00, have 100.00",
        "symbol": "AAPL", "qty": 50, "side": "buy",
    })
    assert r.status_code == 200
    assert r.json()["available"] is True and r.json()["text"]


def test_risk_explain_requires_reason(client):
    _seed()
    _patch(broker=FakeBroker())
    r = client.post("/ai/risk/explain", json={"reason": "  ", "symbol": "AAPL", "qty": 1})
    assert r.json()["available"] is False


def test_screener_parse_drops_unknown_fields(client):
    _seed()
    _patch(broker=FakeBroker())
    d = client.post("/ai/screener/parse", json={"query": "big fast-growing companies"}).json()
    assert d["available"] is True
    fields = [f["field"] for f in d["filters"]]
    assert fields == ["revenue", "revYoY"]  # bogus_field dropped
    assert d["sort_field"] == "pe" and d["sort_dir"] == "asc"
    assert "Congressional" in (d["note"] or "")


def test_screener_parse_empty_query(client):
    _seed()
    _patch(broker=FakeBroker())
    assert client.post("/ai/screener/parse", json={"query": ""}).json()["available"] is False


def test_portfolio_concentrations_math():
    conc = ai_service.portfolio_concentrations(
        [{"symbol": "AAPL", "qty": 10, "price": 100.0},
         {"symbol": "JPM", "qty": 10, "price": 25.0}],
        {"AAPL": "Technology", "JPM": "Financials"},
    )
    assert conc["total"] == 1250.0
    assert conc["positions"][0] == {"label": "AAPL", "weight_pct": 80.0}
    assert conc["top_weight"] == 80.0
    assert {s["label"] for s in conc["sectors"]} == {"Technology", "Financials"}


def test_risk_explainer_is_not_in_the_decision_path():
    """The wall: risk.py decides alone; the explainer only reads its output."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    risk_src = (app_dir / "risk.py").read_text()
    assert "llm" not in risk_src.lower() and "ai" not in risk_src.split("import")[0].lower()
    orders_src = (app_dir / "services" / "orders.py").read_text()
    for line in orders_src.splitlines():
        if line.strip().startswith(("import ", "from ")):
            assert "services.ai" not in line and "services.llm" not in line
