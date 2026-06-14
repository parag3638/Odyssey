from app.strategies.trailing_stop import evaluate, TrailingState
from app.services.orders import place_order
from app.models import ActivityLog
from app.brokers.base import BrokerError
from app.risk import RiskRejection


def run_trailing_stop_tick(db, broker, *, account_id, bot_id, config, position, mode="paper"):
    """Execute one trailing-stop tick. `position` is a Position ORM row (or stub) with
    qty/avg_entry_price/stop_floor/triggered_rungs attributes. Mutates it in place + commits."""
    symbol = config["symbol"]
    quote = broker.get_quote(symbol)
    state = TrailingState(
        qty=float(position.qty or 0),
        avg_entry_price=float(position.avg_entry_price) if position.avg_entry_price is not None else None,
        stop_floor=float(position.stop_floor) if position.stop_floor is not None else None,
        triggered_rungs=list(position.triggered_rungs or []),
    )
    decision = evaluate(config, state, quote.price)

    placed = 0
    for action in [*decision.buys, *decision.sells]:
        try:
            place_order(db, broker, account_id=account_id, mode=mode, action=action)
            placed += 1
        except (RiskRejection, BrokerError) as e:
            db.add(ActivityLog(bot_id=bot_id, level="warn", event="tick_action_skipped",
                               detail={"symbol": symbol, "reason": str(e)}))

    # reconcile position qty/avg from the broker (source of truth)
    by_symbol = {p.symbol: p for p in broker.get_positions()}
    bp = by_symbol.get(symbol)
    position.qty = bp.qty if bp else 0
    position.avg_entry_price = bp.avg_entry_price if bp else None
    if decision.new_stop_floor is not None and (bp is not None):
        position.stop_floor = decision.new_stop_floor
    position.triggered_rungs = decision.new_triggered_rungs

    db.add(ActivityLog(bot_id=bot_id, level="info", event="tick",
                       detail={"symbol": symbol, "price": quote.price,
                               "stop_floor": decision.new_stop_floor,
                               "notes": decision.notes, "actions": placed}))
    db.commit()
    return {"price": quote.price, "actions": placed, "stop_floor": decision.new_stop_floor,
            "notes": decision.notes}
