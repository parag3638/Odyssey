import asyncio
import hashlib
import json
from threading import Lock
from time import monotonic, time_ns

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MarketCache, Signal, Ticker
from app.schemas import (
    IndustryOut,
    SignalOut,
    StockDetailOut,
    StockFinderRow,
    StockMetrics,
    StockRow,
    StocksBootstrapOut,
)
from app.services.finnhub import cached, get_cached_many, set_cached_many
from app.services.finnhub import get_finnhub as _get_finnhub
from app.services.market_data import market_data_for

router = APIRouter(prefix="/stocks")

# Cap symbols enriched per request so one page load stays within Finnhub's
# free-tier rate limit (60 req/min); metrics are then cached 6h per symbol.
_METRICS_PER_REQUEST = 60
# Bounded concurrency for uncached symbols within one /metrics request. This
# does NOT increase the total number of Finnhub calls per page load (same
# count as before, still capped by _METRICS_PER_REQUEST) — it just stops
# them being issued one-at-a-time in a blocking loop, which was the actual
# cause of multi-second page loads on a cold cache (N sequential network
# round-trips instead of N/_METRICS_CONCURRENCY concurrent ones).
_METRICS_CONCURRENCY = 8
_CATALOG_TTL_SEC = 300
_catalog_cache: tuple[
    float, str, list[StockFinderRow], list[IndustryOut]
] | None = None
_catalog_lock = Lock()


# Overridable seams for tests (mirror orders_router.get_broker_for_account).
def get_market_data(db: Session):
    return market_data_for(db)


def get_finnhub():
    return _get_finnhub()


def clear_stocks_catalog_cache() -> None:
    global _catalog_cache
    with _catalog_lock:
        _catalog_cache = None


def warm_stocks_catalog_cache(db: Session) -> None:
    clear_stocks_catalog_cache()
    _stocks_catalog(db)


def _cache_value(row: MarketCache | None):
    if row is None:
        return None
    return row.data.get("v") if isinstance(row.data, dict) and "v" in row.data else row.data


def _all_quotes(db: Session, md) -> dict:
    """Whole-universe price snapshots, cached 5 min under one key so every stock
    page (list + detail) reads prices from cache instead of hitting Alpaca per load."""
    if not md:
        return {}
    syms = [s for (s,) in db.query(Ticker.symbol).all()]
    return cached(db, "quotes_all", 300, lambda: md.snapshots(syms)) or {}


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
    if sort == "market_cap":
        query = query.order_by(Ticker.market_cap.desc().nullslast(), Ticker.symbol)
        tickers = query.limit(limit).all()
    elif sort == "symbol":
        tickers = query.order_by(Ticker.symbol).limit(limit).all()
    else:
        tickers = query.all()
    md = get_market_data(db)
    quotes = _all_quotes(db, md)
    rows = [_row(t, quotes.get(t.symbol)) for t in tickers]
    if sort == "change_pct":
        rows.sort(key=lambda r: r.change_pct if r.change_pct is not None else -1e18, reverse=True)
    elif sort == "symbol":
        rows.sort(key=lambda r: r.symbol)
    elif sort == "price":
        rows.sort(key=lambda r: r.price if r.price is not None else -1e18, reverse=True)
    elif sort == "market_cap":
        pass  # already sorted and limited by Postgres
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


