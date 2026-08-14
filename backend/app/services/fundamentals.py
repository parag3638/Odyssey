from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import get_sessionmaker
from app.services.finnhub import get_finnhub, set_cached_many


FUNDAMENTALS_TTL_SEC = 24 * 3600
FUNDAMENTALS_BATCH_SIZE = 8
FUNDAMENTALS_CONCURRENCY = 4


def refresh_fundamentals_symbols(symbols: list[str]) -> int:
    """Fetch a bounded fundamentals batch without holding a DB connection."""
    fh = get_finnhub()
    if not fh.enabled or not symbols:
        return 0

    fetched: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=FUNDAMENTALS_CONCURRENCY) as pool:
        futures = {pool.submit(fh.metrics, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                value = future.result()
            except Exception:
                value = None
            if isinstance(value, dict):
                fetched[f"metrics:{symbol}"] = value

    if not fetched:
        return 0
    db = get_sessionmaker()()
    try:
        set_cached_many(db, fetched)
    finally:
        db.close()
    return len(fetched)


def refresh_earnings_calendar() -> bool:
    """Refresh the shared earnings calendar outside browser request handling."""
    fh = get_finnhub()
    if not fh.enabled:
        return False
    value = fh.earnings_calendar()
    if not isinstance(value, dict):
        return False
    db = get_sessionmaker()()
    try:
        set_cached_many(db, {"earnings_cal": value})
    finally:
        db.close()
    return True
