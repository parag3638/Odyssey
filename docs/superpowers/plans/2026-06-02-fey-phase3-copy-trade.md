# Fey Phase 3 — Copy-Trade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`. **No git — skip commits.** Backend tests: `cd /Users/parag.singh/Desktop/Fey/backend && .venv/bin/pytest <args> -v`.

**Goal:** Mirror US-Congress disclosures from Capitol Trades. Add a `signals` table, a Capitol Trades fetch+parse layer (pure parse, fixture-tested), a sync service that upserts new signals, a pure copy-trade engine (dedupe by signal hash), a copy-trade bot runner reusing the Phase-1 risk chokepoint + order service, scheduler integration (copy ticks + a slower scraper job), bot-API support for `strategy_type="copy_trade"`, a `/signals` API, and the Signals screener UI.

**Architecture:** New `app/data/capitol_trades.py` (pure `parse_capitol_trades` + `signal_hash` + thin live `fetch_capitol_trades`). New `Signal` model + migration. `app/services/signals_sync.py` upserts by unique hash. Pure `app/strategies/copy_trade.py`. `app/services/copy_runner.py` routes engine actions through `place_order`; "already copied" = signal hashes recorded as `ActivityLog(event="copied")` for that bot. Scheduler gains a copy branch + a scraper job. `bots` router generalized to both strategy types. Signals screen built to `design/fey-mockup.html`.

**Tech Stack:** Existing + `httpx` (already installed) for the live fetch.

---

## Task 1: `Signal` model + migration

**Files:** Modify `backend/app/models.py`; test `backend/tests/test_models_phase3.py`

- [ ] **Step 1: Failing test `backend/tests/test_models_phase3.py`**
```python
from app.models import Signal


def test_signal_columns():
    cols = set(Signal.__table__.columns.keys())
    assert {"id", "politician", "symbol", "tx_type", "tx_date",
            "disclosed_date", "amount_range", "source_url", "hash", "scraped_at"} <= cols
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_models_phase3.py -v` — expect FAIL (ImportError Signal).

- [ ] **Step 3: Append to `backend/app/models.py`** (after `Position`):
```python
class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    politician: Mapped[str] = mapped_column(String(120))
    symbol: Mapped[str] = mapped_column(String(10))
    tx_type: Mapped[str] = mapped_column(String(4))  # buy | sell
    tx_date: Mapped[str] = mapped_column(String(10))
    disclosed_date: Mapped[str] = mapped_column(String(10))
    amount_range: Mapped[str] = mapped_column(String(40), default="")
    source_url: Mapped[str] = mapped_column(String(300), default="")
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 4: Run** the test — expect PASS.

- [ ] **Step 5: Migration** — `cd /Users/parag.singh/Desktop/Fey/backend && .venv/bin/alembic revision --autogenerate -m "signals" && .venv/bin/alembic upgrade head`. Verify `/opt/homebrew/opt/postgresql@16/bin/psql "postgresql://fey:fey@localhost:5432/fey" -c "\dt"` shows `signals`.

- [ ] **Step 6: Commit** — SKIP.

---

## Task 2: Capitol Trades parser + hash + live fetch

**Files:** Create `backend/app/data/__init__.py`, `backend/app/data/capitol_trades.py`; test `backend/tests/test_capitol_trades.py`

The PURE `parse_capitol_trades` and `signal_hash` are fully tested with a fixture. The live `fetch_capitol_trades` maps Capitol Trades' public JSON (`https://bff.capitoltrades.com/trades`) into the raw shape `parse_capitol_trades` expects — its exact field paths must be verified against a live sample during implementation; everything downstream is stable regardless.

