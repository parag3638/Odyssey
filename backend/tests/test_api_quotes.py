def _make_account(client):
    r = client.post(
        "/accounts",
        json={"label": "Test", "alpaca_key_id": "PKTEST1234", "alpaca_secret": "secret"},
    )
    assert r.status_code == 200
    return r.json()["id"]


def test_quotes_with_explicit_symbols(client):
    acct = _make_account(client)
    r = client.get(f"/positions/{acct}/quotes?symbols=AAPL,TSLA")
    assert r.status_code == 200
    data = {q["symbol"]: q for q in r.json()}
    assert data["AAPL"]["price"] == 100.0
    assert data["TSLA"]["price"] == 250.0


def test_quotes_skip_unknown_symbols(client):
    acct = _make_account(client)
    r = client.get(f"/positions/{acct}/quotes?symbols=AAPL,ZZZZ")
    assert r.status_code == 200
    syms = [q["symbol"] for q in r.json()]
    assert "AAPL" in syms
    assert "ZZZZ" not in syms


def test_quotes_default_to_holdings(client):
    acct = _make_account(client)
    # buy AAPL so it becomes a holding
    client.post("/orders", json={"account_id": acct, "symbol": "AAPL", "qty": 2, "side": "buy"})
    r = client.get(f"/positions/{acct}/quotes")
    assert r.status_code == 200
    syms = [q["symbol"] for q in r.json()]
    assert "AAPL" in syms


def test_account_summary_returns_cash(client):
    acct = _make_account(client)
    r = client.get(f"/positions/{acct}/summary")
    assert r.status_code == 200
    assert r.json()["cash"] == 50000.0
