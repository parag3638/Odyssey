# Odyssey Phase 2 — Trailing-Stop Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **No git in this project — skip all commit steps.** Run backend tests with `cd /Users/parag.singh/Desktop/Codes/Personal_Play/Odyssey/backend && .venv/bin/pytest <args> -v`.

**Goal:** Add an automated trailing-stop strategy: a deterministic pure engine (raise-only stop floor + ladder buys), the `bots`/`positions` tables, a per-bot tick runner that routes engine actions through the existing risk chokepoint + order service, an always-on market-hours scheduler, a bot REST API, and a bot-detail UI. Also implements Alpaca market-data quotes (closing the Phase-1 gap so live orders can be priced).

**Architecture:** Builds on the Phase-1 backend (`backend/app/...`). New pure module `app/strategies/trailing_stop.py` (no I/O). New persistence: `Bot` + `Position` models + Alembic migration. New `app/services/runner.py` orchestrates one bot tick (fetch state+quote → engine → per-action risk+submit via the existing `place_order` → persist position state + activity log). APScheduler runs active bots on their cadence during US market hours (Alpaca clock). New `app/routers/bots.py`. Frontend gains a bot-detail screen + create-bot form, built to `design/odyssey-mockup.html`.

**Tech Stack:** Existing stack + `apscheduler` (new dep) + alpaca-py market-data (`StockHistoricalDataClient`) + Alpaca trading clock.

---

## File Structure

Backend (new/modified under `backend/`):
- `app/brokers/base.py` — MODIFY: add `AdjustStop` action + `Clock` dataclass + extend `Broker` protocol with `get_clock()`
- `app/brokers/fake.py` — MODIFY: add `get_clock()`, allow setting quotes/market-open
- `app/brokers/alpaca.py` — MODIFY: implement `get_quote()` (StockHistoricalDataClient latest trade) + `get_clock()` (TradingClient.get_clock)
- `app/strategies/__init__.py`, `app/strategies/trailing_stop.py` — pure engine
- `app/models.py` — MODIFY: add `Bot`, `Position` models
- `alembic/versions/*` — new migration "bots and positions"
- `app/services/runner.py` — per-bot tick orchestration
- `app/services/scheduler.py` — APScheduler setup + market-hours guard
- `app/routers/bots.py` — bot CRUD + run-tick + detail
- `app/main.py` — MODIFY: include bots router + start scheduler on startup
- `app/schemas.py` — MODIFY: add bot schemas
- Tests: `tests/test_trailing_stop.py`, `tests/test_runner.py`, `tests/test_alpaca_quote.py`, `tests/test_api_bots.py`

Frontend (under `frontend/src/`):
- `lib/api.ts` — MODIFY: add bot endpoints
- `components/BotStatus.tsx`, `components/CreateBotForm.tsx`
- `app/bots/[id]/page.tsx` — bot detail screen (matches mockup Position-detail/bot panel)

---

## Task 1: Alpaca market-data quote + clock (closes Phase-1 gap)

**Files:**
- Modify: `backend/app/brokers/base.py`, `backend/app/brokers/alpaca.py`, `backend/app/brokers/fake.py`
- Test: `backend/tests/test_alpaca_quote.py`

- [ ] **Step 1: Add `AdjustStop` + `Clock` to `app/brokers/base.py`** (append after the `Quote` dataclass; add `get_clock` to the protocol):

```python
@dataclass(frozen=True)
class AdjustStop:
    symbol: str
    new_floor: float
    reason: str = ""


@dataclass(frozen=True)
class Clock:
    is_open: bool
```

Then in the `Broker` Protocol add this method signature line:
```python
    def get_clock(self) -> Clock: ...
```

- [ ] **Step 2: Write failing test `backend/tests/test_alpaca_quote.py`**

```python
from app.brokers.base import Quote, Clock
from app.brokers.alpaca import AlpacaBroker


class _Trade:
    price = 142.5


class _StubData:
    def get_stock_latest_trade(self, req):
        return {"AAPL": _Trade()}


class _StubClock:
    is_open = True


class _StubTrading:
    def get_clock(self):
        return _StubClock()


def test_get_quote_uses_latest_trade():
    b = AlpacaBroker(client=_StubTrading(), data_client=_StubData())
    q = b.get_quote("AAPL")
    assert q == Quote(symbol="AAPL", price=142.5)


def test_get_clock_maps_is_open():
    b = AlpacaBroker(client=_StubTrading(), data_client=_StubData())
    assert b.get_clock() == Clock(is_open=True)
```

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_alpaca_quote.py -v` — expect FAIL (`AlpacaBroker.__init__` has no `data_client`; `get_quote` raises NotImplementedError).

- [ ] **Step 4: Implement in `backend/app/brokers/alpaca.py`** — replace the whole file with:

```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from app.brokers.base import (
    Action, BuyOrder, OrderResult, Position, Quote, Clock, BrokerError,
)