- [ ] **Step 1: Failing test `backend/tests/test_capitol_trades.py`**
```python
from app.data.capitol_trades import parse_capitol_trades, signal_hash

RAW = [
    {"txType": "buy", "txDate": "2026-05-30", "pubDate": "2026-06-01",
     "politician": {"firstName": "Michael", "lastName": "McCaul"},
     "asset": {"assetTicker": "NVDA"}, "value": "250K–500K", "_txId": "t1"},
    {"txType": "sell", "txDate": "2026-05-27", "pubDate": "2026-05-28",
     "politician": {"firstName": "Michael", "lastName": "McCaul"},
     "asset": {"assetTicker": "AMZN"}, "value": "15K–50K", "_txId": "t2"},
    # missing ticker -> dropped
    {"txType": "buy", "txDate": "2026-05-20", "pubDate": "2026-05-22",
     "politician": {"firstName": "X", "lastName": "Y"},
     "asset": {"assetTicker": None}, "value": "1K–15K", "_txId": "t3"},
]


def test_parse_normalizes_and_drops_unticketed():
    out = parse_capitol_trades(RAW)
    assert len(out) == 2
    s = out[0]
    assert s["politician"] == "Michael McCaul"
    assert s["symbol"] == "NVDA"
    assert s["tx_type"] == "buy"
    assert s["tx_date"] == "2026-05-30"
    assert s["disclosed_date"] == "2026-06-01"
    assert s["amount_range"] == "250K–500K"
    assert len(s["hash"]) == 40  # sha1 hexdigest


def test_hash_is_stable_and_distinct():
    h1 = signal_hash("Michael McCaul", "NVDA", "buy", "2026-05-30", "250K–500K")
    h2 = signal_hash("Michael McCaul", "NVDA", "buy", "2026-05-30", "250K–500K")
    h3 = signal_hash("Michael McCaul", "NVDA", "sell", "2026-05-30", "250K–500K")
    assert h1 == h2 and h1 != h3
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_capitol_trades.py -v` — expect FAIL (no module).

- [ ] **Step 3: Create `backend/app/data/__init__.py`** (empty) and `backend/app/data/capitol_trades.py`:
```python
import hashlib
import httpx

BFF_URL = "https://bff.capitoltrades.com/trades"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json"}


def signal_hash(politician: str, symbol: str, tx_type: str, tx_date: str, amount_range: str) -> str:
    key = f"{politician}|{symbol}|{tx_type}|{tx_date}|{amount_range}".lower()
    return hashlib.sha1(key.encode()).hexdigest()


def parse_capitol_trades(raw: list[dict]) -> list[dict]:
    """Pure: normalize raw Capitol Trades trade dicts into Signal dicts; drop rows w/o ticker."""
    out: list[dict] = []
    for r in raw:
        asset = (r.get("asset") or {})
        ticker = asset.get("assetTicker")
        if not ticker:
            continue
        pol = (r.get("politician") or {})
        name = f"{pol.get('firstName', '').strip()} {pol.get('lastName', '').strip()}".strip()
        tx_type = "sell" if str(r.get("txType", "")).lower().startswith("s") else "buy"
        tx_date = str(r.get("txDate", ""))[:10]
        disclosed = str(r.get("pubDate", ""))[:10]
        amount = str(r.get("value", "") or "")
        tx_id = r.get("_txId", "")
        out.append({
            "politician": name,
            "symbol": str(ticker).upper(),
            "tx_type": tx_type,
            "tx_date": tx_date,
            "disclosed_date": disclosed,
            "amount_range": amount,
            "source_url": f"https://www.capitoltrades.com/trades/{tx_id}" if tx_id else "https://www.capitoltrades.com/trades",
            "hash": signal_hash(name, str(ticker).upper(), tx_type, tx_date, amount),
        })
    return out


def fetch_capitol_trades(limit: int = 50, politician: str | None = None) -> list[dict]:
    """Live fetch from Capitol Trades' public JSON. Maps the response into the shape
    parse_capitol_trades expects. Network/parse failures raise httpx/ValueError to the caller."""
    params = {"per_page": limit, "page": 1, "sortBy": "-txDate"}
    resp = httpx.get(BFF_URL, params=params, headers=_HEADERS, timeout=20.0)
    resp.raise_for_status()
    body = resp.json()
    rows = body.get("data", body if isinstance(body, list) else [])
    raw = []
    for d in rows:
        raw.append({
            "txType": d.get("txType"),
            "txDate": d.get("txDate"),
            "pubDate": d.get("pubDate"),
            "politician": {
                "firstName": (d.get("politician") or {}).get("firstName", ""),
                "lastName": (d.get("politician") or {}).get("lastName", ""),
            },
            "asset": {"assetTicker": (d.get("asset") or {}).get("assetTicker")},
            "value": d.get("value") or d.get("size"),
            "_txId": d.get("_txId") or d.get("txId") or d.get("id"),
        })
    signals = parse_capitol_trades(raw)
    if politician:
        pl = politician.lower()
        signals = [s for s in signals if pl in s["politician"].lower()]
    return signals
```
NOTE to implementer: the BFF field names above are the documented shape; **fetch a small live sample** (`.venv/bin/python -c "import app.data.capitol_trades as c; print(c.fetch_capitol_trades(3))"`) and, IF the live keys differ, adjust ONLY the mapping inside `fetch_capitol_trades` (not `parse_capitol_trades`, not the hash). If the site blocks the request, report it — the rest of Phase 3 still works against fixtures/manual sync.

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_capitol_trades.py -v` — expect PASS (2 tests).

- [ ] **Step 5: Commit** — SKIP.

---

## Task 3: Signals sync service

**Files:** Create `backend/app/services/signals_sync.py`; test `backend/tests/test_signals_sync.py`

- [ ] **Step 1: Failing test `backend/tests/test_signals_sync.py`**
```python
from app.services.signals_sync import upsert_signals


