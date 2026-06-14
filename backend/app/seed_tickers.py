"""Seed the stock universe.

  python -m app.seed_tickers          # universe + Finnhub enrichment (if key set)
  python -m app.seed_tickers --no-enrich

Creates the new tables (tickers/watchlist/market_cache) if missing, upserts the
curated universe (names/sectors/industries — works with no API key), and, when
FINNHUB_API_KEY is set, enriches each row with logo + market cap (throttled to
the free 60/min limit).
"""
import sys
import time

from app.db import Base, get_engine, get_sessionmaker
from app.data.universe import APPROX_MARKET_CAP_B, UNIVERSE
from app.models import Ticker
from app.services.finnhub import FinnhubClient


def seed(enrich: bool = True) -> None:
    Base.metadata.create_all(get_engine())
    db = get_sessionmaker()()
    try:
        for symbol, name, sector, industry in UNIVERSE:
            t = db.get(Ticker, symbol)
            if t is None:
                t = Ticker(symbol=symbol)
                db.add(t)
            t.name, t.sector, t.industry = name, sector, industry
            cap = APPROX_MARKET_CAP_B.get(symbol)
            if cap:
                t.market_cap = cap * 1e9
        db.commit()
        print(f"✓ seeded {len(UNIVERSE)} tickers (names/sectors/industries)")

        fh = FinnhubClient()
        if enrich and fh.enabled:
            print("→ enriching logos + market cap from Finnhub (≈3 min, 60/min limit)…")
            done = 0
            for symbol, *_ in UNIVERSE:
                p = fh.profile(symbol)
                if p:
                    t = db.get(Ticker, symbol)
                    if p.get("logo"):
                        t.logo_url = p["logo"]
                    if p.get("marketCapitalization"):
                        t.market_cap = float(p["marketCapitalization"]) * 1e6
                    if p.get("exchange"):
                        t.exchange = p["exchange"]
                    db.commit()
                    done += 1
                time.sleep(1.1)  # stay under 60 calls/min
            print(f"✓ enriched {done} tickers from Finnhub")
        else:
            print("• FINNHUB_API_KEY not set — skipped logo/market-cap enrichment "
                  "(universe + sectors are still fully usable).")
    finally:
        db.close()


if __name__ == "__main__":
    seed(enrich="--no-enrich" not in sys.argv)
