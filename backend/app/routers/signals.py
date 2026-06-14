from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Signal
from app.schemas import SignalOut

router = APIRouter(prefix="/signals")


# Overridable seam for tests (avoids hitting the network in the suite).
def _sync(db) -> int:
    from app.services.signals_sync import sync_from_capitol_trades
    return sync_from_capitol_trades(db, limit=60)


@router.post("/sync")
def sync_signals_now(db: Session = Depends(get_db)):
    """Pull the latest congressional trades from Capitol Trades right now and store any new
    ones. Returns how many were added. Network/parse failures surface as a clean 502."""
    try:
        added = _sync(db)
    except Exception as e:  # network down, page shape changed, etc.
        raise HTTPException(502, f"capitol trades fetch failed: {e}")
    return {"added": added}


@router.get("", response_model=list[SignalOut])
def list_signals(politician: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Signal)
    if politician:
        q = q.filter(Signal.politician == politician)
    rows = q.order_by(Signal.id.desc()).limit(limit).all()
    return [SignalOut(id=r.id, politician=r.politician, symbol=r.symbol, tx_type=r.tx_type,
                      tx_date=r.tx_date, disclosed_date=r.disclosed_date,
                      amount_range=r.amount_range, source_url=r.source_url) for r in rows]