class _Q:
    def __init__(self, existing):
        self._existing = existing

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._existing


class _DB:
    def __init__(self, existing_hashes):
        self._existing = existing_hashes
        self.added = []
        self.committed = 0

    def query(self, model):
        class _Query:
            def __init__(self, hashes):
                self._h = hashes
            def with_entities(self, *a):
                return self
            def all(self):
                return [(h,) for h in self._h]
        return _Query(self._existing)

    def add(self, o):
        self.added.append(o)

    def commit(self):
        self.committed += 1


def test_upsert_adds_only_new():
    signals = [
        {"politician": "M M", "symbol": "NVDA", "tx_type": "buy", "tx_date": "2026-05-30",
         "disclosed_date": "2026-06-01", "amount_range": "250K–500K", "source_url": "u", "hash": "h1"},
        {"politician": "M M", "symbol": "AMZN", "tx_type": "sell", "tx_date": "2026-05-27",
         "disclosed_date": "2026-05-28", "amount_range": "15K–50K", "source_url": "u", "hash": "h2"},
    ]
    db = _DB(existing_hashes={"h1"})  # h1 already stored
    added = upsert_signals(db, signals)
    assert added == 1
    assert len(db.added) == 1
    assert db.added[0].hash == "h2"
    assert db.committed == 1
```

- [ ] **Step 2: Run** — expect FAIL (no module).

- [ ] **Step 3: Create `backend/app/services/signals_sync.py`**
```python
from app.models import Signal


def upsert_signals(db, signals: list[dict]) -> int:
    """Insert signals whose hash is not already stored. Returns count added."""
    existing = {row[0] for row in db.query(Signal).with_entities(Signal.hash).all()}
    added = 0
    for s in signals:
        if s["hash"] in existing:
            continue
        db.add(Signal(
            politician=s["politician"], symbol=s["symbol"], tx_type=s["tx_type"],
            tx_date=s["tx_date"], disclosed_date=s["disclosed_date"],
            amount_range=s.get("amount_range", ""), source_url=s.get("source_url", ""),
            hash=s["hash"],
        ))
        existing.add(s["hash"])
        added += 1
    if added:
        db.commit()
    return added


def sync_from_capitol_trades(db, limit: int = 50) -> int:
    """Live: fetch from Capitol Trades then upsert. Returns count added. Caller handles errors."""
    from app.data.capitol_trades import fetch_capitol_trades
    return upsert_signals(db, fetch_capitol_trades(limit=limit))
```

- [ ] **Step 4: Run** — expect PASS.

- [ ] **Step 5: Commit** — SKIP.

---

## Task 4: Copy-trade pure engine

**Files:** Create `backend/app/strategies/copy_trade.py`; test `backend/tests/test_copy_trade.py`

- [ ] **Step 1: Failing test `backend/tests/test_copy_trade.py`**
```python
from app.strategies.copy_trade import evaluate_copy

SIGS = [
    {"hash": "h1", "symbol": "NVDA", "tx_type": "buy"},
    {"hash": "h2", "symbol": "AMZN", "tx_type": "sell"},
    {"hash": "h3", "symbol": "GOOGL", "tx_type": "buy"},
]


def test_buys_new_buy_signals_sized_by_notional():
    d = evaluate_copy(signals=SIGS, held={}, copied_hashes=set(),
                      prices={"NVDA": 100.0, "GOOGL": 200.0},
                      per_trade_notional=1000.0, follow_buys=True, follow_sells=True)
    syms = {b.symbol: b.qty for b in d.buys}
    assert syms["NVDA"] == 10  # 1000/100
    assert syms["GOOGL"] == 5  # 1000/200
    assert "h1" in d.new_copied_hashes and "h3" in d.new_copied_hashes


def test_skips_already_copied():
    d = evaluate_copy(signals=SIGS, held={}, copied_hashes={"h1", "h3"},
                      prices={"NVDA": 100.0, "GOOGL": 200.0},
                      per_trade_notional=1000.0)
    assert not d.buys


def test_sells_only_held_symbols():
    d = evaluate_copy(signals=SIGS, held={"AMZN": 4}, copied_hashes=set(),
                      prices={"NVDA": 100.0, "GOOGL": 200.0, "AMZN": 50.0},
                      per_trade_notional=1000.0)
    assert len(d.sells) == 1 and d.sells[0].symbol == "AMZN" and d.sells[0].qty == 4
    assert "h2" in d.new_copied_hashes


