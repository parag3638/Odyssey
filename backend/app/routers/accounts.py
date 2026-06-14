from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import get_settings
from app.crypto import encrypt_secret, mask_secret
from app.models import BrokerageAccount
from app.schemas import AccountCreate, AccountOut

router = APIRouter(prefix="/accounts")


@router.post("", response_model=AccountOut)
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    key = get_settings().fernet_key
    acct = BrokerageAccount(
        label=body.label, mode="paper",
        alpaca_key_id=body.alpaca_key_id,
        alpaca_secret=encrypt_secret(body.alpaca_secret, key),
        endpoint=body.endpoint,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return AccountOut(id=acct.id, label=acct.label, mode=acct.mode,
                      masked_secret=mask_secret(body.alpaca_secret))


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    out = []
    for a in db.query(BrokerageAccount).all():
        out.append(AccountOut(id=a.id, label=a.label, mode=a.mode,
                              masked_secret="••••" + a.alpaca_key_id[-4:]))
    return out