def _build_stocks_catalog(
    db: Session,
) -> tuple[str, list[StockFinderRow], list[IndustryOut]]:
    tickers = (
        db.query(Ticker)
        .order_by(Ticker.market_cap.desc().nullslast(), Ticker.symbol)
        .all()
    )
    cache_rows = (
        db.query(MarketCache)
        .filter(
            or_(
                MarketCache.key == "quotes_all",
                MarketCache.key == "earnings_cal",
                MarketCache.key.like("metrics:%"),
            )
        )
        .all()
    )
    cache = {row.key: _cache_value(row) for row in cache_rows}
    quotes = cache.get("quotes_all")
    quotes = quotes if isinstance(quotes, dict) else {}
    calendar = cache.get("earnings_cal")
    upcoming = _upcoming_earnings_symbols(calendar if isinstance(calendar, dict) else None)

    rows: list[StockFinderRow] = []
    industry_counts: dict[tuple[str, str], int] = {}
    for ticker in tickers:
        raw_metrics = cache.get(f"metrics:{ticker.symbol}")
        metrics = _map_metrics(raw_metrics if isinstance(raw_metrics, dict) else None)
        if ticker.symbol in upcoming:
            metrics.earnings = "Pending"
        quote = quotes.get(ticker.symbol) or {}
        rows.append(
            StockFinderRow(
                symbol=ticker.symbol,
                name=ticker.name,
                sector=ticker.sector,
                industry=ticker.industry,
                logo_url=ticker.logo_url,
                market_cap=(
                    float(ticker.market_cap)
                    if ticker.market_cap is not None
                    else metrics.marketCap
                ),
                price=quote.get("price"),
                change=quote.get("change"),
                change_pct=quote.get("change_pct"),
                metrics=metrics,
            )
        )
        if ticker.industry:
            key = (ticker.industry, ticker.sector or "")
            industry_counts[key] = industry_counts.get(key, 0) + 1

    industries = [
        IndustryOut(industry=industry, sector=sector, count=count)
        for (industry, sector), count in industry_counts.items()
    ]
    industries.sort(key=lambda item: (-item.count, item.industry))
    return str(time_ns()), rows, industries


def _stocks_catalog(
    db: Session,
) -> tuple[str, list[StockFinderRow], list[IndustryOut]]:
    global _catalog_cache
    now = monotonic()
    with _catalog_lock:
        if _catalog_cache is None or _catalog_cache[0] <= now:
            version, rows, industries = _build_stocks_catalog(db)
            _catalog_cache = (now + _CATALOG_TTL_SEC, version, rows, industries)
        return _catalog_cache[1], _catalog_cache[2], _catalog_cache[3]


def _screen_value(row: StockFinderRow, field: str):
    values = {
        "sector": row.sector,
        "industry": row.industry,
        "change_pct": row.change_pct,
        "marketCap": row.market_cap or row.metrics.marketCap,
        "pe": row.metrics.pe,
        "eps": row.metrics.eps,
        "revenue": row.metrics.revenue,
        "revYoY": row.metrics.revYoY,
        "evSales": row.metrics.evSales,
    }
    return values.get(field)


def _passes_screen(actual, op: str, raw: str) -> bool:
    if actual is None:
        return False
    if isinstance(actual, str):
        expected = raw.strip().lower()
        value = actual.lower()
        return value == expected if op == "=" else expected in value
    try:
        target = float(raw.replace("$", "").replace(",", "").replace("%", "").strip())
    except (AttributeError, ValueError):
        return True
    return {
        "<": actual < target,
        "<=": actual <= target,
        ">": actual > target,
        ">=": actual >= target,
        "=": actual == target,
    }.get(op, True)


def _sort_value(row: StockFinderRow, field: str):
    fields = {
        "company": row.symbol,
        "symbol": row.symbol,
        "sector": row.sector,
        "ret1d": row.change_pct,
        "change_pct": row.change_pct,
        "mktCap": row.market_cap or row.metrics.marketCap,
        "marketCap": row.market_cap or row.metrics.marketCap,
        "rev": row.metrics.revenue,
        "revenue": row.metrics.revenue,
        "revYoY": row.metrics.revYoY,
        "pe": row.metrics.pe,
        "eps": row.metrics.eps,
        "evSales": row.metrics.evSales,
    }
    return fields.get(field, row.symbol)


def _sort_stocks(
    rows: list[StockFinderRow], field: str, direction: str
) -> list[StockFinderRow]:
    populated = [row for row in rows if _sort_value(row, field) is not None]
    missing = [row for row in rows if _sort_value(row, field) is None]
    populated.sort(
        key=lambda row: _sort_value(row, field),
        reverse=direction == "desc",
    )
    return populated + missing


