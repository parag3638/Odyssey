from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Signal, Ticker
from app.schemas import IndustryOut, SignalOut, StockDetailOut, StockMetrics, StockRow
from app.services.finnhub import cached, get_finnhub as _get_finnhub
from app.services.market_data import market_data_for

router = APIRouter(prefix="/stocks")

# Cap symbols enriched per request so one page load stays within Finnhub's
# free-tier rate limit (60 req/min); metrics are then cached 6h per symbol.
_METRICS_PER_REQUEST = 60


# Overridable seams for tests (mirror orders_router.get_broker_for_account).
def get_market_data(db: Session):
    return market_data_for(db)


def get_finnhub():
    return _get_finnhub()


def _row(t: Ticker, q: dict | None) -> StockRow:
    q = q or {}
    return StockRow(
        symbol=t.symbol,
        name=t.name,
        sector=t.sector,
        industry=t.industry,
        logo_url=t.logo_url,
        market_cap=float(t.market_cap) if t.market_cap is not None else None,
        price=q.get("price"),
        change=q.get("change"),
        change_pct=q.get("change_pct"),
    )


@router.get("", response_model=list[StockRow])
def list_stocks(
    industry: str | None = None,
    sector: str | None = None,
    q: str | None = None,
    sort: str = "market_cap",
    limit: int = 300,
    db: Session = Depends(get_db),
):
    query = db.query(Ticker)
    if industry:
        query = query.filter(Ticker.industry == industry)
    if sector:
        query = query.filter(Ticker.sector == sector)
    if q:
        like = f"%{q.upper()}%"
        query = query.filter(
            func.upper(Ticker.symbol).like(like) | func.upper(Ticker.name).like(like)
        )
    tickers = query.all()
    md = get_market_data(db)
    quotes = md.snapshots([t.symbol for t in tickers]) if md else {}
    rows = [_row(t, quotes.get(t.symbol)) for t in tickers]
    if sort == "change_pct":
        rows.sort(key=lambda r: r.change_pct if r.change_pct is not None else -1e18, reverse=True)
    elif sort == "symbol":
        rows.sort(key=lambda r: r.symbol)
    elif sort == "price":
        rows.sort(key=lambda r: r.price if r.price is not None else -1e18, reverse=True)
    else:  # market_cap
        rows.sort(key=lambda r: (r.market_cap or -1.0, r.symbol), reverse=True)
    return rows[:limit]


def _num(m: dict, *keys) -> float | None:
    """First numeric value among `keys`, else None."""
    for k in keys:
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _map_metrics(m: dict | None) -> StockMetrics:
    """Map a Finnhub /stock/metric 'metric' dict to our StockMetrics shape.
    Defensive: tries several field names and leaves anything unknown as None."""
    m = m or {}
    pe = _num(m, "peTTM", "peBasicExclExtraTTM", "peNormalizedAnnual")
    eps = _num(m, "epsTTM", "epsBasicExclExtraItemsTTM", "epsNormalizedAnnual")
    rev_yoy = _num(m, "revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy")
    ps = _num(m, "psTTM", "psAnnual")
    mc_millions = _num(m, "marketCapitalization")  # Finnhub reports $M
    market_cap = mc_millions * 1e6 if mc_millions is not None else None
    # Absolute revenue ($) = marketCap / (price-to-sales). Finnhub's basic
    # financials expose P/S but not absolute TTM revenue directly.
    revenue = (market_cap / ps) if (market_cap and ps) else None
    # EV/Sales: Finnhub exposes true EV/Revenue TTM directly; fall back to P/S
    # (close for low-net-debt large caps) only if it's ever missing.
    ev_sales = _num(m, "evRevenueTTM", "evToSales")
    if ev_sales is None:
        ev_sales = ps
    return StockMetrics(
        pe=pe, eps=eps, revenue=revenue, revYoY=rev_yoy,
        evSales=ev_sales, marketCap=market_cap,
    )


def _upcoming_earnings_symbols(cal: dict | None) -> set[str]:
    rows = (cal or {}).get("earningsCalendar") or []
    return {str(r.get("symbol", "")).upper() for r in rows if r.get("symbol")}


