def _make_account(client):
    r = client.post("/accounts", json={
        "label": "trading-claude", "alpaca_key_id": "AKTEST1234",
        "alpaca_secret": "secretXYZ", "endpoint": "https://paper-api.alpaca.markets"})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["mode"] == "paper"


def test_create_account_masks_secret(client):
    acct = _make_account(client)
    assert acct["mode"] == "paper"
    assert "secretXYZ" not in str(acct)
    assert acct["masked_secret"].startswith("••••")


def test_buy_order_end_to_end(client):
    acct = _make_account(client)
    r = client.post("/orders", json={"account_id": acct["id"], "symbol": "AAPL", "qty": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "filled"
    assert body["symbol"] == "AAPL"
    pos = client.get(f"/positions/{acct['id']}").json()
    assert any(p["symbol"] == "AAPL" and p["qty"] == 3 for p in pos)


def test_oversized_order_rejected(client):
    acct = _make_account(client)
    r = client.post("/orders", json={"account_id": acct["id"], "symbol": "AAPL", "qty": 200})
    assert r.status_code == 422
    assert "risk rejection" in r.json()["detail"]
