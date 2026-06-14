from app.models import Signal


def _acct(client):
    return client.post("/accounts", json={"label": "tc", "alpaca_key_id": "AKTEST1234",
                                          "alpaca_secret": "secretXYZ"}).json()


def _seed_signal(client, **kw):
    # insert a signal row directly via the app's test DB session
    from app.db import get_db
    gen = client.app.dependency_overrides[get_db]()
    db = next(gen)
    s = Signal(politician=kw.get("politician", "M M"), symbol=kw["symbol"],
               tx_type=kw["tx_type"], tx_date="2026-05-30", disclosed_date="2026-06-01",
               amount_range="250K–500K", source_url="u", hash=kw["hash"])
    db.add(s); db.commit()
    try:
        next(gen)
    except StopIteration:
        pass


def test_create_copy_bot_and_run(client):
    acct = _acct(client)
    _seed_signal(client, symbol="AAPL", tx_type="buy", hash="hbuy1", politician="M M")
    r = client.post("/bots", json={"name": "copy mccaul", "account_id": acct["id"],
                                   "strategy_type": "copy_trade", "politician": "M M",
                                   "per_trade_notional": 500})
    assert r.status_code == 200, r.text
    bot = r.json()
    assert bot["strategy_type"] == "copy_trade"
    rt = client.post(f"/bots/{bot['id']}/run")
    assert rt.status_code == 200, rt.text
    assert rt.json()["actions"] >= 1


def test_signals_endpoint_lists(client):
    _seed_signal(client, symbol="NVDA", tx_type="buy", hash="hn1", politician="M M")
    r = client.get("/signals")
    assert r.status_code == 200
    assert any(s["symbol"] == "NVDA" for s in r.json())
