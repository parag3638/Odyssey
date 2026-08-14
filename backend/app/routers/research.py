import hashlib
import json
from threading import Lock
from time import monotonic, time_ns

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import MarketCache, Signal
from app.schemas import (
    AiResponse,
    BullBearOut,
    ResearchAiOut,
    ResearchBootstrapOut,
    SignalOut,
    StockDetailOut,
)
from app.services.research import (
    is_research_refreshing,
    mark_research_view,
    schedule_research_refresh,
)

router = APIRouter(prefix="/research")
_CACHE_TTL_SEC = 60
_cache: dict[str, tuple[float, str, ResearchBootstrapOut]] = {}
_cache_lock = Lock()


def clear_research_cache(symbol: str | None = None) -> None:
    with _cache_lock:
        if symbol is None:
            _cache.clear()
            return
        prefix = f"{symbol.upper()}:"
        for key in [key for key in _cache if key.startswith(prefix)]:
            _cache.pop(key, None)


def _unwrap(row: MarketCache | None):
    if row is None:
        return None
    return row.data.get("v") if isinstance(row.data, dict) and "v" in row.data else row.data


def _signal_out(signal: Signal) -> SignalOut:
    return SignalOut(
        id=signal.id,
        politician=signal.politician,
        symbol=signal.symbol,
        tx_type=signal.tx_type,
        tx_date=signal.tx_date,
        disclosed_date=signal.disclosed_date,
        amount_range=signal.amount_range,
        source_url=signal.source_url,
    )


def _build_bundle(db: Session, symbol: str, range_name: str) -> ResearchBootstrapOut:
    from app.routers.stocks import stock_catalog_row

    row = stock_catalog_row(db, symbol)
    keys = [
        f"metrics:{symbol}",
        f"bars:{symbol}:{range_name}",
        f"news:{symbol}",
        f"earnings:{symbol}",
        f"div:{symbol}",
        f"reco:{symbol}",
        f"ai_research:{symbol}",
        f"ai_sum:{symbol}",
        f"ai_bullbear:{symbol}",
    ]
    cache_rows = db.query(MarketCache).filter(MarketCache.key.in_(keys)).all()
    cache = {item.key: _unwrap(item) for item in cache_rows}
    signals = (
        db.query(Signal)
        .filter(func.upper(Signal.symbol) == symbol)
        .order_by(Signal.id.desc())
        .limit(50)
        .all()
    )
    raw_metrics = cache.get(f"metrics:{symbol}")
    stock = None
    if row is not None:
        stock = StockDetailOut(
            **row.model_dump(exclude={"metrics"}),
            exchange="",
            prev_close=None,
            fundamentals=raw_metrics if isinstance(raw_metrics, dict) else None,
        )

    combined = cache.get(f"ai_research:{symbol}")
    combined = combined if isinstance(combined, dict) else {}
    summary_data = combined.get("summary") or cache.get(f"ai_sum:{symbol}")
    bull_bear_data = combined.get("bull_bear") or cache.get(f"ai_bullbear:{symbol}")
    summary = (
        AiResponse(**summary_data)
        if isinstance(summary_data, dict)
        else AiResponse(available=False)
    )
    bull_bear = (
        BullBearOut(**bull_bear_data)
        if isinstance(bull_bear_data, dict)
        else BullBearOut(available=False)
    )
    return ResearchBootstrapOut(
        symbol=symbol,
        range=range_name,
        stock=stock,
        history=cache.get(f"bars:{symbol}:{range_name}") or [],
        signals=[_signal_out(signal) for signal in signals],
        news=cache.get(f"news:{symbol}") or [],
        earnings=cache.get(f"earnings:{symbol}") or [],
        dividends=cache.get(f"div:{symbol}") or [],
        analysis=cache.get(f"reco:{symbol}") or [],
        ai_summary=summary,
        bull_bear=bull_bear,
        ai_pending=(
            bool(get_settings().openai_api_key)
            and not (summary.available and bull_bear.available)
        ),
    )


def _bundle(db: Session, symbol: str, range_name: str) -> tuple[str, ResearchBootstrapOut]:
    key = f"{symbol}:{range_name}"
    now = monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1], cached[2]
        payload = _build_bundle(db, symbol, range_name)
        version = str(time_ns())
        _cache[key] = (now + _CACHE_TTL_SEC, version, payload)
        return version, payload


@router.get("/{symbol}/bootstrap", response_model=ResearchBootstrapOut)
def research_bootstrap(
    symbol: str,
    request: Request,
    response: Response,
    range: str = "1M",
    db: Session = Depends(get_db),
):
    sym = symbol.upper()
    range_name = (range or "1M").upper()
    mark_research_view(sym)
    version, payload = _bundle(db, sym, range_name)
    schedule_research_refresh(sym, range_name)
    fingerprint = json.dumps([version, sym, range_name], separators=(",", ":"))
    etag = f'"{hashlib.sha1(fingerprint.encode()).hexdigest()}"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=dict(response.headers))
    return payload


@router.get("/{symbol}/ai", response_model=ResearchAiOut)
def research_ai(
    symbol: str,
    db: Session = Depends(get_db),
):
    sym = symbol.upper()
    _, payload = _bundle(db, sym, "1M")
    pending = payload.ai_pending and (
        is_research_refreshing(sym)
        or not (payload.ai_summary.available and payload.bull_bear.available)
    )
    if pending:
        schedule_research_refresh(sym, "1M")
    return ResearchAiOut(
        summary=payload.ai_summary,
        bull_bear=payload.bull_bear,
        pending=pending,
    )