@router.get("/bootstrap", response_model=StocksBootstrapOut)
def stocks_bootstrap(
    request: Request,
    response: Response,
    page: int = 1,
    page_size: int = 20,
    industry: str | None = None,
    sector: str | None = None,
    q: str | None = None,
    sort: str = "company",
    direction: str = "asc",
    screen_filters: str = "",
    db: Session = Depends(get_db),
):
    version, catalog, industries = _stocks_catalog(db)
    rows = catalog
    if industry:
        rows = [row for row in rows if row.industry == industry]
    if sector:
        rows = [row for row in rows if row.sector == sector]
    if q:
        needle = q.strip().upper()
        rows = [
            row for row in rows
            if needle in row.symbol.upper() or needle in row.name.upper()
        ]

    try:
        parsed_filters = json.loads(screen_filters) if screen_filters else []
    except json.JSONDecodeError:
        parsed_filters = []
    if isinstance(parsed_filters, list):
        for item in parsed_filters:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            op = str(item.get("op") or "")
            raw = str(item.get("value") or "")
            rows = [row for row in rows if _passes_screen(_screen_value(row, field), op, raw)]

    direction = "asc" if direction == "asc" else "desc"
    rows = _sort_stocks(rows, sort, direction)
    total = len(rows)
    page_size = max(6, min(page_size, 100))
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, page_count))
    start = (page - 1) * page_size
    payload = StocksBootstrapOut(
        stocks=rows[start:start + page_size],
        industries=industries,
        total=total,
        page=page,
        page_size=page_size,
    )
    fingerprint = json.dumps(
        [version, page, page_size, industry, sector, q, sort, direction, screen_filters],
        separators=(",", ":"),
    )
    etag = f'"{hashlib.sha1(fingerprint.encode()).hexdigest()}"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=dict(response.headers))
    return payload


@router.get("/metrics")
async def stock_metrics(symbols: str = "", db: Session = Depends(get_db)):
    """Per-symbol fundamentals for the stock-finder table. Enriches only the
    requested (current-page) symbols; each is cached 6h, the earnings calendar
    is one shared cached call. Degrades to nulls without a Finnhub key.

    Two things made this endpoint slow against a cold cache, both fixed here:
    1. Uncached symbols were fetched from Finnhub one at a time in a blocking
       loop — now fetched concurrently (bounded by _METRICS_CONCURRENCY).
    2. The cache freshness check itself was one DB round-trip per symbol —
       against a remote pooled DB that dominates wall-clock time even after
       (1) is fixed (measured ~1.2s for 5 sequential round-trips vs ~0.1s for
       the same 5 rows in one batched query). Now one batched read
       (get_cached_many) + one batched write (set_cached_many) regardless of
       how many symbols are requested."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    syms = syms[:_METRICS_PER_REQUEST]
    fh = get_finnhub()
    cal = cached(db, "earnings_cal", 12 * 3600, lambda: fh.earnings_calendar())
    upcoming = _upcoming_earnings_symbols(cal if isinstance(cal, dict) else None)

    cache_keys = {f"metrics:{sym}": sym for sym in syms}
    fresh_by_key = get_cached_many(db, list(cache_keys), 6 * 3600)
    fresh = {cache_keys[k]: v for k, v in fresh_by_key.items()}
    stale = [s for s in syms if s not in fresh]

    fetched: dict[str, dict | None] = {}
    if stale and fh.enabled:
        sem = asyncio.Semaphore(_METRICS_CONCURRENCY)

        async def fetch_one(sym: str) -> None:
            async with sem:
                fetched[sym] = await asyncio.to_thread(fh.metrics, sym)

        await asyncio.gather(*(fetch_one(s) for s in stale))
        set_cached_many(db, {f"metrics:{sym}": m for sym, m in fetched.items()})

    out: dict[str, dict] = {}
    for sym in syms:
        m = fresh[sym] if sym in fresh else fetched.get(sym)
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
    if not md:
        return {"gainers": [], "losers": []}
    return cached(db, "movers", 300, lambda: md.movers())


@router.get("/{symbol}", response_model=StockDetailOut)
def stock_detail(symbol: str, db: Session = Depends(get_db)):
    sym = symbol.upper()
    t = db.get(Ticker, sym)
    md = get_market_data(db)
    fh = get_finnhub()
    quote = _all_quotes(db, md).get(sym, {})
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
    if not md:
        return []
    sym = symbol.upper()
    rng = (range or "1M").upper()
    return cached(db, f"bars:{sym}:{rng}", 3600, lambda: md.bars(sym, rng)) or []


@router.get("/{symbol}/news")
def news(symbol: str, db: Session = Depends(get_db)):
    md = get_market_data(db)
    if not md:
        return []
    sym = symbol.upper()
    return cached(db, f"news:{sym}", 1800, lambda: md.news([sym], 15)) or []


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
    if not md:
        return []
    sym = symbol.upper()
    return cached(db, f"div:{sym}", 43200, lambda: md.dividends(sym)) or []


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