_STATUS_MAP = {
    "filled": "filled",
    "accepted": "pending",
    "new": "pending",
    "pending_new": "pending",
    "rejected": "rejected",
    "canceled": "rejected",
}


class AlpacaBroker:
    def __init__(self, client=None, data_client=None, key_id: str = "", secret: str = "", paper: bool = True):
        self._client = client or TradingClient(key_id, secret, paper=paper)
        self._data = data_client or (StockHistoricalDataClient(key_id, secret) if key_id else None)

    def submit(self, action: Action) -> OrderResult:
        side = OrderSide.BUY if isinstance(action, BuyOrder) else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=action.symbol, qty=action.qty, side=side,
            time_in_force=TimeInForce.DAY,
        )
        try:
            o = self._client.submit_order(order_data=req)
        except Exception as e:
            raise BrokerError(str(e)) from e
        filled = float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else None
        return OrderResult(
            status=_STATUS_MAP.get(str(o.status).split(".")[-1].lower(), "pending"),
            filled_price=filled,
            broker_order_id=str(o.id),
        )

    def get_positions(self) -> list[Position]:
        return [
            Position(p.symbol, float(p.qty), float(p.avg_entry_price))
            for p in self._client.get_all_positions()
        ]

    def get_quote(self, symbol: str) -> Quote:
        if self._data is None:
            raise BrokerError("no market-data client configured")
        try:
            resp = self._data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
            trade = resp[symbol]
            return Quote(symbol=symbol, price=float(trade.price))
        except BrokerError:
            raise
        except Exception as e:
            raise BrokerError(f"quote failed for {symbol}: {e}") from e

    def get_clock(self) -> Clock:
        try:
            c = self._client.get_clock()
            return Clock(is_open=bool(c.is_open))
        except Exception as e:
            raise BrokerError(str(e)) from e

    def get_cash(self) -> float:
        return float(self._client.get_account().cash)
```

Note: the test's `_StubData.get_stock_latest_trade` ignores its arg and returns a dict keyed by symbol; the impl builds a `StockLatestTradeRequest` (real alpaca-py class) and indexes the response by symbol — matches both the stub and the real SDK (which returns a dict-like keyed by symbol with `.price`).

- [ ] **Step 5: Add `get_clock` to `backend/app/brokers/fake.py`** — add this method to `FakeBroker` and a `market_open` ctor param:

Change the `__init__` signature to:
```python
    def __init__(self, cash: float = 100_000.0, quotes: dict[str, float] | None = None, market_open: bool = True):
        self._cash = cash
        self._quotes = quotes or {}
        self._positions: dict[str, Position] = {}
        self._counter = 0
        self._market_open = market_open
```
Add import `Clock` to the existing `from app.brokers.base import (...)` line, and add this method:
```python
    def get_clock(self) -> Clock:
        return Clock(is_open=self._market_open)

    def set_quote(self, symbol: str, price: float) -> None:
        self._quotes[symbol] = price
```

- [ ] **Step 6: Run** `.venv/bin/pytest tests/test_alpaca_quote.py tests/test_fake_broker.py -v` — expect PASS (2 new + 2 existing).

- [ ] **Step 7: Commit** — SKIP.

---

## Task 2: Add `apscheduler` dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add to `[project].dependencies`** in `backend/pyproject.toml` the line `"apscheduler>=3.10",` (after the `"alpaca-py>=0.33",` line).

- [ ] **Step 2: Install** — run `cd /Users/parag.singh/Desktop/Codes/Personal_Play/Odyssey/backend && .venv/bin/pip install "apscheduler>=3.10"`. Expect success.

- [ ] **Step 3: Verify** — `cd /Users/parag.singh/Desktop/Codes/Personal_Play/Odyssey/backend && .venv/bin/python -c "import apscheduler; print('ok')"` → `ok`.

- [ ] **Step 4: Commit** — SKIP.

---

## Task 3: `Bot` and `Position` models + migration

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_models_phase2.py`

- [ ] **Step 1: Write failing test `backend/tests/test_models_phase2.py`**

