"""Finnhub free-tier client + a generic TTL cache (MarketCache).

Everything degrades gracefully: with no API key (or on any error / rate-limit)
methods return None and the routers fall back to sample/empty data so the UI
never breaks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import MarketCache

BASE = "https://finnhub.io/api/v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(ts: datetime | None) -> float:
    if ts is None:
        return 1e12
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (_now() - ts).total_seconds()


def cached(db: Session, key: str, ttl_sec: int, fetch: Callable[[], Any]) -> Any:
    """Return cached data if fresh, else fetch + store. Never raises."""
    row = db.get(MarketCache, key)
    if row is not None and _age_seconds(row.fetched_at) <= ttl_sec:
        return row.data.get("v") if isinstance(row.data, dict) and "v" in row.data else row.data
    data = fetch()
    if data is not None:
        payload = data if isinstance(data, dict) and "v" not in data else {"v": data}
        if row is None:
            db.add(MarketCache(key=key, data=payload, fetched_at=_now()))
        else:
            row.data = payload
            row.fetched_at = _now()
        db.commit()
    return data


def get_cached_many(db: Session, keys: list[str], ttl_sec: int) -> dict[str, Any]:
    """Batched read-only cache lookup — ONE query for all `keys` instead of
    one round-trip per key. Returns only the fresh entries; missing/stale
    keys are simply absent from the result (caller treats that as "go
    fetch this one"). Never fetches itself — pairs with `set_cached_many()`
    so a caller can fetch the misses concurrently before writing them back.

    This exists because, against a remote DB (e.g. a pooled Postgres a few
    hundred ms away), N sequential `db.get()` calls dominate wall-clock time
    even after the actual upstream API fetch is made concurrent — batching
    the reads is what actually fixes that, not just parallelizing fetches."""
    if not keys:
        return {}
    rows = db.query(MarketCache).filter(MarketCache.key.in_(keys)).all()
    out: dict[str, Any] = {}
    for row in rows:
        if _age_seconds(row.fetched_at) <= ttl_sec:
            out[row.key] = row.data.get("v") if isinstance(row.data, dict) and "v" in row.data else row.data
    return out


def set_cached_many(db: Session, items: dict[str, Any]) -> None:
    """Batched write-through cache set — ONE upsert statement for all `items`
    regardless of count, instead of one INSERT/UPDATE per key. Skips None
    values (never cache a failed fetch, same as `cached()`).

    Deliberately a single `INSERT ... ON CONFLICT DO UPDATE` rather than a
    SELECT-then-add()-per-row loop: the latter still issues one round-trip
    per row at flush time even inside a single `commit()` (measured ~1.25s
    for 16 rows against a remote pooled DB, vs ~0.1-0.2s for a real batched
    statement) — same class of hidden-N-round-trips problem as the read
    side, just on the write path."""
    items = {k: v for k, v in items.items() if v is not None}
    if not items:
        return
    now = _now()
    rows = [
        {
            "key": key,
            "data": data if isinstance(data, dict) and "v" not in data else {"v": data},
            "fetched_at": now,
        }
        for key, data in items.items()
    ]
    stmt = pg_insert(MarketCache).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MarketCache.key],
        set_={"data": stmt.excluded.data, "fetched_at": stmt.excluded.fetched_at},
    )
    db.execute(stmt)
    db.commit()


class FinnhubClient:
    def __init__(self, token: str | None = None):
        self.token = token if token is not None else get_settings().finnhub_api_key

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _get(self, path: str, params: dict) -> Any:
        if not self.enabled:
            return None
        try:
            r = httpx.get(f"{BASE}{path}", params={**params, "token": self.token}, timeout=10.0)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    def profile(self, symbol: str) -> dict | None:
        return self._get("/stock/profile2", {"symbol": symbol})

    def metrics(self, symbol: str) -> dict | None:
        d = self._get("/stock/metric", {"symbol": symbol, "metric": "all"})
        if isinstance(d, dict):
            return d.get("metric")
        return None

    def earnings(self, symbol: str) -> list | None:
        # historical EPS surprises
        d = self._get("/stock/earnings", {"symbol": symbol})
        return d if isinstance(d, list) else None

    def recommendation(self, symbol: str) -> list | None:
        d = self._get("/stock/recommendation", {"symbol": symbol})
        return d if isinstance(d, list) else None

    def earnings_calendar(self, days_ahead: int = 90) -> dict | None:
        """Upcoming earnings for the whole market in one call (so the stock-finder
        'Earnings' column costs one request, not one-per-symbol)."""
        today = _now().date()
        frm = today.isoformat()
        to = (today + timedelta(days=days_ahead)).isoformat()
        d = self._get("/calendar/earnings", {"from": frm, "to": to})
        return d if isinstance(d, dict) else None


def get_finnhub() -> FinnhubClient:
    return FinnhubClient()
