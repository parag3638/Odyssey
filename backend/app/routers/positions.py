from threading import Lock
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.brokers.base import BrokerError
from app.db import get_db
from app.models import BrokerageAccount
from app.routers import orders as orders_router
from app.schemas import (
    AccountOut,
    AccountSummaryOut,
    PortfolioHealthOut,
    PortfolioOverviewOut,
    PositionOut,
    PositionsBootstrapOut,
    QuoteOut,
)
from app.services.finnhub import cached_value

router = APIRouter(prefix="/positions")
_overview_lock = Lock()
_overview_cache: dict[int, tuple[float, PortfolioOverviewOut]] = {}


def clear_positions_cache(account_id: int | None = None) -> None:
    with _overview_lock:
        if account_id is None:
            _overview_cache.clear()
        else:
            _overview_cache.pop(account_id, None)


def _account_or_404(account_id: int, db: Session) -> BrokerageAccount:
    account = db.get(BrokerageAccount, account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    return account


def _overview_for(account: BrokerageAccount, db: Session) -> tuple[PortfolioOverviewOut, bool]:
    with _overview_lock:
        cached = _overview_cache.get(account.id)
        if cached is not None and cached[0] > monotonic():
            return cached[1], True

    account_out = AccountOut(
        id=account.id,
        label=account.label,
        mode=account.mode,
        masked_secret="••••" + account.alpaca_key_id[-4:],
    )
    broker = orders_router.get_broker_for_account(account)
    db.rollback()
    positions = broker.get_positions()
    symbols = [position.symbol for position in positions]
    quotes = broker.get_quotes(symbols)
    try:
        cash = broker.get_cash()
    except Exception:
        cash = None
    payload = PortfolioOverviewOut(
        account=account_out,
        positions=[
            PositionOut(
                symbol=position.symbol,
                qty=position.qty,
                avg_entry_price=position.avg_entry_price,
            )
            for position in positions
        ],
        quotes=[
            QuoteOut(symbol=quote.symbol, price=quote.price, prev_close=quote.prev_close)
            for quote in quotes
        ],
        cash=cash,
    )
    with _overview_lock:
        _overview_cache[account.id] = (monotonic() + 15, payload)
    return payload, False


@router.get("/bootstrap", response_model=PositionsBootstrapOut)
def positions_bootstrap(response: Response, db: Session = Depends(get_db)):
    account = db.query(BrokerageAccount).order_by(BrokerageAccount.id).first()
    if account is None:
        return PositionsBootstrapOut()
    overview, cache_hit = _overview_for(account, db)
    quote_map = {quote.symbol: quote for quote in overview.quotes}
    holdings = [
        {
            "symbol": position.symbol,
            "qty": position.qty,
            "price": (
                quote_map[position.symbol].price
                if position.symbol in quote_map
                else position.avg_entry_price
            ),
        }
        for position in overview.positions
    ]
    from app.routers.ai import portfolio_health_for_holdings

    health, health_key, health_pending = portfolio_health_for_holdings(db, holdings)
    response.headers["Cache-Control"] = "private, max-age=10, stale-while-revalidate=30"
    response.headers["X-Odyssey-Cache"] = "hit" if cache_hit else "miss"
    return PositionsBootstrapOut(
        **overview.model_dump(),
        portfolio_health=health,
        health_key=health_key,
        health_pending=health_pending,
    )


@router.get("/health", response_model=PortfolioHealthOut)
def positions_health(key: str, db: Session = Depends(get_db)):
    if not key.startswith("ai_health:") or len(key) > 64:
        raise HTTPException(400, "invalid health key")
    data = cached_value(db, key, 6 * 3600)
    return PortfolioHealthOut(**data) if isinstance(data, dict) else PortfolioHealthOut(available=False)


@router.get("/{account_id}", response_model=list[PositionOut])
def list_positions(account_id: int, db: Session = Depends(get_db)):
    account = _account_or_404(account_id, db)
    broker = orders_router.get_broker_for_account(account)
    return [PositionOut(symbol=p.symbol, qty=p.qty, avg_entry_price=p.avg_entry_price)
            for p in broker.get_positions()]


@router.get("/{account_id}/quotes", response_model=list[QuoteOut])
def list_quotes(account_id: int, symbols: str | None = None, db: Session = Depends(get_db)):
    """Live quotes (price + previous close) for the given symbols, or, when no
    symbols are supplied, for the account's current holdings. Best-effort:
    symbols whose quote can't be fetched are simply omitted."""
    account = _account_or_404(account_id, db)
    broker = orders_router.get_broker_for_account(account)
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        syms = [p.symbol for p in broker.get_positions()]
    out: list[QuoteOut] = []
    for s in syms:
        try:
            q = broker.get_quote(s)
        except BrokerError:
            continue
        out.append(QuoteOut(symbol=s, price=q.price, prev_close=q.prev_close))
    return out


@router.get("/{account_id}/summary", response_model=AccountSummaryOut)
def account_summary(account_id: int, db: Session = Depends(get_db)):
    """Account-level cash, used with holdings to compute total portfolio value."""
    account = _account_or_404(account_id, db)
    broker = orders_router.get_broker_for_account(account)
    try:
        cash = broker.get_cash()
    except Exception:
        cash = None
    return AccountSummaryOut(cash=cash)


@router.get("/{account_id}/overview", response_model=PortfolioOverviewOut)
def portfolio_overview(account_id: int, db: Session = Depends(get_db)):
    account = _account_or_404(account_id, db)
    overview, _ = _overview_for(account, db)
    return overview
