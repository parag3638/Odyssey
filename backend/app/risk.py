from app.brokers.base import Action, BuyOrder


class RiskRejection(Exception):
    pass


def validate_order(
    action: Action,
    *,
    mode: str,
    cash: float,
    price: float,
    max_position_notional: float,
) -> None:
    """Raise RiskRejection if the order violates a guardrail. Returns None if OK."""
    if mode != "paper":
        raise RiskRejection("live trading disabled in v1")
    if action.qty <= 0:
        raise RiskRejection("quantity must be positive")
    notional = price * action.qty
    if notional > max_position_notional:
        raise RiskRejection(
            f"order notional {notional:.2f} exceeds max position {max_position_notional:.2f}"
        )
    if isinstance(action, BuyOrder) and notional > cash:
        raise RiskRejection(
            f"insufficient buying power: need {notional:.2f}, have {cash:.2f}"
        )
