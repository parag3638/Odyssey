def _make_account(client):
    r = client.post("/accounts", json={"label": "tc", "alpaca_key_id": "AKTEST1234",
                                       "alpaca_secret": "secretXYZ"})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_and_run_trailing_bot(client):
    acct = _make_account(client)
    r = client.post("/bots", json={"name": "TSLA trail", "account_id": acct["id"],
                                   "symbol": "TSLA", "initial_shares": 10})
    assert r.status_code == 200, r.text
    bot = r.json()
    assert bot["strategy_type"] == "trailing_stop"
    assert bot["status"] == "active"

    # run one tick now (uses the injected FakeBroker with a TSLA quote)
    rt = client.post(f"/bots/{bot['id']}/run")
    assert rt.status_code == 200, rt.text
    assert rt.json()["actions"] >= 1

    # detail reflects the opened position
    rd = client.get(f"/bots/{bot['id']}")
    assert rd.status_code == 200
    body = rd.json()
    assert body["position"]["qty"] == 10
    assert float(body["position"]["stop_floor"]) > 0


def test_pause_bot(client):
    acct = _make_account(client)
    bot = client.post("/bots", json={"name": "x", "account_id": acct["id"], "symbol": "TSLA"}).json()
    r = client.patch(f"/bots/{bot['id']}", json={"status": "paused"})
    assert r.status_code == 200
    assert r.json()["status"] == "paused"
