from app.models import Signal


def upsert_signals(db, signals: list[dict]) -> int:
    """Insert signals whose hash is not already stored. Returns count added."""
    existing = {row[0] for row in db.query(Signal).with_entities(Signal.hash).all()}
    added = 0
    for s in signals:
        if s["hash"] in existing:
            continue
        db.add(Signal(
            politician=s["politician"], symbol=s["symbol"], tx_type=s["tx_type"],
            tx_date=s["tx_date"], disclosed_date=s["disclosed_date"],
            amount_range=s.get("amount_range", ""), source_url=s.get("source_url", ""),
            hash=s["hash"],
        ))
        existing.add(s["hash"])
        added += 1
    if added:
        db.commit()
    return added


def sync_from_capitol_trades(db, limit: int = 50) -> int:
    """Live: fetch from Capitol Trades then upsert. Returns count added. Caller handles errors."""
    from app.data.capitol_trades import fetch_capitol_trades
    return upsert_signals(db, fetch_capitol_trades(limit=limit))
