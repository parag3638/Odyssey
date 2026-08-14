from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone

_scheduler: BackgroundScheduler | None = None


def should_run_tick(*, market_open: bool, bot_status: str) -> bool:
    return bool(market_open) and bot_status == "active"


def copied_hashes_for_bot(db, *, bot_id) -> set:
    from app.models import ActivityLog
    rows = (db.query(ActivityLog)
            .filter(ActivityLog.bot_id == bot_id, ActivityLog.event == "copied").all())
    return {r.detail.get("hash") for r in rows if r.detail and r.detail.get("hash")}


def tick_all_active_bots():
    from app.db import get_sessionmaker
    from app.models import Bot, Position, BrokerageAccount, Signal
    from app.routers.bots import build_bot_broker
    from app.services.runner import run_trailing_stop_tick
    from app.services.copy_runner import run_copy_trade_tick

    db = get_sessionmaker()()
    try:
        bots = db.query(Bot).filter(Bot.status == "active").all()
        for bot in bots:
            account = db.get(BrokerageAccount, bot.account_id)
            if account is None:
                continue
            broker = build_bot_broker(account)
            try:
                if not should_run_tick(market_open=broker.get_clock().is_open, bot_status=bot.status):
                    continue
            except Exception:
                continue
            if bot.strategy_type == "trailing_stop":
                pos = db.query(Position).filter(Position.bot_id == bot.id).first()
                if pos is None:
                    pos = Position(bot_id=bot.id, symbol=bot.config["symbol"], qty=0, triggered_rungs=[])
                    db.add(pos); db.commit(); db.refresh(pos)
                run_trailing_stop_tick(db, broker, account_id=account.id, bot_id=bot.id,
                                       config=bot.config, position=pos, mode=account.mode)
            elif bot.strategy_type == "copy_trade":
                pol = bot.config.get("politician", "")
                q = db.query(Signal)
                if pol and pol != "auto":
                    q = q.filter(Signal.politician == pol)
                recent = [{"hash": s.hash, "symbol": s.symbol, "tx_type": s.tx_type,
                           "politician": s.politician}
                          for s in q.order_by(Signal.id.desc()).limit(50).all()]
                run_copy_trade_tick(db, broker, account_id=account.id, bot_id=bot.id,
                                    config=bot.config, recent_signals=recent,
                                    copied_hashes=copied_hashes_for_bot(db, bot_id=bot.id),
                                    mode=account.mode)
    finally:
        db.close()


def scrape_signals_job():
    """Slow cadence: pull latest Capitol Trades into the signals table."""
    from app.db import get_sessionmaker
    from app.services.signals_sync import sync_from_capitol_trades
    db = get_sessionmaker()()
    try:
        sync_from_capitol_trades(db, limit=50)
        from app.routers.dashboard import clear_dashboard_cache
        clear_dashboard_cache()
    except Exception:
        pass
    finally:
        db.close()


def refresh_market_cache_job():
    """Refresh shared quote/mover caches away from the request path."""
    from app.db import get_sessionmaker
    from app.models import Ticker
    from app.services.finnhub import set_cached_many
    from app.services.market_data import market_data_for

    db = get_sessionmaker()()
    try:
        market_data = market_data_for(db)
        symbols = [symbol for (symbol,) in db.query(Ticker.symbol).all()]
        db.rollback()
        if market_data is None or not symbols:
            return
        quotes = market_data.snapshots(symbols)
        movers = market_data.movers()
        set_cached_many(db, {"quotes_all": quotes, "movers": movers})
        from app.routers.dashboard import clear_dashboard_cache
        clear_dashboard_cache()
    except Exception:
        db.rollback()
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(daemon=True)
    common = {"replace_existing": True, "coalesce": True, "max_instances": 1}
    _scheduler.add_job(tick_all_active_bots, "interval", seconds=300, id="tick_all", **common)
    _scheduler.add_job(scrape_signals_job, "interval", seconds=3600, id="scrape_signals", **common)
    _scheduler.add_job(
        refresh_market_cache_job,
        "interval",
        seconds=240,
        id="refresh_market_cache",
        next_run_time=datetime.now(timezone.utc),
        **common,
    )
    _scheduler.start()
    return _scheduler
