from dataclasses import dataclass, field
from app.brokers.base import BuyOrder, SellOrder


@dataclass
class TrailingState:
    qty: float
    avg_entry_price: float | None
    stop_floor: float | None
    triggered_rungs: list = field(default_factory=list)


@dataclass
class TrailingDecision:
    buys: list = field(default_factory=list)
    sells: list = field(default_factory=list)
    new_stop_floor: float | None = None
    new_triggered_rungs: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def evaluate(config: dict, state: TrailingState, price: float) -> TrailingDecision:
    """Pure trailing-stop logic. No I/O. Returns intended buys/sells + updated state."""
    symbol = config["symbol"]
    stop_pct = config.get("stop_pct", 0.10)
    trail_pct = config.get("trail_pct", 0.05)
    d = TrailingDecision(new_triggered_rungs=list(state.triggered_rungs))

    # No position yet -> open it.
    if state.qty <= 0:
        d.buys.append(BuyOrder(symbol=symbol, qty=config["initial_shares"], reason="trailing: initial entry"))
        d.new_stop_floor = round(price * (1 - stop_pct), 4)
        d.notes.append(f"opened position; floor {d.new_stop_floor}")
        return d

    entry = state.avg_entry_price or price
    base_floor = entry * (1 - stop_pct)
    current_floor = state.stop_floor if state.stop_floor is not None else base_floor
    trail_floor = price * (1 - trail_pct)
    # floor only ever rises
    new_floor = max(current_floor, trail_floor)
    d.new_stop_floor = round(new_floor, 4)
    if new_floor > current_floor:
        d.notes.append(f"raised floor to {d.new_stop_floor}")

    # stop hit -> exit fully
    if price <= d.new_stop_floor:
        d.sells.append(SellOrder(symbol=symbol, qty=state.qty, reason="trailing: stop hit"))
        d.notes.append("stop hit; selling all")
        return d

    # ladder buys (each rung fires at most once)
    for rung in config.get("ladder", []):
        drop = rung["drop_pct"]
        if drop in d.new_triggered_rungs:
            continue
        if price <= entry * (1 - drop):
            d.buys.append(BuyOrder(symbol=symbol, qty=rung["add_shares"],
                                   reason=f"trailing: ladder -{int(drop*100)}%"))
            d.new_triggered_rungs.append(drop)
            d.notes.append(f"ladder buy {rung['add_shares']} @ -{int(drop*100)}%")
    return d
