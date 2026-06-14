import app.routers.signals as sig


def test_sync_endpoint_returns_added_count(client, monkeypatch):
    monkeypatch.setattr(sig, "_sync", lambda db: 3)
    r = client.post("/signals/sync")
    assert r.status_code == 200
    assert r.json() == {"added": 3}


def test_sync_endpoint_surfaces_failure_as_502(client, monkeypatch):
    def _boom(db):
        raise RuntimeError("capitol trades unreachable")
    monkeypatch.setattr(sig, "_sync", _boom)
    r = client.post("/signals/sync")
    assert r.status_code == 502
    assert "capitol trades fetch failed" in r.json()["detail"]
