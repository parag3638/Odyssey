from app.models import Signal


def test_signal_columns():
    cols = set(Signal.__table__.columns.keys())
    assert {"id", "politician", "symbol", "tx_type", "tx_date",
            "disclosed_date", "amount_range", "source_url", "hash", "scraped_at"} <= cols
