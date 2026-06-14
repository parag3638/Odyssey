"""Finnhub free-tier client + a generic TTL cache (MarketCache).

Everything degrades gracefully: with no API key (or on any error / rate-limit)
methods return None and the routers fall back to sample/empty data so the UI
never breaks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
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