def test_buy_without_price_is_deferred():
    d = evaluate_copy(signals=[{"hash": "h9", "symbol": "TSLA", "tx_type": "buy"}],
                      held={}, copied_hashes=set(), prices={}, per_trade_notional=1000.0)
    assert not d.buys and "h9" not in d.new_copied_hashes  # retried next tick
```

- [ ] **Step 2: Run** — expect FAIL (no module).

- [ ] **Step 3: Create `backend/app/strategies/copy_trade.py`**
```python
from dataclasses import dataclass, field
from app.brokers.base import BuyOrder, SellOrder


@dataclass
class CopyDecision:
    buys: list = field(default_factory=list)
    sells: list = field(default_factory=list)
    new_copied_hashes: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def evaluate_copy(*, signals: list[dict], held: dict, copied_hashes: set,
                  prices: dict, per_trade_notional: float,
                  follow_buys: bool = True, follow_sells: bool = True) -> CopyDecision:
    """Pure copy-trade logic. `held` = {symbol: qty} currently owned. `prices` = {symbol: price}.
    A signal is marked copied only when we actually act on it (so price-less buys retry later)."""
    d = CopyDecision()
    for s in signals:
        h = s["hash"]
        if h in copied_hashes or h in d.new_copied_hashes:
            continue
        sym = s["symbol"]
        if s["tx_type"] == "buy" and follow_buys:
            price = prices.get(sym)
            if not price or price <= 0:
                d.notes.append(f"defer buy {sym}: no price")
                continue
            qty = max(1, int(per_trade_notional // price))
            d.buys.append(BuyOrder(symbol=sym, qty=qty, reason=f"copy: {s.get('politician','')} buy"))
            d.new_copied_hashes.append(h)
            d.notes.append(f"copy buy {qty} {sym}")
        elif s["tx_type"] == "sell" and follow_sells:
            held_qty = held.get(sym, 0)
            if held_qty and held_qty > 0:
                d.sells.append(SellOrder(symbol=sym, qty=held_qty, reason=f"copy: {s.get('politician','')} sell"))
                d.new_copied_hashes.append(h)
                d.notes.append(f"copy sell {held_qty} {sym}")
            else:
                # nothing to sell; mark copied so we don't reconsider a sell of an unowned name
                d.new_copied_hashes.append(h)
    return d
```

- [ ] **Step 4: Run** — expect PASS (4 tests).

- [ ] **Step 5: Commit** — SKIP.

---

## Task 5: Copy-trade bot runner

**Files:** Create `backend/app/services/copy_runner.py`; test `backend/tests/test_copy_runner.py`

"Already copied" hashes for a bot = the set of `ActivityLog` rows with that `bot_id` and `event == "copied"` (one logged per acted signal). The runner reads recent `Signal` rows for the followed politician, fetches quotes for their symbols, runs the engine, places each order via `place_order`, and logs `event="copied"` (detail.hash) for each acted signal + a `tick` summary.

- [ ] **Step 1: Failing test `backend/tests/test_copy_runner.py`**
```python
from app.brokers.fake import FakeBroker
from app.services.copy_runner import run_copy_trade_tick


class _Act:
    def __init__(self, event, detail):
        self.event = event
        self.detail = detail


class _DB:
    def __init__(self, signals, copied):
        self._signals = signals
        self._copied = copied
        self.added = []
        self.committed = 0

    def add(self, o):
        self.added.append(o)

    def commit(self):
        self.committed += 1

    def refresh(self, o):
        pass


def test_copy_runner_buys_new_signal_and_logs_copied():
    signals = [{"hash": "h1", "symbol": "NVDA", "tx_type": "buy", "politician": "M M"}]
    db = _DB(signals, copied=set())
    broker = FakeBroker(cash=100_000, quotes={"NVDA": 100.0})
    res = run_copy_trade_tick(
        db, broker, account_id=1, bot_id=7,
        config={"politician": "M M", "per_trade_notional": 1000.0,
                "follow_buys": True, "follow_sells": True},
        recent_signals=signals, copied_hashes=set(), mode="paper")
    # one NVDA buy placed (10 sh), and a "copied" log for h1
    pos = {p.symbol: p.qty for p in broker.get_positions()}
    assert pos.get("NVDA") == 10
    assert any(getattr(o, "event", None) == "copied" and o.detail.get("hash") == "h1" for o in db.added)
    assert res["actions"] == 1
```

- [ ] **Step 2: Run** — expect FAIL (no module).

- [ ] **Step 3: Create `backend/app/services/copy_runner.py`**
```python
from app.strategies.copy_trade import evaluate_copy
from app.services.orders import place_order
from app.models import ActivityLog
from app.brokers.base import BrokerError
from app.risk import RiskRejection


def run_copy_trade_tick(db, broker, *, account_id, bot_id, config, recent_signals, copied_hashes, mode="paper"):
    """Execute one copy-trade tick. `recent_signals` = list of Signal-dicts for the followed
    politician (most recent first). `copied_hashes` = set of already-acted signal hashes."""
    # quotes for the symbols in play
    prices: dict = {}
    for s in recent_signals:
        sym = s["symbol"]
        if sym in prices:
            continue
        try:
            prices[sym] = broker.get_quote(sym).price
        except BrokerError:
            pass
    held = {p.symbol: p.qty for p in broker.get_positions()}

    decision = evaluate_copy(
        signals=recent_signals, held=held, copied_hashes=set(copied_hashes), prices=prices,
        per_trade_notional=float(config.get("per_trade_notional", 1000.0)),
        follow_buys=config.get("follow_buys", True), follow_sells=config.get("follow_sells", True),
    )

    placed = 0
    for action in [*decision.buys, *decision.sells]:
        try:
            place_order(db, broker, account_id=account_id, mode=mode, action=action)
            placed += 1
        except (RiskRejection, BrokerError) as e:
            db.add(ActivityLog(bot_id=bot_id, level="warn", event="copy_action_skipped",
                               detail={"symbol": action.symbol, "reason": str(e)}))

    for h in decision.new_copied_hashes:
        db.add(ActivityLog(bot_id=bot_id, level="info", event="copied", detail={"hash": h}))

    db.add(ActivityLog(bot_id=bot_id, level="info", event="tick",
                       detail={"actions": placed, "notes": decision.notes}))
    db.commit()
    return {"actions": placed, "notes": decision.notes}
```

- [ ] **Step 4: Run** — expect PASS.

- [ ] **Step 5: Commit** — SKIP.

---

## Task 6: Scheduler integration (copy ticks + scraper job)

**Files:** Modify `backend/app/services/scheduler.py`; test add to `backend/tests/test_scheduler.py`

- [ ] **Step 1: Add failing test to `backend/tests/test_scheduler.py`** (append):
```python
def test_copied_hashes_for_bot_reads_activity_log():
    from app.services.scheduler import copied_hashes_for_bot

    class _Row:
        def __init__(self, h):
            self.detail = {"hash": h}

    class _Q:
        def filter(self, *a, **k):
            return self
        def all(self):
            return [_Row("h1"), _Row("h2")]

    class _DB:
        def query(self, *a, **k):
            return _Q()

    assert copied_hashes_for_bot(_DB(), bot_id=1) == {"h1", "h2"}
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_scheduler.py -v` — expect the new test to FAIL (no `copied_hashes_for_bot`).

- [ ] **Step 3: Edit `backend/app/services/scheduler.py`** — add `copied_hashes_for_bot` and extend `tick_all_active_bots` to handle copy_trade bots. Add this helper (top-level) and update the per-bot loop:

Add helper:
```python
def copied_hashes_for_bot(db, *, bot_id) -> set:
    from app.models import ActivityLog
    rows = (db.query(ActivityLog)
            .filter(ActivityLog.bot_id == bot_id, ActivityLog.event == "copied").all())
    return {r.detail.get("hash") for r in rows if r.detail and r.detail.get("hash")}
```

Replace the body of `tick_all_active_bots` with one that branches by `strategy_type` (keep the trailing-stop path; add copy_trade):
```python
def tick_all_active_bots():
    from app.db import get_sessionmaker
    from app.models import Bot, Position, BrokerageAccount, Signal
    from app.routers.bots import build_bot_broker
    from app.services.runner import run_trailing_stop_tick
    from app.services.copy_runner import run_copy_trade_tick

    db = get_sessionmaker()()
    try:
        bots = db.query(Bot).filter(Bot.status == "active").all()
        for bot in bots:
            account = db.get(BrokerageAccount, bot.account_id)
            if account is None:
                continue
            broker = build_bot_broker(account)
            try:
                if not should_run_tick(market_open=broker.get_clock().is_open, bot_status=bot.status):
                    continue
            except Exception:
                continue
            if bot.strategy_type == "trailing_stop":
                pos = db.query(Position).filter(Position.bot_id == bot.id).first()
                if pos is None:
                    pos = Position(bot_id=bot.id, symbol=bot.config["symbol"], qty=0, triggered_rungs=[])
                    db.add(pos); db.commit(); db.refresh(pos)
                run_trailing_stop_tick(db, broker, account_id=account.id, bot_id=bot.id,
                                       config=bot.config, position=pos, mode=account.mode)
            elif bot.strategy_type == "copy_trade":
                pol = bot.config.get("politician", "")
                q = db.query(Signal)
                if pol and pol != "auto":
                    q = q.filter(Signal.politician == pol)
                recent = [{"hash": s.hash, "symbol": s.symbol, "tx_type": s.tx_type,
                           "politician": s.politician}
                          for s in q.order_by(Signal.id.desc()).limit(50).all()]
                run_copy_trade_tick(db, broker, account_id=account.id, bot_id=bot.id,
                                    config=bot.config, recent_signals=recent,
                                    copied_hashes=copied_hashes_for_bot(db, bot_id=bot.id),
                                    mode=account.mode)
    finally:
        db.close()


def scrape_signals_job():
    """Slow cadence: pull latest Capitol Trades into the signals table."""
    from app.db import get_sessionmaker
    from app.services.signals_sync import sync_from_capitol_trades
    db = get_sessionmaker()()
    try:
        sync_from_capitol_trades(db, limit=50)
    except Exception:
        pass
    finally:
        db.close()
```

Then in `start_scheduler`, add a second job (after the existing `tick_all` job add line):
```python
    _scheduler.add_job(scrape_signals_job, "interval", seconds=3600, id="scrape_signals", replace_existing=True)
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_scheduler.py -v` — expect all pass (4 tests).

- [ ] **Step 5: Commit** — SKIP.

---

## Task 7: Bot API (copy_trade) + Signals API + run branching

**Files:** Modify `backend/app/schemas.py`, `backend/app/routers/bots.py`, `backend/app/main.py`; create `backend/app/routers/signals.py`; test `backend/tests/test_api_copy.py`

- [ ] **Step 1: Extend `backend/app/schemas.py`** — replace the `BotCreate` class with:
```python
class BotCreate(BaseModel):
    name: str
    account_id: int
    strategy_type: str = "trailing_stop"  # trailing_stop | copy_trade
    # trailing_stop fields
    symbol: str | None = None
    initial_shares: float = 10
    stop_pct: float = 0.10
    trail_pct: float = 0.05
    ladder: list[dict] = []
    # copy_trade fields
    politician: str | None = None
    per_trade_notional: float = 1000.0
    follow_buys: bool = True
    follow_sells: bool = True
    cadence_sec: int = 300
```
And append:
```python
class SignalOut(BaseModel):
    id: int
    politician: str
    symbol: str
    tx_type: str
    tx_date: str
    disclosed_date: str
    amount_range: str
    source_url: str
```

- [ ] **Step 2: Failing test `backend/tests/test_api_copy.py`**
```python
from app.models import Signal


def _acct(client):
    return client.post("/accounts", json={"label": "tc", "alpaca_key_id": "AKTEST1234",
                                          "alpaca_secret": "secretXYZ"}).json()


def _seed_signal(client, **kw):
    # insert a signal row directly via the app's test DB session
    from app.db import get_db
    gen = client.app.dependency_overrides[get_db]()
    db = next(gen)
    s = Signal(politician=kw.get("politician", "M M"), symbol=kw["symbol"],
               tx_type=kw["tx_type"], tx_date="2026-05-30", disclosed_date="2026-06-01",
               amount_range="250K–500K", source_url="u", hash=kw["hash"])
    db.add(s); db.commit()
    try:
        next(gen)
    except StopIteration:
        pass


def test_create_copy_bot_and_run(client):
    acct = _acct(client)
    _seed_signal(client, symbol="AAPL", tx_type="buy", hash="hbuy1", politician="M M")
    r = client.post("/bots", json={"name": "copy mccaul", "account_id": acct["id"],
                                   "strategy_type": "copy_trade", "politician": "M M",
                                   "per_trade_notional": 500})
    assert r.status_code == 200, r.text
    bot = r.json()
    assert bot["strategy_type"] == "copy_trade"
    rt = client.post(f"/bots/{bot['id']}/run")
    assert rt.status_code == 200, rt.text
    assert rt.json()["actions"] >= 1


def test_signals_endpoint_lists(client):
    _seed_signal(client, symbol="NVDA", tx_type="buy", hash="hn1", politician="M M")
    r = client.get("/signals")
    assert r.status_code == 200
    assert any(s["symbol"] == "NVDA" for s in r.json())
```

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_api_copy.py -v` — expect FAIL.

- [ ] **Step 4: Edit `backend/app/routers/bots.py`** — generalize `create_bot` to branch on `strategy_type`, and `run_bot` to dispatch to the right runner. Replace the `create_bot` function and the `run_bot` function with:
```python
@router.post("", response_model=BotOut)
def create_bot(body: BotCreate, db: Session = Depends(get_db)):
    if db.get(BrokerageAccount, body.account_id) is None:
        raise HTTPException(404, "account not found")
    if body.strategy_type == "copy_trade":
        config = {"politician": body.politician or "auto",
                  "per_trade_notional": body.per_trade_notional,
                  "follow_buys": body.follow_buys, "follow_sells": body.follow_sells}
    else:
        if not body.symbol:
            raise HTTPException(422, "symbol required for trailing_stop")
        config = {"symbol": body.symbol.upper(), "initial_shares": body.initial_shares,
                  "stop_pct": body.stop_pct, "trail_pct": body.trail_pct, "ladder": body.ladder}
    bot = Bot(name=body.name, account_id=body.account_id, strategy_type=body.strategy_type,
              status="active", config=config, schedule_cadence_sec=body.cadence_sec)
    db.add(bot); db.commit(); db.refresh(bot)
    return _bot_out(bot)
```
and replace `run_bot`:
```python
@router.post("/{bot_id}/run")
def run_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404, "bot not found")
    account = db.get(BrokerageAccount, bot.account_id)
    broker = get_broker_for_account(account)
    if bot.strategy_type == "copy_trade":
        from app.services.copy_runner import run_copy_trade_tick
        from app.services.scheduler import copied_hashes_for_bot
        from app.models import Signal
        pol = bot.config.get("politician", "")
        q = db.query(Signal)
        if pol and pol != "auto":
            q = q.filter(Signal.politician == pol)
        recent = [{"hash": s.hash, "symbol": s.symbol, "tx_type": s.tx_type, "politician": s.politician}
                  for s in q.order_by(Signal.id.desc()).limit(50).all()]
        return run_copy_trade_tick(db, broker, account_id=account.id, bot_id=bot_id,
                                   config=bot.config, recent_signals=recent,
                                   copied_hashes=copied_hashes_for_bot(db, bot_id=bot_id),
                                   mode=account.mode)
    from app.services.runner import run_trailing_stop_tick
    pos = db.query(Position).filter(Position.bot_id == bot_id).first()
    if pos is None:
        pos = Position(bot_id=bot_id, symbol=bot.config["symbol"], qty=0, triggered_rungs=[])
        db.add(pos); db.commit(); db.refresh(pos)
    return run_trailing_stop_tick(db, broker, account_id=account.id, bot_id=bot_id,
                                  config=bot.config, position=pos, mode=account.mode)
```

- [ ] **Step 5: Create `backend/app/routers/signals.py`**
```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Signal
from app.schemas import SignalOut

router = APIRouter(prefix="/signals")


@router.get("", response_model=list[SignalOut])
def list_signals(politician: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Signal)
    if politician:
        q = q.filter(Signal.politician == politician)
    rows = q.order_by(Signal.id.desc()).limit(limit).all()
    return [SignalOut(id=r.id, politician=r.politician, symbol=r.symbol, tx_type=r.tx_type,
                      tx_date=r.tx_date, disclosed_date=r.disclosed_date,
                      amount_range=r.amount_range, source_url=r.source_url) for r in rows]
```

- [ ] **Step 6: Wire signals router in `backend/app/main.py`** — add `signals` to the import line `from app.routers import health, accounts, orders, positions, bots, signals` and add `app.include_router(signals.router)`.

- [ ] **Step 7: Run** `.venv/bin/pytest tests/test_api_copy.py -v` (expect 2 passed) then the FULL suite `.venv/bin/pytest -q` (expect everything green: Phase 1+2+3). If the copy `/bots/{id}/run` test fails because the FakeBroker lacks a quote for the seeded symbol, note that conftest's FakeBroker has AAPL=100 & TSLA=250 — the test seeds an **AAPL** buy signal so the quote exists. Keep test symbols within {AAPL, TSLA} unless you also extend the conftest FakeBroker quotes (allowed test-harness change if needed; report it).

- [ ] **Step 8: Commit** — SKIP.

---

## Task 8: Frontend — Signals screen + copy-trade bot

**Files:** Modify `frontend/src/lib/api.ts`, `frontend/src/components/CreateBotForm.tsx`, `frontend/src/app/page.tsx`; create `frontend/src/app/signals/page.tsx`, `frontend/src/components/SignalsTable.tsx`

> **Design:** match `design/fey-mockup.html` — the **Signals screener** (filter chips, dense table: Politician / Ticker / Action BUY↗ green · SELL↘ red / Est. size / Tx date / Disclosed / Status, with `.st` pills) and the screener header. Reuse ported tokens. Dark theme.

- [ ] **Step 1: Add to `frontend/src/lib/api.ts`**:
```typescript
export type Signal = { id: number; politician: string; symbol: string; tx_type: string; tx_date: string; disclosed_date: string; amount_range: string; source_url: string };

export async function listSignals(politician?: string): Promise<Signal[]> {
  const url = new URL(`${BASE}/signals`);
  if (politician) url.searchParams.set("politician", politician);
  const r = await fetch(url.toString(), { cache: "no-store" });
  if (!r.ok) throw new Error("signals failed");
  return r.json();
}
```
(`BASE` = the existing module-level API base constant.)

- [ ] **Step 2: Create `frontend/src/components/SignalsTable.tsx`** — a client component rendering `Signal[]` in the mockup's screener `.tcard`/table style: circular politician initials avatar, ticker, an `.act`/`.st` BUY (green ↗) / SELL (red ↘) cell, est. size, tx date, disclosed date. Loading/empty/error states. Port the `.act`/`.st`/`.filter`/`.chipval` styles from the mockup into `globals.css` if not already present.

- [ ] **Step 3: Create `frontend/src/app/signals/page.tsx`** — the Signals screen: a screener header ("Signals · Capitol Trades · congressional disclosures") + `SignalsTable` (fetches `listSignals()`), plus filter chips (Politician / Action) that are at minimum visual, ideally wired to re-filter client-side. Match the mockup.

- [ ] **Step 4: Extend `frontend/src/components/CreateBotForm.tsx`** — add a strategy selector (Trailing-stop | Copy-trade). When Copy-trade is chosen, show politician + per_trade_notional inputs instead of symbol/stop/trail, and POST `{strategy_type:"copy_trade", politician, per_trade_notional, account_id, name}` via `createBot` (extend the `createBot` input type in api.ts to accept these optional fields + `strategy_type`).

- [ ] **Step 5: Modify `frontend/src/app/page.tsx`** — add a nav link / button to `/signals` (the nav already lists "Signals" — make it route to `/signals`).

- [ ] **Step 6: Build & verify** — `cd /Users/parag.singh/Desktop/Fey/frontend && npm run build` — MUST compile with no type errors.

- [ ] **Step 7: Commit** — SKIP.

---

## Self-Review

**Spec coverage (Phase 3 / copy-trade):**
- Capitol Trades scrape → Task 2 (`fetch_capitol_trades`) + Task 6 scraper job; pure parse fixture-tested ✓
- `signals` table → Task 1 ✓
- Sync/upsert dedup by hash → Task 3 ✓
- Pure copy engine (new-signal buy, sell-held, dedupe) → Task 4 ✓
- Copy runner through risk chokepoint + order service → Task 5 (reuses `place_order`) ✓
- Scheduler runs copy bots + slow scraper → Task 6 ✓
- Copy-trade bot creation + run + signals API → Task 7 ✓
- Signals screener UI + copy-bot creation → Task 8 ✓
- "auto-pick politician" → represented by `politician="auto"` (no DB filter → all recent signals); a smarter ranking is a later refinement (flagged, not silently dropped).

**Placeholder scan:** No TBD/TODO; backend steps have full code. UI steps (Task 8 Steps 2–5) describe components with explicit props/data/styles, deferring exact JSX to the mockup (consistent with Phases 1–2).

**Type consistency:** Signal-dict shape `{politician,symbol,tx_type,tx_date,disclosed_date,amount_range,source_url,hash}` is produced by `parse_capitol_trades` (Task 2), consumed by `upsert_signals` (Task 3) and the runners. `evaluate_copy(signals, held, copied_hashes, prices, per_trade_notional, follow_buys, follow_sells)` matches Task 4 tests and Task 5 caller. `copied_hashes_for_bot(db, bot_id=...)` defined in scheduler (Task 6) and reused by `run_bot` (Task 7). `run_copy_trade_tick(...)` signature matches Task 5 tests, scheduler (Task 6), and run_bot (Task 7). `BotCreate.strategy_type` branching consistent across create + run. FakeBroker quote symbols (AAPL/TSLA) noted for Task 7 tests.

**Known follow-ups (flagged):** live Capitol Trades fetch shape must be verified against the site during Task 2 (mapping-only risk; parse/hash stable); "auto" politician selection is naive; scraper has no retry/backoff beyond swallow-and-continue; copied-hash tracking via ActivityLog is O(rows-per-bot) but fine at Phase-3 scale.