```python
from app.models import Bot, Position


def test_bot_and_position_columns():
    assert {"id", "name", "account_id", "strategy_type", "status",
            "config", "schedule_cadence_sec", "created_at"} <= set(Bot.__table__.columns.keys())
    assert {"id", "bot_id", "symbol", "qty", "avg_entry_price",
            "stop_floor", "triggered_rungs", "updated_at"} <= set(Position.__table__.columns.keys())
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_models_phase2.py -v` — expect FAIL (ImportError: cannot import name 'Bot').

- [ ] **Step 3: Append to `backend/app/models.py`** (after `ActivityLog`):

```python
class Bot(Base):
    __tablename__ = "bots"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[int] = mapped_column(ForeignKey("brokerage_accounts.id"))
    strategy_type: Mapped[str] = mapped_column(String(20))  # trailing_stop | copy_trade
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | paused | stopped
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule_cadence_sec: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"))
    symbol: Mapped[str] = mapped_column(String(10))
    qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    avg_entry_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    stop_floor: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    triggered_rungs: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_models_phase2.py -v` — expect PASS.

- [ ] **Step 5: Generate + apply migration** — run:
```
cd /Users/parag.singh/Desktop/Codes/Personal_Play/Odyssey/backend && .venv/bin/alembic revision --autogenerate -m "bots and positions" && .venv/bin/alembic upgrade head
```
Verify: `/opt/homebrew/opt/postgresql@16/bin/psql "postgresql://fey:fey@localhost:5432/fey" -c "\dt"` shows `bots` and `positions`.

- [ ] **Step 6: Commit** — SKIP.

---

## Task 4: Trailing-stop pure engine

**Files:**
- Create: `backend/app/strategies/__init__.py`, `backend/app/strategies/trailing_stop.py`
- Test: `backend/tests/test_trailing_stop.py`

- [ ] **Step 1: Write failing test `backend/tests/test_trailing_stop.py`**

```python
from app.strategies.trailing_stop import evaluate, TrailingState

CFG = {"symbol": "TSLA", "initial_shares": 10, "stop_pct": 0.10, "trail_pct": 0.05,
       "ladder": [{"drop_pct": 0.20, "add_shares": 20}]}


def test_no_position_buys_initial_and_sets_floor():
    d = evaluate(CFG, TrailingState(qty=0, avg_entry_price=None, stop_floor=None, triggered_rungs=[]), price=100.0)
    assert len(d.buys) == 1 and d.buys[0].qty == 10 and d.buys[0].symbol == "TSLA"
    assert not d.sells
    assert round(d.new_stop_floor, 2) == 90.0  # 100 * (1 - stop_pct)


def test_floor_rises_but_never_falls():
    # price climbed to 200; trail floor = 190 which is above current floor 90
    d = evaluate(CFG, TrailingState(qty=10, avg_entry_price=100.0, stop_floor=90.0, triggered_rungs=[]), price=200.0)
    assert round(d.new_stop_floor, 2) == 190.0
    assert not d.sells and not d.buys
    # now price dips to 180; floor must NOT fall below 190
    d2 = evaluate(CFG, TrailingState(qty=10, avg_entry_price=100.0, stop_floor=190.0, triggered_rungs=[]), price=180.0)
    assert d2.new_stop_floor == 190.0
    assert len(d2.sells) == 1  # 180 <= 190 -> stop hit -> sell all


def test_stop_hit_sells_all():
    d = evaluate(CFG, TrailingState(qty=10, avg_entry_price=100.0, stop_floor=95.0, triggered_rungs=[]), price=94.0)
    assert len(d.sells) == 1 and d.sells[0].qty == 10


def test_ladder_buy_fires_once():
    # price dropped 20% below entry (100 -> 80); rung not yet triggered
    state = TrailingState(qty=10, avg_entry_price=100.0, stop_floor=70.0, triggered_rungs=[])
    d = evaluate(CFG, state, price=80.0)
    assert len(d.buys) == 1 and d.buys[0].qty == 20
    assert 0.20 in d.new_triggered_rungs
    # same rung already triggered -> no second ladder buy
    state2 = TrailingState(qty=30, avg_entry_price=93.0, stop_floor=70.0, triggered_rungs=[0.20])
    d2 = evaluate(CFG, state2, price=80.0)
    assert not d2.buys
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_trailing_stop.py -v` — expect FAIL (No module named 'app.strategies').

- [ ] **Step 3: Create `backend/app/strategies/__init__.py`** (empty) and `backend/app/strategies/trailing_stop.py`:

