from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Ticker, Watchlist
from app.routers import stocks as stocks_router
from app.schemas import StockRow, WatchlistAdd

router = APIRouter(prefix="/watchlist")


def _rows(db: Session) -> list[StockRow]:
    items = db.query(Watchlist).order_by(Watchlist.id.desc()).all()
    syms = [w.symbol for w in items]
    md = stocks_router.get_market_data(db)
    quotes = md.snapshots(syms) if md else {}
    out: list[StockRow] = []
    for w in items:
        t = db.get(Ticker, w.symbol)
        q = quotes.get(w.symbol) or {}
        out.append(
            StockRow(
                symbol=w.symbol,
                name=t.name if t else "",
                sector=t.sector if t else "",
                industry=t.industry if t else "",
                logo_url=t.logo_url if t else "",
                market_cap=float(t.market_cap) if (t and t.market_cap is not None) else None,
                price=q.get("price"),
                change=q.get("change"),
                change_pct=q.get("change_pct"),
            )
        )
    return out


@router.get("", response_model=list[StockRow])
def list_watchlist(db: Session = Depends(get_db)):
    return _rows(db)


@router.post("", response_model=list[StockRow])
def add_watchlist(body: WatchlistAdd, db: Session = Depends(get_db)):
    sym = body.symbol.upper().strip()
    if not sym:
        raise HTTPException(422, "symbol required")
    if db.query(Watchlist).filter(Watchlist.symbol == sym).first() is None:
        db.add(Watchlist(symbol=sym))
        db.commit()
    return _rows(db)


@router.delete("/{symbol}", response_model=list[StockRow])
def remove_watchlist(symbol: str, db: Session = Depends(get_db)):
    db.query(Watchlist).filter(Watchlist.symbol == symbol.upper()).delete()
    db.commit()
    return _rows(db)
