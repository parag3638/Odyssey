from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import ActivityLog, Bot
from app.schemas import ActivityOut

router = APIRouter(prefix="/activity")


@router.get("", response_model=list[ActivityOut])
def list_activity(limit: int = 50, db: Session = Depends(get_db)):
    """Recent activity across all bots, newest first, enriched with bot name + symbol."""
    rows = (
        db.query(ActivityLog)
        .order_by(ActivityLog.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    bots = {b.id: b for b in db.query(Bot).all()}
    out: list[ActivityOut] = []
    for a in rows:
        bot = bots.get(a.bot_id) if a.bot_id else None
        symbol = None
        if bot is not None and isinstance(bot.config, dict):
            symbol = bot.config.get("symbol")
        out.append(
            ActivityOut(
                id=a.id,
                bot_id=a.bot_id,
                level=a.level,
                event=a.event,
                detail=a.detail or {},
                created_at=a.created_at.isoformat() if a.created_at else None,
                bot_name=bot.name if bot else None,
                symbol=symbol,
            )
        )
    return out