```python
from dataclasses import dataclass, field
from app.brokers.base import BuyOrder, SellOrder


@dataclass
class TrailingState:
    qty: float
    avg_entry_price: float | None
    stop_floor: float | None
    triggered_rungs: list = field(default_factory=list)


@dataclass
class TrailingDecision:
    buys: list = field(default_factory=list)
    sells: list = field(default_factory=list)
    new_stop_floor: float | None = None
    new_triggered_rungs: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def evaluate(config: dict, state: TrailingState, price: float) -> TrailingDecision:
    """Pure trailing-stop logic. No I/O. Returns intended buys/sells + updated state."""
    symbol = config["symbol"]
    stop_pct = config.get("stop_pct", 0.10)
    trail_pct = config.get("trail_pct", 0.05)
    d = TrailingDecision(new_triggered_rungs=list(state.triggered_rungs))

    # No position yet -> open it.
    if state.qty <= 0:
        d.buys.append(BuyOrder(symbol=symbol, qty=config["initial_shares"], reason="trailing: initial entry"))
        d.new_stop_floor = round(price * (1 - stop_pct), 4)
        d.notes.append(f"opened position; floor {d.new_stop_floor}")
        return d

    entry = state.avg_entry_price or price
    base_floor = entry * (1 - stop_pct)
    current_floor = state.stop_floor if state.stop_floor is not None else base_floor
    trail_floor = price * (1 - trail_pct)
    # floor only ever rises
    new_floor = max(current_floor, trail_floor)
    d.new_stop_floor = round(new_floor, 4)
    if new_floor > current_floor:
        d.notes.append(f"raised floor to {d.new_stop_floor}")

    # stop hit -> exit fully
    if price <= d.new_stop_floor:
        d.sells.append(SellOrder(symbol=symbol, qty=state.qty, reason="trailing: stop hit"))
        d.notes.append("stop hit; selling all")
        return d

    # ladder buys (each rung fires at most once)
    for rung in config.get("ladder", []):
        drop = rung["drop_pct"]
        if drop in d.new_triggered_rungs:
            continue
        if price <= entry * (1 - drop):
            d.buys.append(BuyOrder(symbol=symbol, qty=rung["add_shares"],
                                   reason=f"trailing: ladder -{int(drop*100)}%"))
            d.new_triggered_rungs.append(drop)
            d.notes.append(f"ladder buy {rung['add_shares']} @ -{int(drop*100)}%")
    return d
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_trailing_stop.py -v` — expect PASS (4 tests).

- [ ] **Step 5: Commit** — SKIP.

---

## Task 5: Bot tick runner

**Files:**
- Create: `backend/app/services/runner.py`
- Test: `backend/tests/test_runner.py`

The runner executes ONE tick for one trailing-stop bot: load/seed its `Position`, fetch the quote, run the engine, route each buy/sell through the existing `place_order` (which enforces risk + submits + persists an `Order` + logs), then persist the updated `stop_floor`/`triggered_rungs` and reconcile `qty`/`avg_entry_price`, logging an `AdjustStop`-style activity entry.

- [ ] **Step 1: Write failing test `backend/tests/test_runner.py`**

```python
import pytest
from app.brokers.fake import FakeBroker
from app.strategies.trailing_stop import TrailingState
from app.services.runner import run_trailing_stop_tick


class _Pos:
    def __init__(self):
        self.qty = 0
        self.avg_entry_price = None
        self.stop_floor = None
        self.triggered_rungs = []


class _DB:
    def __init__(self, pos):
        self._pos = pos
        self.committed = 0
        self.added = []

    def add(self, o):
        self.added.append(o)

    def commit(self):
        self.committed += 1

    def refresh(self, o):
        pass


CFG = {"symbol": "TSLA", "initial_shares": 10, "stop_pct": 0.10, "trail_pct": 0.05, "ladder": []}


def test_tick_opens_position_and_records_floor():
    pos = _Pos()
    db = _DB(pos)
    broker = FakeBroker(cash=100_000, quotes={"TSLA": 250.0})
    result = run_trailing_stop_tick(db, broker, account_id=1, bot_id=1, config=CFG, position=pos, mode="paper")
    # engine wanted an initial buy of 10; fake broker fills it
    assert pos.qty == 10
    assert round(float(pos.stop_floor), 2) == 225.0  # 250 * 0.9
    assert result["actions"] >= 1
    assert db.committed >= 1
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_runner.py -v` — expect FAIL (No module named 'app.services.runner').

- [ ] **Step 3: Create `backend/app/services/runner.py`**

