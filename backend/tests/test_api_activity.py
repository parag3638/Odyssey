def _make_account(client):
    r = client.post(
        "/accounts",
        json={"label": "Test", "alpaca_key_id": "PKTEST1234", "alpaca_secret": "secret"},
    )
    return r.json()["id"]


def test_activity_empty(client):
    r = client.get("/activity")
    assert r.status_code == 200
    assert r.json() == []


def test_activity_after_bot_tick(client):
    acct = _make_account(client)
    bot = client.post(
        "/bots",
        json={
            "name": "AAPL trail",
            "account_id": acct,
            "strategy_type": "trailing_stop",
            "symbol": "AAPL",
            "initial_shares": 1,
            "stop_pct": 0.1,
            "trail_pct": 0.05,
        },
    ).json()
    client.post(f"/bots/{bot['id']}/run")
    r = client.get("/activity")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    if rows:
        a = rows[0]
        assert "event" in a and "bot_name" in a
