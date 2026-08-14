from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock
from time import monotonic

from app.db import get_sessionmaker
from app.models import MarketCache
from app.services import ai as ai_service
from app.services.finnhub import get_finnhub, set_cached_many
from app.services.market_data import market_data_for

SOURCE_TTLS = {
    "news": 1800,
    "earnings": 12 * 3600,
    "reco": 12 * 3600,
    "div": 12 * 3600,
    "metrics": 24 * 3600,
    "bars": 3600,
}
AI_TTL_SEC = 6 * 3600

_state_lock = Lock()
_refreshing: set[str] = set()
_scheduled: set[str] = set()
_recent: dict[str, float] = {}
_refresh_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research-refresh")

COMBINED_AI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "summary_citation_ids": {"type": "array", "items": {"type": "string"}},
        "bull": {"type": "array", "items": {"type": "string"}},
        "bear": {"type": "array", "items": {"type": "string"}},
        "crux": {"type": "string"},
        "bull_bear_citation_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary", "summary_citation_ids", "bull", "bear", "crux",
        "bull_bear_citation_ids",
    ],
}

COMBINED_AI_SYSTEM = (
    ai_service.SYSTEM_PROMPT
    + " Also produce three concise bullish points, three concise bearish points, "
      "and one sentence naming the central uncertainty. Use the same supplied facts "
      "for both outputs and return citation ids separately for the summary and debate."
)


def mark_research_view(symbol: str) -> None:
    with _state_lock:
        _recent[symbol.upper()] = monotonic()


def recent_research_symbols(limit: int = 4) -> list[str]:
    cutoff = monotonic() - 24 * 3600
    with _state_lock:
        active = [(symbol, seen) for symbol, seen in _recent.items() if seen >= cutoff]
    active.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in active[:limit]]


def is_research_refreshing(symbol: str) -> bool:
    with _state_lock:
        prefix = f"{symbol.upper()}:"
        return any(
            token.startswith(prefix) for token in (_refreshing | _scheduled)
        )


def schedule_research_refresh(symbol: str, range_name: str = "1M") -> bool:
    """Queue a bounded refresh without extending the originating HTTP request."""
    symbol = symbol.upper()
    range_name = range_name.upper()
    token = f"{symbol}:{range_name}"
    with _state_lock:
        if token in _refreshing or token in _scheduled:
            return False
        _scheduled.add(token)

    def _run() -> None:
        try:
            refresh_research_symbol(symbol, range_name)
        finally:
            with _state_lock:
                _scheduled.discard(token)

    _refresh_executor.submit(_run)
    return True


def _age_seconds(value: datetime | None) -> float:
    if value is None:
        return float("inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - value).total_seconds()


def _unwrap(row: MarketCache | None):
    if row is None:
        return None
    return row.data.get("v") if isinstance(row.data, dict) and "v" in row.data else row.data


def _generate_combined_ai(symbol: str) -> bool:
    db = get_sessionmaker()()
    try:
        keys = [f"news:{symbol}", f"reco:{symbol}", f"metrics:{symbol}", f"ai_research:{symbol}"]
        rows = db.query(MarketCache).filter(MarketCache.key.in_(keys)).all()
        by_key = {row.key: row for row in rows}
        existing = by_key.get(f"ai_research:{symbol}")
        if existing is not None and _age_seconds(existing.fetched_at) <= AI_TTL_SEC:
            return False
        news = _unwrap(by_key.get(f"news:{symbol}"))
        reco = _unwrap(by_key.get(f"reco:{symbol}"))
        metrics = _unwrap(by_key.get(f"metrics:{symbol}"))
    finally:
        db.close()

    bundle = ai_service.build_summary_bundle(
        symbol,
        news=news if isinstance(news, list) else [],
        reco=reco if isinstance(reco, list) else [],
        metrics=metrics if isinstance(metrics, dict) else None,
    )
    if not bundle["facts"]:
        return False
    from app.routers.ai import get_llm
    llm = get_llm()
    if not llm.enabled:
        return False
    result = llm.complete_json(
        system=COMBINED_AI_SYSTEM,
        user=ai_service.render_user_prompt(symbol, bundle),
        schema=COMBINED_AI_SCHEMA,
        max_tokens=900,
    )
    if not result or not result.get("summary"):
        return False
    summary_citations = ai_service.validate_citations(
        result.get("summary_citation_ids") or [], bundle
    )
    debate_citations = ai_service.validate_citations(
        result.get("bull_bear_citation_ids") or [], bundle
    )
    value = {
        "summary": {
            "available": True,
            "text": result["summary"],
            "citations": summary_citations,
            "model": llm.model,
        },
        "bull_bear": {
            "available": True,
            "bull": result.get("bull") or [],
            "bear": result.get("bear") or [],
            "crux": result.get("crux") or None,
            "citations": debate_citations,
            "model": llm.model,
        },
    }
    db = get_sessionmaker()()
    try:
        set_cached_many(db, {f"ai_research:{symbol}": value})
    finally:
        db.close()
    return True


def refresh_research_symbol(symbol: str, range_name: str = "1M") -> None:
    symbol = symbol.upper()
    token = f"{symbol}:{range_name}"
    with _state_lock:
        if token in _refreshing:
            return
        _refreshing.add(token)
    changed = False
    try:
        db = get_sessionmaker()()
        try:
            md = market_data_for(db)
            fh = get_finnhub()
            names = {
                "news": f"news:{symbol}",
                "earnings": f"earnings:{symbol}",
                "reco": f"reco:{symbol}",
                "div": f"div:{symbol}",
                "metrics": f"metrics:{symbol}",
                "bars": f"bars:{symbol}:{range_name}",
            }
            rows = db.query(MarketCache).filter(MarketCache.key.in_(names.values())).all()
            fetched_at = {row.key: row.fetched_at for row in rows}
            stale = {
                name for name, key in names.items()
                if _age_seconds(fetched_at.get(key)) > SOURCE_TTLS[name]
            }
            db.rollback()
        finally:
            db.close()

        tasks = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            if md and "news" in stale:
                tasks[pool.submit(md.news, [symbol], 15)] = names["news"]
            if md and "bars" in stale:
                tasks[pool.submit(md.bars, symbol, range_name)] = names["bars"]
            if md and "div" in stale:
                tasks[pool.submit(md.dividends, symbol)] = names["div"]
            if fh.enabled and "earnings" in stale:
                tasks[pool.submit(fh.earnings, symbol)] = names["earnings"]
            if fh.enabled and "reco" in stale:
                tasks[pool.submit(fh.recommendation, symbol)] = names["reco"]
            if fh.enabled and "metrics" in stale:
                tasks[pool.submit(fh.metrics, symbol)] = names["metrics"]
            fetched = {}
            for future in as_completed(tasks):
                try:
                    value = future.result()
                except Exception:
                    value = None
                if value is not None:
                    fetched[tasks[future]] = value
        if fetched:
            db = get_sessionmaker()()
            try:
                set_cached_many(db, fetched)
            finally:
                db.close()
            changed = True
        changed = _generate_combined_ai(symbol) or changed
    finally:
        with _state_lock:
            _refreshing.discard(token)
        if changed:
            from app.routers.research import clear_research_cache
            clear_research_cache(symbol)
