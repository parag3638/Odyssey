def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("FERNET_KEY", "k")
    monkeypatch.setenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")
    from app.config import Settings
    s = Settings()
    assert s.database_url == "postgresql+psycopg://x/y"
    assert s.alpaca_endpoint.endswith("alpaca.markets")
