from datetime import datetime, timezone
from sqlalchemy import String, Integer, Numeric, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrokerageAccount(Base):
    __tablename__ = "brokerage_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(10), default="paper")
    alpaca_key_id: Mapped[str] = mapped_column(String(200))
    alpaca_secret: Mapped[str] = mapped_column(String(500))
    endpoint: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("brokerage_accounts.id"))
    symbol: Mapped[str] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Numeric(18, 4))
    status: Mapped[str] = mapped_column(String(12), default="pending")
    alpaca_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(200), default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str] = mapped_column(String(8), default="info")
    event: Mapped[str] = mapped_column(String(120))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Bot(Base):
    __tablename__ = "bots"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[int] = mapped_column(ForeignKey("brokerage_accounts.id"))
    strategy_type: Mapped[str] = mapped_column(String(20))  # trailing_stop | copy_trade
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | paused | stopped
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule_cadence_sec: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"))
    symbol: Mapped[str] = mapped_column(String(10))
    qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    avg_entry_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    stop_floor: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    triggered_rungs: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    politician: Mapped[str] = mapped_column(String(120))
    symbol: Mapped[str] = mapped_column(String(10))
    tx_type: Mapped[str] = mapped_column(String(4))  # buy | sell
    tx_date: Mapped[str] = mapped_column(String(10))
    disclosed_date: Mapped[str] = mapped_column(String(10))
    amount_range: Mapped[str] = mapped_column(String(40), default="")
    source_url: Mapped[str] = mapped_column(String(300), default="")
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Ticker(Base):
    """The curated stock universe powering /stocks + the industry filter.

    Seeded from a static curated list (symbol/name/sector/industry) so it works
    without any external API; Finnhub enriches logo + market_cap when available.
    """
    __tablename__ = "tickers"
    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    sector: Mapped[str] = mapped_column(String(60), default="", index=True)
    industry: Mapped[str] = mapped_column(String(80), default="", index=True)
    exchange: Mapped[str] = mapped_column(String(20), default="")
    logo_url: Mapped[str] = mapped_column(String(300), default="")
    market_cap: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Watchlist(Base):
    __tablename__ = "watchlist"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(12), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MarketCache(Base):
    """Generic TTL cache for external market data (Finnhub/Alpaca) so pages are
    fast and we stay within rate limits."""
    __tablename__ = "market_cache"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