@router.get("/metrics")
def stock_metrics(symbols: str = "", db: Session = Depends(get_db)):
    """Per-symbol fundamentals for the stock-finder table. Enriches only the
    requested (current-page) symbols; each is cached 6h, the earnings calendar
    is one shared cached call. Degrades to nulls without a Finnhub key."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    syms = syms[:_METRICS_PER_REQUEST]
    fh = get_finnhub()
    cal = cached(db, "earnings_cal", 12 * 3600, lambda: fh.earnings_calendar())
    upcoming = _upcoming_earnings_symbols(cal if isinstance(cal, dict) else None)
    out: dict[str, dict] = {}
    for sym in syms:
        m = cached(db, f"metrics:{sym}", 6 * 3600, lambda s=sym: fh.metrics(s))
        d = _map_metrics(m if isinstance(m, dict) else None).model_dump()
        d["earnings"] = "Pending" if sym in upcoming else None
        out[sym] = d
    return out


@router.get("/industries", response_model=list[IndustryOut])
def industries(db: Session = Depends(get_db)):
    rows = (
        db.query(Ticker.industry, Ticker.sector, func.count(Ticker.symbol))
        .group_by(Ticker.industry, Ticker.sector)
        .all()
    )
    out = [IndustryOut(industry=i, sector=s or "", count=c) for i, s, c in rows if i]
    out.sort(key=lambda x: (-x.count, x.industry))
    return out


@router.get("/movers")
def movers(db: Session = Depends(get_db)):
    md = get_market_data(db)
    return md.movers() if md else {"gainers": [], "losers": []}


@router.get("/{symbol}", response_model=StockDetailOut)
def stock_detail(symbol: str, db: Session = Depends(get_db)):
    sym = symbol.upper()
    t = db.get(Ticker, sym)
    md = get_market_data(db)
    fh = get_finnhub()
    quote = md.snapshots([sym]).get(sym, {}) if md else {}
    metrics = cached(db, f"metrics:{sym}", 6 * 3600, lambda: fh.metrics(sym))
    market_cap = float(t.market_cap) if (t and t.market_cap is not None) else None
    if market_cap is None and isinstance(metrics, dict) and metrics.get("marketCapitalization"):
        market_cap = float(metrics["marketCapitalization"]) * 1e6  # Finnhub reports $M
    return StockDetailOut(
        symbol=sym,
        name=t.name if t else sym,
        sector=t.sector if t else "",
        industry=t.industry if t else "",
        logo_url=t.logo_url if t else "",
        exchange=t.exchange if t else "",
        market_cap=market_cap,
        price=quote.get("price"),
        prev_close=quote.get("prev_close"),
        change=quote.get("change"),
        change_pct=quote.get("change_pct"),
        fundamentals=metrics if isinstance(metrics, dict) else None,
    )


@router.get("/{symbol}/history")
def history(symbol: str, range: str = "1M", db: Session = Depends(get_db)):
    md = get_market_data(db)
    return md.bars(symbol, range) if md else []


@router.get("/{symbol}/news")
def news(symbol: str, db: Session = Depends(get_db)):
    md = get_market_data(db)
    return md.news([symbol.upper()], 15) if md else []


@router.get("/{symbol}/earnings")
def earnings(symbol: str, db: Session = Depends(get_db)):
    sym = symbol.upper()
    fh = get_finnhub()
    return cached(db, f"earnings:{sym}", 12 * 3600, lambda: fh.earnings(sym)) or []


@router.get("/{symbol}/analysis")
def analysis(symbol: str, db: Session = Depends(get_db)):
    sym = symbol.upper()
    fh = get_finnhub()
    return cached(db, f"reco:{sym}", 12 * 3600, lambda: fh.recommendation(sym)) or []


@router.get("/{symbol}/dividends")
def dividends(symbol: str, db: Session = Depends(get_db)):
    md = get_market_data(db)
    return md.dividends(symbol) if md else []


@router.get("/{symbol}/signals", response_model=list[SignalOut])
def stock_signals(symbol: str, db: Session = Depends(get_db)):
    sym = symbol.upper()
    rows = (
        db.query(Signal)
        .filter(func.upper(Signal.symbol) == sym)
        .order_by(Signal.id.desc())
        .limit(50)
        .all()
    )
    return [
        SignalOut(
            id=s.id,
            politician=s.politician,
            symbol=s.symbol,
            tx_type=s.tx_type,
            tx_date=s.tx_date,
            disclosed_date=s.disclosed_date,
            amount_range=s.amount_range,
            source_url=s.source_url,
        )
        for s in rows
    ]
