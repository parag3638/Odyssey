from dataclasses import dataclass, field
from app.brokers.base import BuyOrder, SellOrder


@dataclass
class CopyDecision:
    buys: list = field(default_factory=list)
    sells: list = field(default_factory=list)
    new_copied_hashes: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def evaluate_copy(*, signals: list[dict], held: dict, copied_hashes: set,
                  prices: dict, per_trade_notional: float,
                  follow_buys: bool = True, follow_sells: bool = True) -> CopyDecision:
    """Pure copy-trade logic. `held` = {symbol: qty} currently owned. `prices` = {symbol: price}.
    A signal is marked copied only when we actually act on it (so price-less buys retry later)."""
    d = CopyDecision()
    for s in signals:
        h = s["hash"]
        if h in copied_hashes or h in d.new_copied_hashes:
            continue
        sym = s["symbol"]
        if s["tx_type"] == "buy" and follow_buys:
            price = prices.get(sym)
            if not price or price <= 0:
                d.notes.append(f"defer buy {sym}: no price")
                continue
            qty = max(1, int(per_trade_notional // price))
            d.buys.append(BuyOrder(symbol=sym, qty=qty, reason=f"copy: {s.get('politician','')} buy"))
            d.new_copied_hashes.append(h)
            d.notes.append(f"copy buy {qty} {sym}")
        elif s["tx_type"] == "sell" and follow_sells:
            held_qty = held.get(sym, 0)
            if held_qty and held_qty > 0:
                d.sells.append(SellOrder(symbol=sym, qty=held_qty, reason=f"copy: {s.get('politician','')} sell"))
                d.new_copied_hashes.append(h)
                d.notes.append(f"copy sell {held_qty} {sym}")
            else:
                # nothing to sell; mark copied so we don't reconsider a sell of an unowned name
                d.new_copied_hashes.append(h)
    return d
