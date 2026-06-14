from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import Bot, Position, ActivityLog, BrokerageAccount
from app.brokers.alpaca import AlpacaBroker
from app.services.runner import run_trailing_stop_tick
from app.schemas import BotCreate, BotOut, BotDetail

router = APIRouter(prefix="/bots")


def build_bot_broker(account: BrokerageAccount):
    secret = decrypt_secret(account.alpaca_secret, get_settings().fernet_key)
    return AlpacaBroker(key_id=account.alpaca_key_id, secret=secret, paper=True)


# Overridable seam for tests:
def get_broker_for_account(account: BrokerageAccount):
    return build_bot_broker(account)


def _bot_out(bot: Bot) -> BotOut:
    return BotOut(id=bot.id, name=bot.name, strategy_type=bot.strategy_type,
                  status=bot.status, config=bot.config, schedule_cadence_sec=bot.schedule_cadence_sec)


@router.post("", response_model=BotOut)
def create_bot(body: BotCreate, db: Session = Depends(get_db)):
    if db.get(BrokerageAccount, body.account_id) is None:
        raise HTTPException(404, "account not found")
    if body.strategy_type == "copy_trade":
        config = {"politician": body.politician or "auto",
                  "per_trade_notional": body.per_trade_notional,
                  "follow_buys": body.follow_buys, "follow_sells": body.follow_sells}
    else:
        if not body.symbol:
            raise HTTPException(422, "symbol required for trailing_stop")
        config = {"symbol": body.symbol.upper(), "initial_shares": body.initial_shares,
                  "stop_pct": body.stop_pct, "trail_pct": body.trail_pct, "ladder": body.ladder}
    bot = Bot(name=body.name, account_id=body.account_id, strategy_type=body.strategy_type,
              status="active", config=config, schedule_cadence_sec=body.cadence_sec)
    db.add(bot); db.commit(); db.refresh(bot)
    return _bot_out(bot)


@router.get("", response_model=list[BotOut])
def list_bots(db: Session = Depends(get_db)):
    return [_bot_out(b) for b in db.query(Bot).all()]


@router.get("/{bot_id}", response_model=BotDetail)
def get_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404, "bot not found")
    pos = db.query(Position).filter(Position.bot_id == bot_id).first()
    acts = (db.query(ActivityLog).filter(ActivityLog.bot_id == bot_id)
            .order_by(ActivityLog.id.desc()).limit(10).all())
    pos_dict = None
    if pos is not None:
        pos_dict = {"symbol": pos.symbol, "qty": float(pos.qty or 0),
                    "avg_entry_price": float(pos.avg_entry_price) if pos.avg_entry_price is not None else None,
                    "stop_floor": float(pos.stop_floor) if pos.stop_floor is not None else None,
                    "triggered_rungs": pos.triggered_rungs or []}
    base = _bot_out(bot)
    return BotDetail(**base.model_dump(), position=pos_dict,
                     recent_activity=[{"event": a.event, "level": a.level, "detail": a.detail} for a in acts])


class _StatusPatch(BaseModel):
    status: str


@router.patch("/{bot_id}", response_model=BotOut)
def patch_bot(bot_id: int, body: _StatusPatch, db: Session = Depends(get_db)):
    bot = db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404, "bot not found")
    if body.status not in ("active", "paused", "stopped"):
        raise HTTPException(422, "invalid status")
    bot.status = body.status
    db.commit()
    db.refresh(bot)
    return _bot_out(bot)


@router.post("/{bot_id}/run")
def run_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404, "bot not found")
    account = db.get(BrokerageAccount, bot.account_id)
    broker = get_broker_for_account(account)
    if bot.strategy_type == "copy_trade":
        from app.services.copy_runner import run_copy_trade_tick
        from app.services.scheduler import copied_hashes_for_bot
        from app.models import Signal
        pol = bot.config.get("politician", "")
        q = db.query(Signal)
        if pol and pol != "auto":
            q = q.filter(Signal.politician == pol)
        recent = [{"hash": s.hash, "symbol": s.symbol, "tx_type": s.tx_type, "politician": s.politician}
                  for s in q.order_by(Signal.id.desc()).limit(50).all()]
        return run_copy_trade_tick(db, broker, account_id=account.id, bot_id=bot_id,
                                   config=bot.config, recent_signals=recent,
                                   copied_hashes=copied_hashes_for_bot(db, bot_id=bot_id),
                                   mode=account.mode)
    from app.services.runner import run_trailing_stop_tick
    pos = db.query(Position).filter(Position.bot_id == bot_id).first()
    if pos is None:
        pos = Position(bot_id=bot_id, symbol=bot.config["symbol"], qty=0, triggered_rungs=[])
        db.add(pos); db.commit(); db.refresh(pos)
    return run_trailing_stop_tick(db, broker, account_id=account.id, bot_id=bot_id,
                                  config=bot.config, position=pos, mode=account.mode)
