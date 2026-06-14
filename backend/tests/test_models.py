from app.models import BrokerageAccount, Order, ActivityLog


def test_models_have_expected_columns():
    assert {"id", "label", "mode", "alpaca_key_id", "alpaca_secret",
            "endpoint", "created_at"} <= set(BrokerageAccount.__table__.columns.keys())
    assert {"id", "account_id", "symbol", "side", "qty", "status",
            "alpaca_order_id", "reason", "dedupe_key", "submitted_at"} <= set(Order.__table__.columns.keys())
    assert {"id", "level", "event", "detail", "created_at"} <= set(ActivityLog.__table__.columns.keys())
