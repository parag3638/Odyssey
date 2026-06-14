from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import BrokerageAccount
from app.brokers.base import BuyOrder, SellOrder, BrokerError
from app.brokers.alpaca import AlpacaBroker
from app.risk import RiskRejection
from app.services.orders import place_order
from app.schemas import OrderCreate, OrderOut

router = APIRouter(prefix="/orders")


def build_broker(account: BrokerageAccount):
    secret = decrypt_secret(account.alpaca_secret, get_settings().fernet_key)
    return AlpacaBroker(key_id=account.alpaca_key_id, secret=secret, paper=True)


def get_broker_for_account(account: BrokerageAccount):
    return build_broker(account)


@router.post("", response_model=OrderOut)
def create_order(body: OrderCreate, db: Session = Depends(get_db)):
    account = db.get(BrokerageAccount, body.account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    action_cls = BuyOrder if body.side == "buy" else SellOrder
    action = action_cls(symbol=body.symbol.upper(), qty=body.qty, reason="manual")
    broker = get_broker_for_account(account)
    try:
        order = place_order(db, broker, account_id=account.id, mode=account.mode, action=action)
    except RiskRejection as e:
        raise HTTPException(422, f"risk rejection: {e}")
    except BrokerError as e:
        raise HTTPException(502, f"broker error: {e}")
    return OrderOut(id=order.id, symbol=order.symbol, side=order.side,
                    qty=float(order.qty), status=order.status, reason=order.reason)