```python
from app.strategies.trailing_stop import evaluate, TrailingState
from app.services.orders import place_order
from app.models import ActivityLog
from app.brokers.base import BrokerError
from app.risk import RiskRejection


def run_trailing_stop_tick(db, broker, *, account_id, bot_id, config, position, mode="paper"):
    """Execute one trailing-stop tick. `position` is a Position ORM row (or stub) with
    qty/avg_entry_price/stop_floor/triggered_rungs attributes. Mutates it in place + commits."""
    symbol = config["symbol"]
    quote = broker.get_quote(symbol)
    state = TrailingState(
        qty=float(position.qty or 0),
        avg_entry_price=float(position.avg_entry_price) if position.avg_entry_price is not None else None,
        stop_floor=float(position.stop_floor) if position.stop_floor is not None else None,
        triggered_rungs=list(position.triggered_rungs or []),
    )
    decision = evaluate(config, state, quote.price)

    placed = 0
    for action in [*decision.buys, *decision.sells]:
        try:
            place_order(db, broker, account_id=account_id, mode=mode, action=action)
            placed += 1
        except (RiskRejection, BrokerError) as e:
            db.add(ActivityLog(bot_id=bot_id, level="warn", event="tick_action_skipped",
                               detail={"symbol": symbol, "reason": str(e)}))

    # reconcile position qty/avg from the broker (source of truth)
    by_symbol = {p.symbol: p for p in broker.get_positions()}
    bp = by_symbol.get(symbol)
    position.qty = bp.qty if bp else 0
    position.avg_entry_price = bp.avg_entry_price if bp else None
    if decision.new_stop_floor is not None and (bp is not None):
        position.stop_floor = decision.new_stop_floor
    position.triggered_rungs = decision.new_triggered_rungs

    db.add(ActivityLog(bot_id=bot_id, level="info", event="tick",
                       detail={"symbol": symbol, "price": quote.price,
                               "stop_floor": decision.new_stop_floor,
                               "notes": decision.notes, "actions": placed}))
    db.commit()
    return {"price": quote.price, "actions": placed, "stop_floor": decision.new_stop_floor,
            "notes": decision.notes}
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_runner.py -v` — expect PASS.

- [ ] **Step 5: Commit** — SKIP.

---

## Task 6: Scheduler (market-hours, per-bot cadence)

**Files:**
- Create: `backend/app/services/scheduler.py`
- Test: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Write failing test `backend/tests/test_scheduler.py`**

```python
from app.services.scheduler import should_run_tick


def test_skips_when_market_closed():
    assert should_run_tick(market_open=False, bot_status="active") is False


def test_skips_when_paused():
    assert should_run_tick(market_open=True, bot_status="paused") is False


def test_runs_when_open_and_active():
    assert should_run_tick(market_open=True, bot_status="active") is True
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_scheduler.py -v` — expect FAIL.

- [ ] **Step 3: Create `backend/app/services/scheduler.py`**

```python
from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: BackgroundScheduler | None = None


def should_run_tick(*, market_open: bool, bot_status: str) -> bool:
    return bool(market_open) and bot_status == "active"


def tick_all_active_bots():
    """Run one tick for every active trailing-stop bot. Imported lazily to avoid
    circulars and to read fresh DB state each run."""
    from app.db import get_sessionmaker
    from app.models import Bot, Position, BrokerageAccount
    from app.routers.bots import build_bot_broker
    from app.services.runner import run_trailing_stop_tick

    db = get_sessionmaker()()
    try:
        bots = db.query(Bot).filter(Bot.strategy_type == "trailing_stop",
                                    Bot.status == "active").all()
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
            pos = (db.query(Position).filter(Position.bot_id == bot.id).first())
            if pos is None:
                pos = Position(bot_id=bot.id, symbol=bot.config["symbol"], qty=0, triggered_rungs=[])
                db.add(pos)
                db.commit()
                db.refresh(pos)
            run_trailing_stop_tick(db, broker, account_id=account.id, bot_id=bot.id,
                                   config=bot.config, position=pos, mode=account.mode)
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(tick_all_active_bots, "interval", seconds=300, id="tick_all", replace_existing=True)
    _scheduler.start()
    return _scheduler
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_scheduler.py -v` — expect PASS (3 tests).

- [ ] **Step 5: Commit** — SKIP.

---

