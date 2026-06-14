from app.strategies.copy_trade import evaluate_copy
from app.services.orders import place_order
from app.models import ActivityLog
from app.brokers.base import BrokerError
from app.risk import RiskRejection


def run_copy_trade_tick(db, broker, *, account_id, bot_id, config, recent_signals, copied_hashes, mode="paper"):
    """Execute one copy-trade tick. `recent_signals` = list of Signal-dicts for the followed
    politician (most recent first). `copied_hashes` = set of already-acted signal hashes."""
    # quotes for the symbols in play
    prices: dict = {}
    for s in recent_signals:
        sym = s["symbol"]
        if sym in prices:
            continue
        try:
            prices[sym] = broker.get_quote(sym).price
        except BrokerError:
            pass
    held = {p.symbol: p.qty for p in broker.get_positions()}

    decision = evaluate_copy(
        signals=recent_signals, held=held, copied_hashes=set(copied_hashes), prices=prices,
        per_trade_notional=float(config.get("per_trade_notional", 1000.0)),
        follow_buys=config.get("follow_buys", True), follow_sells=config.get("follow_sells", True),
    )

    placed = 0
    for action in [*decision.buys, *decision.sells]:
        try:
            place_order(db, broker, account_id=account_id, mode=mode, action=action)
            placed += 1
        except (RiskRejection, BrokerError) as e:
            db.add(ActivityLog(bot_id=bot_id, level="warn", event="copy_action_skipped",
                               detail={"symbol": action.symbol, "reason": str(e)}))

    for h in decision.new_copied_hashes:
        db.add(ActivityLog(bot_id=bot_id, level="info", event="copied", detail={"hash": h}))

    db.add(ActivityLog(bot_id=bot_id, level="info", event="tick",
                       detail={"actions": placed, "notes": decision.notes}))
    db.commit()
    return {"actions": placed, "notes": decision.notes}
