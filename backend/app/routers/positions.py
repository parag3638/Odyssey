from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import BrokerageAccount
from app.brokers.base import BrokerError
from app.routers import orders as orders_router
from app.schemas import PositionOut, QuoteOut, AccountSummaryOut, AccountOut, PortfolioOverviewOut

router = APIRouter(prefix="/positions")


def _account_or_404(account_id: int, db: Session) -> BrokerageAccount:
    account = db.get(BrokerageAccount, account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    return account


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
    account_out = AccountOut(
        id=account.id,
        label=account.label,
        mode=account.mode,
        masked_secret="••••" + account.alpaca_key_id[-4:],
    )
    broker = orders_router.get_broker_for_account(account)
    db.rollback()

    positions = broker.get_positions()
    symbols = [p.symbol for p in positions]
    quotes = broker.get_quotes(symbols)
    try:
        cash = broker.get_cash()
    except Exception:
        cash = None
    return PortfolioOverviewOut(
        account=account_out,
        positions=[
            PositionOut(symbol=p.symbol, qty=p.qty, avg_entry_price=p.avg_entry_price)
            for p in positions
        ],
        quotes=[QuoteOut(symbol=q.symbol, price=q.price, prev_close=q.prev_close) for q in quotes],
        cash=cash,
    )
