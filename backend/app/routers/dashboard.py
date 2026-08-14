from fastapi import APIRouter, Depends, Response
from sqlalchemy import desc
from sqlalchemy.orm import Session
from threading import Lock
from time import monotonic

from app.db import get_db
from app.models import Bot, BrokerageAccount, Signal, Ticker
from app.schemas import (
    AccountOut,
    BotOut,
    DashboardBootstrapOut,
    MoversOut,
    SignalOut,
    StockRow,
)
from app.services.finnhub import cached_value


router = APIRouter(prefix="/dashboard")
_cache_lock = Lock()
_bootstrap_cache: tuple[float, DashboardBootstrapOut] | None = None


def clear_dashboard_cache() -> None:
    global _bootstrap_cache
    with _cache_lock:
        _bootstrap_cache = None


def _get_dashboard_cache() -> DashboardBootstrapOut | None:
    with _cache_lock:
        if _bootstrap_cache is None or _bootstrap_cache[0] <= monotonic():
            return None
        return _bootstrap_cache[1]


def _set_dashboard_cache(value: DashboardBootstrapOut) -> None:
    global _bootstrap_cache
    with _cache_lock:
        _bootstrap_cache = (monotonic() + 10, value)


def _account_out(account: BrokerageAccount | None) -> AccountOut | None:
    if account is None:
        return None
    return AccountOut(
        id=account.id,
        label=account.label,
        mode=account.mode,
        masked_secret="••••" + account.alpaca_key_id[-4:],
    )


def _bot_out(bot: Bot) -> BotOut:
    return BotOut(
        id=bot.id,
        name=bot.name,
        strategy_type=bot.strategy_type,
        status=bot.status,
        config=bot.config,
        schedule_cadence_sec=bot.schedule_cadence_sec,
    )


def _signal_out(signal: Signal) -> SignalOut:
    return SignalOut(
        id=signal.id,
        politician=signal.politician,
        symbol=signal.symbol,
        tx_type=signal.tx_type,
        tx_date=signal.tx_date,
        disclosed_date=signal.disclosed_date,
        amount_range=signal.amount_range,
        source_url=signal.source_url,
    )


@router.get("/bootstrap", response_model=DashboardBootstrapOut)
def dashboard_bootstrap(response: Response, db: Session = Depends(get_db)):
    cached = _get_dashboard_cache()
    if cached is not None:
        response.headers["X-Odyssey-Cache"] = "hit"
        response.headers["Cache-Control"] = "private, max-age=15, stale-while-revalidate=60"
        return cached

    account = db.query(BrokerageAccount).order_by(BrokerageAccount.id).first()
    bots = db.query(Bot).order_by(Bot.id.desc()).limit(6).all()
    signals = db.query(Signal).order_by(Signal.id.desc()).limit(8).all()
    tickers = (
        db.query(Ticker)
        .order_by(desc(Ticker.market_cap).nullslast(), Ticker.symbol)
        .limit(8)
        .all()
    )
    quotes = cached_value(db, "quotes_all") or {}
    movers = cached_value(db, "major_movers") or {"gainers": [], "losers": []}

    stocks = [
        StockRow(
            symbol=t.symbol,
            name=t.name,
            sector=t.sector,
            industry=t.industry,
            logo_url=t.logo_url,
            market_cap=float(t.market_cap) if t.market_cap is not None else None,
            price=(quotes.get(t.symbol) or {}).get("price"),
            change=(quotes.get(t.symbol) or {}).get("change"),
            change_pct=(quotes.get(t.symbol) or {}).get("change_pct"),
        )
        for t in tickers
    ]
    response.headers["Cache-Control"] = "private, max-age=15, stale-while-revalidate=60"
    payload = DashboardBootstrapOut(
        account=_account_out(account),
        stocks=stocks,
        movers=MoversOut.model_validate(movers),
        bots=[_bot_out(bot) for bot in bots],
        signals=[_signal_out(signal) for signal in signals],
        featured_symbol=stocks[0].symbol if stocks else "",
    )
    _set_dashboard_cache(payload)
    response.headers["X-Odyssey-Cache"] = "miss"
    return payload
