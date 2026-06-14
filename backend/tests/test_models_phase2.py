from app.models import Bot, Position


def test_bot_and_position_columns():
    assert {"id", "name", "account_id", "strategy_type", "status",
            "config", "schedule_cadence_sec", "created_at"} <= set(Bot.__table__.columns.keys())
    assert {"id", "bot_id", "symbol", "qty", "avg_entry_price",
            "stop_floor", "triggered_rungs", "updated_at"} <= set(Position.__table__.columns.keys())
