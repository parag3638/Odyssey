from dataclasses import dataclass
from typing import Protocol, Literal


@dataclass(frozen=True)
class BuyOrder:
    symbol: str
    qty: float
    reason: str = ""
    side: Literal["buy"] = "buy"


@dataclass(frozen=True)
class SellOrder:
    symbol: str
    qty: float
    reason: str = ""
    side: Literal["sell"] = "sell"


Action = BuyOrder | SellOrder


@dataclass(frozen=True)
class OrderResult:
    status: str
    filled_price: float | None = None
    broker_order_id: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_entry_price: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    prev_close: float | None = None


@dataclass(frozen=True)
class AdjustStop:
    symbol: str
    new_floor: float
    reason: str = ""


@dataclass(frozen=True)
class Clock:
    is_open: bool


class BrokerError(Exception):
    pass


class Broker(Protocol):
    def submit(self, action: Action) -> OrderResult: ...
    def get_positions(self) -> list[Position]: ...
    def get_quote(self, symbol: str) -> Quote: ...
    def get_clock(self) -> Clock: ...
    def get_cash(self) -> float: ...
