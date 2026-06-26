from pydantic import BaseModel


class AccountCreate(BaseModel):
    label: str
    alpaca_key_id: str
    alpaca_secret: str
    endpoint: str = "https://paper-api.alpaca.markets"


class AccountOut(BaseModel):
    id: int
    label: str
    mode: str
    masked_secret: str


class OrderCreate(BaseModel):
    account_id: int
    symbol: str
    qty: float
    side: str = "buy"


class OrderOut(BaseModel):
    id: int
    symbol: str
    side: str
    qty: float
    status: str
    reason: str


class PositionOut(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float


class QuoteOut(BaseModel):
    symbol: str
    price: float
    prev_close: float | None = None


class AccountSummaryOut(BaseModel):
    cash: float | None = None


class BotCreate(BaseModel):
    name: str
    account_id: int
    strategy_type: str = "trailing_stop"  # trailing_stop | copy_trade
    # trailing_stop fields
    symbol: str | None = None
    initial_shares: float = 10
    stop_pct: float = 0.10
    trail_pct: float = 0.05
    ladder: list[dict] = []
    # copy_trade fields
    politician: str | None = None
    per_trade_notional: float = 1000.0
    follow_buys: bool = True
    follow_sells: bool = True
    cadence_sec: int = 300


class BotOut(BaseModel):
    id: int
    name: str
    strategy_type: str
    status: str
    config: dict
    schedule_cadence_sec: int


class BotDetail(BotOut):
    position: dict | None = None
    recent_activity: list[dict] = []


class SignalOut(BaseModel):
    id: int
    politician: str
    symbol: str
    tx_type: str
    tx_date: str
    disclosed_date: str
    amount_range: str
    source_url: str


class ActivityOut(BaseModel):
    id: int
    bot_id: int | None = None
    level: str
    event: str
    detail: dict = {}
    created_at: str | None = None
    bot_name: str | None = None
    symbol: str | None = None


class StockRow(BaseModel):
    symbol: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    logo_url: str = ""
    market_cap: float | None = None
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None


class StockDetailOut(StockRow):
    exchange: str = ""
    prev_close: float | None = None
    fundamentals: dict | None = None


class StockMetrics(BaseModel):
    """Per-symbol fundamentals for the stock-finder table (from Finnhub).
    Every field is optional — anything Finnhub doesn't supply renders as "—"."""
    pe: float | None = None
    eps: float | None = None
    revenue: float | None = None  # absolute TTM revenue in USD
    revYoY: float | None = None  # YoY revenue growth, as a percent (e.g. 12.4)
    evSales: float | None = None
    marketCap: float | None = None  # absolute market cap in USD
    earnings: str | None = None  # next-earnings status, e.g. "Pending"


class IndustryOut(BaseModel):
    industry: str
    sector: str = ""
    count: int
