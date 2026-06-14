from app.brokers.base import (
    Action, BuyOrder, SellOrder, OrderResult, Position, Quote, Clock, BrokerError,
)


class FakeBroker:
    def __init__(self, cash: float = 100_000.0, quotes: dict[str, float] | None = None, market_open: bool = True, prev_closes: dict[str, float] | None = None):
        self._cash = cash
        self._quotes = quotes or {}
        self._prev = prev_closes or {}
        self._positions: dict[str, Position] = {}
        self._counter = 0
        self._market_open = market_open

    def submit(self, action: Action) -> OrderResult:
        price = self._quotes.get(action.symbol)
        if price is None:
            raise BrokerError(f"no quote for {action.symbol}")
        self._counter += 1
        oid = f"fake-{self._counter}"
        if isinstance(action, BuyOrder):
            self._cash -= price * action.qty
            existing = self._positions.get(action.symbol)
            new_qty = (existing.qty if existing else 0) + action.qty
            self._positions[action.symbol] = Position(action.symbol, new_qty, price)
        elif isinstance(action, SellOrder):
            existing = self._positions.get(action.symbol)
            remaining = (existing.qty if existing else 0) - action.qty
            self._cash += price * action.qty
            if remaining <= 0:
                self._positions.pop(action.symbol, None)
            else:
                self._positions[action.symbol] = Position(
                    action.symbol, remaining, existing.avg_entry_price
                )
        return OrderResult(status="filled", filled_price=price, broker_order_id=oid)

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_quote(self, symbol: str) -> Quote:
        if symbol not in self._quotes:
            raise BrokerError(f"no quote for {symbol}")
        return Quote(symbol=symbol, price=self._quotes[symbol], prev_close=self._prev.get(symbol))

    def get_clock(self) -> Clock:
        return Clock(is_open=self._market_open)

    def set_quote(self, symbol: str, price: float) -> None:
        self._quotes[symbol] = price

    def get_cash(self) -> float:
        return self._cash
