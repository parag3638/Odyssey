from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from app.models import Bot, MarketCache, Signal, Ticker


def _account(client):
    response = client.post(
        "/accounts",
        json={"label": "Primary", "alpaca_key_id": "AKTEST1234", "alpaca_secret": "secret"},
    )
    return response.json()


def test_dashboard_bootstrap_combines_home_data_without_upstream_calls(client):
    account = _account(client)
    from app.db import get_sessionmaker

    db = get_sessionmaker()()
    try:
        db.add_all(
            [
                Ticker(symbol="AAPL", name="Apple", market_cap=3_000_000),
                Ticker(symbol="MSFT", name="Microsoft", market_cap=2_000_000),
                Bot(
                    name="AAPL trail",
                    account_id=account["id"],
                    strategy_type="trailing_stop",
                    status="active",
                    config={"symbol": "AAPL"},
                    schedule_cadence_sec=300,
                ),
                Signal(
                    politician="Jane Doe",
                    symbol="AAPL",
                    tx_type="buy",
                    tx_date="2026-08-01",
                    disclosed_date="2026-08-03",
                    amount_range="$1-15K",
                    source_url="",
                    hash="dashboard-signal",
                ),
                MarketCache(
                    key="quotes_all",
                    data={"AAPL": {"price": 123.0, "change": 2.0, "change_pct": 1.65}},
                    fetched_at=datetime.now(timezone.utc),
                ),
                MarketCache(
                    key="movers",
                    data={
                        "gainers": [{"symbol": "AAPL", "price": 123.0, "change_pct": 1.65}],
                        "losers": [],
                    },
                    fetched_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/dashboard/bootstrap")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account"]["id"] == account["id"]
    assert body["featured_symbol"] == "AAPL"
    assert body["stocks"][0]["price"] == 123.0
    assert body["bots"][0]["name"] == "AAPL trail"
    assert body["signals"][0]["politician"] == "Jane Doe"
    assert body["movers"]["gainers"][0]["symbol"] == "AAPL"
    assert "Server-Timing" in response.headers
    assert response.headers["X-Odyssey-Cache"] == "miss"
    assert client.get("/dashboard/bootstrap").headers["X-Odyssey-Cache"] == "hit"


def test_portfolio_overview_combines_positions_quotes_and_cash(client):
    account = _account(client)
    client.post(
        "/orders",
        json={"account_id": account["id"], "symbol": "AAPL", "qty": 2, "side": "buy"},
    )

    response = client.get(f'/positions/{account["id"]}/overview')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account"]["id"] == account["id"]
    assert body["positions"][0]["symbol"] == "AAPL"
    assert body["quotes"][0]["price"] == 100.0
    assert body["cash"] == 49_800.0


def test_dashboard_bootstrap_handles_twenty_concurrent_requests(client):
    _account(client)

    def fetch(_):
        return client.get("/dashboard/bootstrap").status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(fetch, range(20)))

    assert statuses == [200] * 20
