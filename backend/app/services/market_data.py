"""Alpaca market data (price snapshots, history bars, news, movers, dividends).

Built from the first brokerage account's stored keys (data API keys are shared
with trading keys). Every method is best-effort: any failure returns empty so
research pages degrade gracefully.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import BrokerageAccount


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MarketData:
    def __init__(self, key: str, secret: str):
        self._key = key
        self._secret = secret
        self._stock = None

    def _stock_client(self):
        if self._stock is None:
            from alpaca.data.historical import StockHistoricalDataClient
            self._stock = StockHistoricalDataClient(self._key, self._secret)
        return self._stock

    # ---- current price + previous close (batch) ----
    def snapshots(self, symbols: list[str]) -> dict[str, dict]:
        symbols = [s.upper() for s in symbols if s]
        if not symbols:
            return {}
        try:
            from alpaca.data.requests import StockSnapshotRequest
            snap = self._stock_client().get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=symbols)
            )
        except Exception:
            return {}
        out: dict[str, dict] = {}
        for sym, s in (snap or {}).items():
            try:
                price = None
                if getattr(s, "latest_trade", None):
                    price = float(s.latest_trade.price)
                elif getattr(s, "daily_bar", None):
                    price = float(s.daily_bar.close)
                prev = None
                if getattr(s, "previous_daily_bar", None):
                    prev = float(s.previous_daily_bar.close)
                change = (price - prev) if (price is not None and prev) else None
                pct = (change / prev * 100) if (change is not None and prev) else None
                # Guard against stale/unadjusted previous-close (splits, old daily
                # bar): a >40% one-day move on a large cap is a data error, not real.
                if pct is not None and abs(pct) > 40:
                    prev = change = pct = None
                out[sym] = {"price": price, "prev_close": prev, "change": change, "change_pct": pct}
            except Exception:
                continue
        return out

    # ---- historical bars for a range ----
    def _range(self, rng: str):
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        now = _now()
        rng = (rng or "1M").upper()
        if rng == "1D":
            return TimeFrame(15, TimeFrameUnit.Minute), now - timedelta(days=2)
        if rng == "1W":
            return TimeFrame(1, TimeFrameUnit.Hour), now - timedelta(days=8)
        if rng == "1M":
            return TimeFrame.Day, now - timedelta(days=35)
        if rng == "3M":
            return TimeFrame.Day, now - timedelta(days=95)
        if rng == "YTD":
            return TimeFrame.Day, datetime(now.year, 1, 1, tzinfo=timezone.utc)
        if rng == "1Y":
            return TimeFrame.Day, now - timedelta(days=370)
        return TimeFrame.Week, now - timedelta(days=365 * 5)

    def bars(self, symbol: str, rng: str = "1M") -> list[dict]:
        symbol = symbol.upper()
        try:
            from alpaca.data.requests import StockBarsRequest
            tf, start = self._range(rng)
            resp = self._stock_client().get_stock_bars(
                StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, limit=3000)
            )
            data = resp.data.get(symbol, []) if hasattr(resp, "data") else resp[symbol]
            return [
                {
                    "t": b.timestamp.isoformat(),
                    "price": float(b.close),
                    "volume": float(b.volume or 0),
                }
                for b in data
            ]
        except Exception:
            return []

    # ---- company news ----
    def news(self, symbols: list[str], limit: int = 15) -> list[dict]:
        try:
            from alpaca.data.historical.news import NewsClient
            from alpaca.data.requests import NewsRequest
            client = NewsClient(self._key, self._secret)
            resp = client.get_news(NewsRequest(symbols=",".join(symbols), limit=limit))
            articles = getattr(resp, "news", None)
            if articles is None and hasattr(resp, "data"):
                articles = resp.data.get("news", [])
            out = []
            for a in articles or []:
                imgs = getattr(a, "images", None) or []
                img = imgs[0].url if imgs and hasattr(imgs[0], "url") else (imgs[0].get("url") if imgs and isinstance(imgs[0], dict) else "")
                created = getattr(a, "created_at", None)
                out.append(
                    {
                        "headline": getattr(a, "headline", ""),
                        "summary": getattr(a, "summary", ""),
                        "source": getattr(a, "source", ""),
                        "url": getattr(a, "url", ""),
                        "datetime": created.isoformat() if created else None,
                        "image": img,
                        "symbols": getattr(a, "symbols", []),
                    }
                )
            return out
        except Exception:
            return []

    # ---- market movers ----
    def movers(self) -> dict:
        try:
            from alpaca.data.historical.screener import ScreenerClient
            from alpaca.data.requests import MarketMoversRequest
            client = ScreenerClient(self._key, self._secret)
            resp = client.get_market_movers(MarketMoversRequest(top=12))

            def _m(items):
                return [
                    {
                        "symbol": getattr(i, "symbol", ""),
                        "price": float(getattr(i, "price", 0) or 0),
                        "change_pct": float(getattr(i, "percent_change", 0) or 0),
                    }
                    for i in (items or [])
                ]

            return {"gainers": _m(getattr(resp, "gainers", [])), "losers": _m(getattr(resp, "losers", []))}
        except Exception:
            return {"gainers": [], "losers": []}

    # ---- dividends (corporate actions) ----
    def dividends(self, symbol: str) -> list[dict]:
        try:
            from alpaca.data.historical.corporate_actions import CorporateActionsClient
            from alpaca.data.requests import CorporateActionsRequest
            client = CorporateActionsClient(self._key, self._secret)
            resp = client.get_corporate_actions(
                CorporateActionsRequest(
                    symbols=[symbol.upper()],
                    types=["cash_dividend"],
                    start=(_now() - timedelta(days=730)).date(),
                )
            )
            data = resp.data if hasattr(resp, "data") else {}
            divs = data.get("cash_dividends", []) if isinstance(data, dict) else []
            out = []
            for d in divs:
                out.append(
                    {
                        "ex_date": str(getattr(d, "ex_date", "") or (d.get("ex_date") if isinstance(d, dict) else "")),
                        "amount": float(getattr(d, "rate", 0) or (d.get("rate") if isinstance(d, dict) else 0) or 0),
                        "pay_date": str(getattr(d, "payable_date", "") or (d.get("payable_date") if isinstance(d, dict) else "")),
                    }
                )
            return out
        except Exception:
            return []


def market_data_for(db: Session) -> MarketData | None:
    acct = db.query(BrokerageAccount).first()
    if acct is None:
        return None
    secret = decrypt_secret(acct.alpaca_secret, get_settings().fernet_key)
    return MarketData(acct.alpaca_key_id, secret)
