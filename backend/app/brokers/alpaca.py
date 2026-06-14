from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from app.brokers.base import (
    Action, BuyOrder, OrderResult, Position, Quote, Clock, BrokerError,
)

_STATUS_MAP = {
    "filled": "filled",
    "accepted": "pending",
    "new": "pending",
    "pending_new": "pending",
    "rejected": "rejected",
    "canceled": "rejected",
}


class AlpacaBroker:
    def __init__(self, client=None, data_client=None, key_id: str = "", secret: str = "", paper: bool = True):
        self._client = client or TradingClient(key_id, secret, paper=paper)
        self._data = data_client or (StockHistoricalDataClient(key_id, secret) if key_id else None)

    def submit(self, action: Action) -> OrderResult:
        side = OrderSide.BUY if isinstance(action, BuyOrder) else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=action.symbol, qty=action.qty, side=side,
            time_in_force=TimeInForce.DAY,
        )
        try:
            o = self._client.submit_order(order_data=req)
        except Exception as e:
            raise BrokerError(str(e)) from e
        filled = float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else None
        return OrderResult(
            status=_STATUS_MAP.get(str(o.status).split(".")[-1].lower(), "pending"),
            filled_price=filled,
            broker_order_id=str(o.id),
        )

    def get_positions(self) -> list[Position]:
        return [
            Position(p.symbol, float(p.qty), float(p.avg_entry_price))
            for p in self._client.get_all_positions()
        ]

    def get_quote(self, symbol: str) -> Quote:
        if self._data is None:
            raise BrokerError("no market-data client configured")
        # Prefer a snapshot: latest trade price + previous daily close (→ today's return).
        try:
            from alpaca.data.requests import StockSnapshotRequest

            snap = self._data.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=symbol))
            s = snap[symbol]
            price = float(s.latest_trade.price)
            prev_bar = getattr(s, "previous_daily_bar", None)
            prev = float(prev_bar.close) if prev_bar is not None else None
            return Quote(symbol=symbol, price=price, prev_close=prev)
        except Exception:
            pass
        # Fallback: latest trade only.
        try:
            resp = self._data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
            trade = resp[symbol]
            return Quote(symbol=symbol, price=float(trade.price))
        except Exception as e:
            raise BrokerError(f"quote failed for {symbol}: {e}") from e

    def get_clock(self) -> Clock:
        try:
            c = self._client.get_clock()
            return Clock(is_open=bool(c.is_open))
        except Exception as e:
            raise BrokerError(str(e)) from e

    def get_cash(self) -> float:
        return float(self._client.get_account().cash)
