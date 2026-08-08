from pydantic import BaseModel, Field


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


class Citation(BaseModel):
    """A source an AI summary is grounded in — rendered as a link chip."""
    id: str
    label: str
    url: str
    kind: str = "news"


class AiResponse(BaseModel):
    """Envelope for every non-chat AI feature. `available=False` means the LLM is
    unconfigured or produced nothing, and the UI hides the AI affordance."""
    available: bool = True
    text: str | None = None
    citations: list[Citation] = []
    disclaimer: str = "Information only — not personalized investment advice."
    model: str | None = None


class BullBearOut(BaseModel):
    """Cross-source bull-vs-bear synthesis for one stock."""
    available: bool = True
    bull: list[str] = []
    bear: list[str] = []
    crux: str | None = None
    citations: list[Citation] = []
    disclaimer: str = "Information only — not personalized investment advice."
    model: str | None = None


class ConcentrationOut(BaseModel):
    """One named concentration/overlap the portfolio observer surfaced."""
    label: str
    weight_pct: float


class PortfolioHealthOut(BaseModel):
    available: bool = True
    text: str | None = None
    concentrations: list[ConcentrationOut] = []
    disclaimer: str = "Information only — not personalized investment advice."
    model: str | None = None


class RiskExplainIn(BaseModel):
    reason: str
    symbol: str = ""
    qty: float = 0
    side: str = "buy"


class ScreenerFilter(BaseModel):
    field: str
    op: str  # < <= > >= = contains
    value: str


class ScreenerParseOut(BaseModel):
    available: bool = True
    filters: list[ScreenerFilter] = []
    sort_field: str | None = None
    sort_dir: str = "desc"
    note: str | None = None
    model: str | None = None


class ScreenerParseIn(BaseModel):
    query: str


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatContext(BaseModel):
    symbol: str | None = None  # the stock the user is viewing, if any


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = []
    context: ChatContext = Field(default_factory=ChatContext)
