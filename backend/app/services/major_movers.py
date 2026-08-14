from collections.abc import Iterable, Mapping
from math import isfinite
from typing import Any


MAJOR_UNIVERSE_SIZE = 100
MAX_DAILY_MOVE_PCT = 40.0
MIN_PRICE = 5.0


def build_major_movers(
    tickers: Iterable[Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = 12,
) -> dict[str, list[dict[str, Any]]]:
    """Rank valid daily moves within a preselected large-cap universe."""
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        symbol = str(ticker["symbol"])
        quote = quotes.get(symbol) or {}
        price = quote.get("price")
        change_pct = quote.get("change_pct")
        if price is None or change_pct is None:
            continue

        price = float(price)
        change_pct = float(change_pct)
        if (
            not isfinite(price)
            or not isfinite(change_pct)
            or price < MIN_PRICE
            or change_pct == 0
            or abs(change_pct) > MAX_DAILY_MOVE_PCT
        ):
            continue

        rows.append(
            {
                "symbol": symbol,
                "name": str(ticker.get("name") or ""),
                "price": price,
                "change_pct": change_pct,
            }
        )

    gainers = sorted(
        (row for row in rows if row["change_pct"] > 0),
        key=lambda row: row["change_pct"],
        reverse=True,
    )[:limit]
    losers = sorted(
        (row for row in rows if row["change_pct"] < 0),
        key=lambda row: row["change_pct"],
    )[:limit]
    return {"gainers": gainers, "losers": losers}
