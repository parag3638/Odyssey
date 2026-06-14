from app.routers import stocks as stocks_router


class FakeMD:
    def snapshots(self, syms):
        return {s.upper(): {"price": 100.0, "prev_close": 95.0, "change": 5.0, "change_pct": 5.26} for s in syms}


def test_watchlist_flow(client):
    stocks_router.get_market_data = lambda db: FakeMD()

    r = client.post("/watchlist", json={"symbol": "aapl"})
    assert r.status_code == 200
    assert any(x["symbol"] == "AAPL" for x in r.json())

    # idempotent add
    client.post("/watchlist", json={"symbol": "AAPL"})
    assert len(client.get("/watchlist").json()) == 1

    r = client.delete("/watchlist/AAPL")
    assert all(x["symbol"] != "AAPL" for x in r.json())