## Task 7: Bot REST API + schemas + app wiring

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/main.py`
- Create: `backend/app/routers/bots.py`
- Test: `backend/tests/test_api_bots.py`

- [ ] **Step 1: Add schemas to `backend/app/schemas.py`** (append):

```python
class BotCreate(BaseModel):
    name: str
    account_id: int
    symbol: str
    initial_shares: float = 10
    stop_pct: float = 0.10
    trail_pct: float = 0.05
    ladder: list[dict] = []
    cadence_sec: int = 300


class BotOut(BaseModel):
    id: int
    name: str
    strategy_type: str
    status: str
    config: dict
    schedule_cadence_sec: int


class BotDetail(BotOut):
    position: dict | None = None
    recent_activity: list[dict] = []
```

- [ ] **Step 2: Write failing test `backend/tests/test_api_bots.py`**

```python
def _make_account(client):
    r = client.post("/accounts", json={"label": "tc", "alpaca_key_id": "AKTEST1234",
                                       "alpaca_secret": "secretXYZ"})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_and_run_trailing_bot(client):
    acct = _make_account(client)
    r = client.post("/bots", json={"name": "TSLA trail", "account_id": acct["id"],
                                   "symbol": "TSLA", "initial_shares": 10})
    assert r.status_code == 200, r.text
    bot = r.json()
    assert bot["strategy_type"] == "trailing_stop"
    assert bot["status"] == "active"

    # run one tick now (uses the injected FakeBroker with a TSLA quote)
    rt = client.post(f"/bots/{bot['id']}/run")
    assert rt.status_code == 200, rt.text
    assert rt.json()["actions"] >= 1

    # detail reflects the opened position
    rd = client.get(f"/bots/{bot['id']}")
    assert rd.status_code == 200
    body = rd.json()
    assert body["position"]["qty"] == 10
    assert float(body["position"]["stop_floor"]) > 0


def test_pause_bot(client):
    acct = _make_account(client)
    bot = client.post("/bots", json={"name": "x", "account_id": acct["id"], "symbol": "TSLA"}).json()
    r = client.patch(f"/bots/{bot['id']}", json={"status": "paused"})
    assert r.status_code == 200
    assert r.json()["status"] == "paused"
```

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_api_bots.py -v` — expect FAIL (no /bots).

- [ ] **Step 4: Create `backend/app/routers/bots.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import Bot, Position, ActivityLog, BrokerageAccount
from app.brokers.alpaca import AlpacaBroker
from app.services.runner import run_trailing_stop_tick
from app.schemas import BotCreate, BotOut, BotDetail

router = APIRouter(prefix="/bots")


def build_bot_broker(account: BrokerageAccount):
    secret = decrypt_secret(account.alpaca_secret, get_settings().fernet_key)
    return AlpacaBroker(key_id=account.alpaca_key_id, secret=secret, paper=True)


# Overridable seam for tests:
def get_broker_for_account(account: BrokerageAccount):
    return build_bot_broker(account)


def _bot_out(bot: Bot) -> BotOut:
    return BotOut(id=bot.id, name=bot.name, strategy_type=bot.strategy_type,
                  status=bot.status, config=bot.config, schedule_cadence_sec=bot.schedule_cadence_sec)


@router.post("", response_model=BotOut)
def create_bot(body: BotCreate, db: Session = Depends(get_db)):
    if db.get(BrokerageAccount, body.account_id) is None:
        raise HTTPException(404, "account not found")
    config = {"symbol": body.symbol.upper(), "initial_shares": body.initial_shares,
              "stop_pct": body.stop_pct, "trail_pct": body.trail_pct, "ladder": body.ladder}
    bot = Bot(name=body.name, account_id=body.account_id, strategy_type="trailing_stop",
              status="active", config=config, schedule_cadence_sec=body.cadence_sec)
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return _bot_out(bot)


@router.get("", response_model=list[BotOut])
def list_bots(db: Session = Depends(get_db)):
    return [_bot_out(b) for b in db.query(Bot).all()]


@router.get("/{bot_id}", response_model=BotDetail)
def get_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404, "bot not found")
    pos = db.query(Position).filter(Position.bot_id == bot_id).first()
    acts = (db.query(ActivityLog).filter(ActivityLog.bot_id == bot_id)
            .order_by(ActivityLog.id.desc()).limit(10).all())
    pos_dict = None
    if pos is not None:
        pos_dict = {"symbol": pos.symbol, "qty": float(pos.qty or 0),
                    "avg_entry_price": float(pos.avg_entry_price) if pos.avg_entry_price is not None else None,
                    "stop_floor": float(pos.stop_floor) if pos.stop_floor is not None else None,
                    "triggered_rungs": pos.triggered_rungs or []}
    base = _bot_out(bot)
    return BotDetail(**base.model_dump(), position=pos_dict,
                     recent_activity=[{"event": a.event, "level": a.level, "detail": a.detail} for a in acts])


class _StatusPatch(BaseModel):
    status: str


@router.patch("/{bot_id}", response_model=BotOut)
def patch_bot(bot_id: int, body: _StatusPatch, db: Session = Depends(get_db)):
    bot = db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404, "bot not found")
    if body.status not in ("active", "paused", "stopped"):
        raise HTTPException(422, "invalid status")
    bot.status = body.status
    db.commit()
    db.refresh(bot)
    return _bot_out(bot)


@router.post("/{bot_id}/run")
def run_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404, "bot not found")
    account = db.get(BrokerageAccount, bot.account_id)
    broker = get_broker_for_account(account)
    pos = db.query(Position).filter(Position.bot_id == bot_id).first()
    if pos is None:
        pos = Position(bot_id=bot_id, symbol=bot.config["symbol"], qty=0, triggered_rungs=[])
        db.add(pos)
        db.commit()
        db.refresh(pos)
    return run_trailing_stop_tick(db, broker, account_id=account.id, bot_id=bot_id,
                                  config=bot.config, position=pos, mode=account.mode)
```

