from app.brokers.base import Action, Broker
from app.models import Order, ActivityLog
from app.risk import validate_order, RiskRejection

DEFAULT_MAX_POSITION_NOTIONAL = 10_000.0


def place_order(
    db,
    broker: Broker,
    *,
    account_id: int,
    mode: str,
    action: Action,
    max_position_notional: float = DEFAULT_MAX_POSITION_NOTIONAL,
) -> Order:
    price = broker.get_quote(action.symbol).price
    cash = broker.get_cash()
    try:
        validate_order(action, mode=mode, cash=cash, price=price,
                       max_position_notional=max_position_notional)
    except RiskRejection as e:
        db.add(ActivityLog(level="warn", event="order_rejected",
                           detail={"symbol": action.symbol, "qty": float(action.qty),
                                   "reason": str(e)}))
        db.commit()
        raise

    result = broker.submit(action)
    order = Order(
        account_id=account_id, symbol=action.symbol, side=action.side,
        qty=action.qty, status=result.status,
        alpaca_order_id=result.broker_order_id, reason=action.reason,
    )
    db.add(order)
    db.add(ActivityLog(level="info", event="order_submitted",
                       detail={"symbol": action.symbol, "qty": float(action.qty),
                               "side": action.side, "status": result.status}))
    db.commit()
    db.refresh(order)
    return order