- [ ] **Step 5: Wire router + scheduler in `backend/app/main.py`** — change the file to:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import health, accounts, orders, positions, bots


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    if os.environ.get("ODYSSEY_DISABLE_SCHEDULER") != "1":
        from app.services.scheduler import start_scheduler
        start_scheduler()
    yield


app = FastAPI(title="Odyssey", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(orders.router)
app.include_router(positions.router)
app.include_router(bots.router)
```

- [ ] **Step 6: Update test harness** — in `backend/tests/conftest.py`, the `client` fixture must also (a) set `ODYSSEY_DISABLE_SCHEDULER=1` so APScheduler doesn't start during tests, and (b) patch the bots router's broker seam to the FakeBroker (with a TSLA quote). Add `os.environ["ODYSSEY_DISABLE_SCHEDULER"] = "1"` inside the `_env` fixture. Inside the `client` fixture, after importing, add:
```python
    from app.routers import bots as bots_router
    bots_router.get_broker_for_account = lambda account: fake
```
and ensure the FakeBroker is created with `quotes={"AAPL": 100.0, "TSLA": 250.0}` (already is).

- [ ] **Step 7: Run** `.venv/bin/pytest tests/test_api_bots.py -v` — expect PASS (2 tests). Then run the FULL suite `.venv/bin/pytest -q` — expect everything green (Phase 1 + Phase 2).

- [ ] **Step 8: Commit** — SKIP.

---

## Task 8: Frontend — bot create + detail

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/BotStatus.tsx`, `frontend/src/components/CreateBotForm.tsx`, `frontend/src/app/bots/[id]/page.tsx`
- Modify: `frontend/src/app/page.tsx` (add an "Active bots" card linking to bot detail, + a "New bot" affordance)

> **Design:** match `design/odyssey-mockup.html` — the Overview "Active bots" compact rows (`.botrow`) and the Position-detail right-panel **Trailing-stop bot status** card (status, stop floor, trail %, next ladder, action timeline). Use the existing ported tokens/components. Dark theme.

- [ ] **Step 1: Add to `frontend/src/lib/api.ts`** types + calls:

```typescript
export type Bot = { id: number; name: string; strategy_type: string; status: string; config: Record<string, unknown>; schedule_cadence_sec: number };
export type BotDetail = Bot & { position: { symbol: string; qty: number; avg_entry_price: number | null; stop_floor: number | null; triggered_rungs: number[] } | null; recent_activity: { event: string; level: string; detail: Record<string, unknown> }[] };

export async function listBots(): Promise<Bot[]> {
  const r = await fetch(`${BASE}/bots`, { cache: "no-store" });
  if (!r.ok) throw new Error("bots failed");
  return r.json();
}
export async function getBot(id: number): Promise<BotDetail> {
  const r = await fetch(`${BASE}/bots/${id}`, { cache: "no-store" });
  if (!r.ok) throw new Error("bot failed");
  return r.json();
}
export async function createBot(input: { name: string; account_id: number; symbol: string; initial_shares?: number; stop_pct?: number; trail_pct?: number; }): Promise<Bot> {
  const r = await fetch(`${BASE}/bots`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) });
  if (!r.ok) throw new Error((await r.json()).detail ?? "create bot failed");
  return r.json();
}
export async function runBot(id: number): Promise<{ price: number; actions: number; stop_floor: number | null; notes: string[] }> {
  const r = await fetch(`${BASE}/bots/${id}/run`, { method: "POST" });
  if (!r.ok) throw new Error("run failed");
  return r.json();
}
export async function setBotStatus(id: number, status: string): Promise<Bot> {
  const r = await fetch(`${BASE}/bots/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) });
  if (!r.ok) throw new Error("patch failed");
  return r.json();
}
```
(Ensure `BASE` is the module-level `NEXT_PUBLIC_API_BASE` constant already defined in `api.ts`.)

- [ ] **Step 2: Create `frontend/src/components/BotStatus.tsx`** — a client component rendering the trailing-stop status card from a `BotDetail` (status dot, stop floor, trail %, position qty, downside-to-floor, a recent-activity timeline, and a "Run tick now" button that calls `runBot` then refreshes). Use the mockup's `.rpanel`/`.botstat`/`.timeline` styles (port the class names into the component or `globals.css`). Numbers use the tabular/mono treatment. Keep it focused (<150 lines).

- [ ] **Step 3: Create `frontend/src/components/CreateBotForm.tsx`** — a client component: inputs for name, symbol, initial_shares, stop_pct, trail_pct, and an account selector (from `listAccounts()`); on submit calls `createBot` and routes to `/bots/{id}`. Match the mockup's form styling.

- [ ] **Step 4: Create `frontend/src/app/bots/[id]/page.tsx`** — bot detail screen: a back link to Overview, the ticker header, and the `BotStatus` card. Fetch via `getBot(id)` (client component reading `params.id`). Match the mockup Position-detail layout (dark, KPI-strip optional).

- [ ] **Step 5: Modify `frontend/src/app/page.tsx`** — add an "Active bots" section (compact `.botrow` rows from `listBots()`, each linking to `/bots/{id}` and showing status + symbol + P&L if available) and a "New bot" button that reveals/links to `CreateBotForm`.

- [ ] **Step 6: Build & verify** — `cd /Users/parag.singh/Desktop/Codes/Personal_Play/Odyssey/frontend && npm run build` — MUST compile with no type errors. Fix any errors.

- [ ] **Step 7: Commit** — SKIP.

---

## Self-Review

**Spec coverage (Phase 2 / trailing-stop):**
- Trailing-stop engine (raise-only floor, stop-hit sell, ladder once) → Task 4 ✓
- Bots + positions persistence → Task 3 ✓
- Per-bot tick through risk chokepoint + order service → Task 5 (reuses Phase-1 `place_order`) ✓
- Always-on market-hours scheduler → Task 6 (APScheduler + Alpaca clock via `should_run_tick`) ✓
- Bot dashboard (create + detail + status) → Task 8 ✓
- Closes Phase-1 quote gap (Alpaca market data) → Task 1 ✓
- Risk guardrails reused unchanged (no duplication) ✓
- Idempotency: orders carry `dedupe_key` (Phase-1 column) — engine actions are per-tick; the runner does not double-submit within a tick. Cross-tick dedupe for ladder rungs handled by `triggered_rungs`. (Full per-tick dedupe keys are a later hardening; noted, not silently skipped.)

**Placeholder scan:** No TBD/TODO; every backend step has full code. Frontend Steps 2–5 describe components with explicit props, data sources, styles, and size budgets but not full JSX (the design lives in `design/odyssey-mockup.html`, which the implementer ports) — acceptable for the UI layer, consistent with Phase-1 Task 10.

**Type consistency:** `evaluate(config, state, price) -> TrailingDecision` used identically in Task 4 (tests) and Task 5 (runner). `TrailingState` fields (qty/avg_entry_price/stop_floor/triggered_rungs) match the `Position` model columns (Task 3) and the runner's mapping (Task 5). `get_broker_for_account` is the injection seam in `bots.py` (Task 7) overridden in conftest (Task 7 Step 6) — same pattern as Phase-1 orders/positions. `build_bot_broker` is referenced by both `bots.py` and `scheduler.py` (Task 6) via module import. `AdjustStop`/`Clock` added in Task 1 are imported where used. Scheduler's `should_run_tick` signature matches its tests (Task 6) and its caller (Task 6 `tick_all_active_bots`).

**Known follow-ups (flagged):** live end-to-end (real fills) requires the user's Alpaca **paper** keys; the scheduler interval is a fixed 300s (per-bot cadence honored at the DB-config level is a later refinement); market-data uses Alpaca's default feed.
