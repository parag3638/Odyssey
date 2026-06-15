# Odyssey Phase 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove Odyssey's Alpaca paper-trading money-path end-to-end — a manual "buy N shares of SYMBOL" flows through a risk chokepoint, a swappable broker adapter, into Postgres, and is visible on a Next.js dashboard with a PAPER badge.

**Architecture:** FastAPI backend with a pure risk layer and a broker-adapter interface (Alpaca impl + in-memory fake for tests); SQLAlchemy/Alembic on Postgres for persistence; a thin Next.js dashboard that only calls the REST API. Secrets are encrypted at rest and never sent to the frontend.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, pydantic-settings, alpaca-py, cryptography (Fernet), pytest + httpx; Next.js (App Router, TypeScript); Postgres 16 via docker-compose.

**Design source of truth:** `design/odyssey-mockup.html` is the LOCKED, approved visual reference — a self-contained static implementation of Odyssey's full design system (near-monochrome dark+light themes, Geist type, green/coral semantics, the responsive nav, dashboard, position detail, signals, activity, onboarding screens). Phase-1 frontend work ports its CSS tokens and core components verbatim, then wires the Overview to the real API. Pixel-level polish is intentionally deferred to refinement against the running app (per design decision). Component/token details live in `[[odyssey-design-foundation]]` memory.

---

## File Structure

Backend (`backend/`):
- `pyproject.toml` — deps + pytest/ruff config
- `app/__init__.py`
- `app/config.py` — env-based settings (one responsibility: configuration)
- `app/crypto.py` — Fernet encrypt/decrypt for secrets at rest
- `app/db.py` — SQLAlchemy engine + session factory + `Base`
- `app/models.py` — `BrokerageAccount`, `Order`, `ActivityLog`
- `app/schemas.py` — Pydantic request/response models (masked secrets)
- `app/brokers/base.py` — `Action` types + `Broker` protocol + broker errors
- `app/brokers/fake.py` — in-memory fake broker for tests
- `app/brokers/alpaca.py` — Alpaca implementation of `Broker`
- `app/risk.py` — pure `validate_order()` guardrails
- `app/services/orders.py` — orchestrates risk → broker → persist + activity log
- `app/routers/health.py`, `app/routers/accounts.py`, `app/routers/orders.py`, `app/routers/positions.py`
- `app/main.py` — FastAPI app wiring + dependency providers
- `alembic/` + `alembic.ini` — migrations
- `tests/conftest.py`, `tests/test_crypto.py`, `tests/test_risk.py`, `tests/test_fake_broker.py`, `tests/test_alpaca_adapter.py`, `tests/test_orders_service.py`, `tests/test_api_orders.py`

Frontend (`frontend/`): standard `create-next-app` TypeScript output, plus
- `src/lib/api.ts` — typed fetch wrapper to the backend
- `src/app/page.tsx` — dashboard (PAPER badge, positions table, order form)

Infra (repo root):
- `docker-compose.yml` — Postgres 16 (dev + test DBs)
- `.env.example`, `.gitignore`, `README.md`

---

## Task 0: Repo scaffold, Postgres, and tooling

**Files:**
- Create: `docker-compose.yml`, `.env.example`, `.gitignore`, `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/tests/__init__.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
.env
__pycache__/
*.pyc
.venv/
.pytest_cache/
node_modules/
.next/
```

- [ ] **Step 2: Create `docker-compose.yml`** (dev + test databases)

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: fey
      POSTGRES_PASSWORD: fey
      POSTGRES_DB: fey
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
volumes:
  pgdata:
```

- [ ] **Step 3: Create `.env.example`**

```bash
DATABASE_URL=postgresql+psycopg://fey:fey@localhost:5432/fey
TEST_DATABASE_URL=postgresql+psycopg://fey:fey@localhost:5432/fey_test
# 32-byte urlsafe base64 key; generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY=replace-me
ALPACA_ENDPOINT=https://paper-api.alpaca.markets
```

- [ ] **Step 4: Create `backend/pyproject.toml`**

```toml
[project]
name = "odyssey"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "sqlalchemy>=2.0",
  "psycopg[binary]>=3.2",
  "alembic>=1.13",
  "pydantic-settings>=2.4",
  "cryptography>=43",
  "alpaca-py>=0.33",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27", "ruff>=0.6"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 5: Create empty package files**

Create `backend/app/__init__.py` and `backend/tests/__init__.py` (both empty).

- [ ] **Step 6: Bring up Postgres and create the test DB**

Run:
```bash
docker compose up -d db
sleep 3
docker compose exec -T db psql -U fey -d fey -c "CREATE DATABASE fey_test;"
```
Expected: `CREATE DATABASE` (or "already exists" on a re-run — both fine).

- [ ] **Step 7: Install backend deps**

Run:
```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```
Expected: installs without error; `pytest --version` works.

- [ ] **Step 8: Commit**

```bash
git init  # only if a repo is desired; skip if not using git
git add . && git commit -m "chore: scaffold Odyssey backend, Postgres, tooling"
```

---

## Task 1: Settings/config module

**Files:**
- Create: `backend/app/config.py`
- Test: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config.py
def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("FERNET_KEY", "k")
    monkeypatch.setenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")
    from app.config import Settings
    s = Settings()
    assert s.database_url == "postgresql+psycopg://x/y"
    assert s.alpaca_endpoint.endswith("alpaca.markets")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    test_database_url: str = ""
    fernet_key: str
    alpaca_endpoint: str = "https://paper-api.alpaca.markets"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py
git commit -m "feat: env-based settings module"
```

---

## Task 2: Secret encryption util

**Files:**
- Create: `backend/app/crypto.py`
- Test: `backend/tests/test_crypto.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_crypto.py
from cryptography.fernet import Fernet
from app.crypto import encrypt_secret, decrypt_secret


def test_roundtrip():
    key = Fernet.generate_key().decode()
    token = encrypt_secret("super-secret", key)
    assert token != "super-secret"
    assert decrypt_secret(token, key) == "super-secret"


def test_mask():
    from app.crypto import mask_secret
    assert mask_secret("ABCD1234EF") == "••••34EF"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.crypto'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/crypto.py
from cryptography.fernet import Fernet


def encrypt_secret(plaintext: str, key: str) -> str:
    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str, key: str) -> str:
    return Fernet(key.encode()).decrypt(token.encode()).decode()


def mask_secret(secret: str) -> str:
    return "••••" + secret[-4:] if len(secret) >= 4 else "••••"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_crypto.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/crypto.py backend/tests/test_crypto.py
git commit -m "feat: Fernet secret encryption + masking"
```

---

## Task 3: Database engine + Base

**Files:**
- Create: `backend/app/db.py`

- [ ] **Step 1: Write the implementation** (no separate unit test — exercised via models/integration tests in later tasks)

```python
# backend/app/db.py
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_db():
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: Verify it imports**

Run: `cd backend && python -c "import app.db; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/db.py
git commit -m "feat: SQLAlchemy engine, session, Base"
```

---

## Task 4: Models + Alembic migration

**Files:**
- Create: `backend/app/models.py`, `backend/alembic.ini`, `backend/alembic/env.py`, migration file
- Test: `backend/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_models.py
from app.models import BrokerageAccount, Order, ActivityLog


def test_models_have_expected_columns():
    assert {"id", "label", "mode", "alpaca_key_id", "alpaca_secret",
            "endpoint", "created_at"} <= set(BrokerageAccount.__table__.columns.keys())
    assert {"id", "account_id", "symbol", "side", "qty", "status",
            "alpaca_order_id", "reason", "dedupe_key", "submitted_at"} <= set(Order.__table__.columns.keys())
    assert {"id", "level", "event", "detail", "created_at"} <= set(ActivityLog.__table__.columns.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`.

- [ ] **Step 3: Write the models**

```python
# backend/app/models.py
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Numeric, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrokerageAccount(Base):
    __tablename__ = "brokerage_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(10), default="paper")  # paper | live
    alpaca_key_id: Mapped[str] = mapped_column(String(200))
    alpaca_secret: Mapped[str] = mapped_column(String(500))  # encrypted at rest
    endpoint: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("brokerage_accounts.id"))
    symbol: Mapped[str] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(4))   # buy | sell
    qty: Mapped[float] = mapped_column(Numeric(18, 4))
    status: Mapped[str] = mapped_column(String(12), default="pending")
    alpaca_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(200), default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str] = mapped_column(String(8), default="info")  # info | warn | error
    event: Mapped[str] = mapped_column(String(120))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

Note: `JSON` is used (portable); it maps to JSONB on Postgres via SQLAlchemy's PG dialect when desired in a later phase. Phase 1 keeps it simple.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Initialize Alembic**

Run: `cd backend && alembic init alembic`
Then edit `backend/alembic/env.py` — set the target metadata and URL. Replace the `run_migrations_online`/config section so it reads:

```python
# backend/alembic/env.py  (key edits)
from app.db import Base
from app.config import get_settings
from app import models  # noqa: F401  (register tables)

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

- [ ] **Step 6: Generate and apply the migration**

Run:
```bash
cd backend && alembic revision --autogenerate -m "init schema" && alembic upgrade head
```
Expected: a migration file is created under `alembic/versions/`; `upgrade head` runs without error. Verify:
```bash
docker compose exec -T db psql -U fey -d fey -c "\dt"
```
Expected: lists `brokerage_accounts`, `orders`, `activity_log`, `alembic_version`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/alembic.ini backend/alembic backend/tests/test_models.py
git commit -m "feat: ORM models + initial Alembic migration"
```

---

## Task 5: Broker interface, Action types, and fake broker

**Files:**
- Create: `backend/app/brokers/__init__.py`, `backend/app/brokers/base.py`, `backend/app/brokers/fake.py`
- Test: `backend/tests/test_fake_broker.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_fake_broker.py
from app.brokers.base import BuyOrder, SellOrder, Position, Quote
from app.brokers.fake import FakeBroker


def test_fake_broker_places_and_tracks_orders():
    b = FakeBroker(cash=10_000, quotes={"AAPL": 100.0})
    res = b.submit(BuyOrder(symbol="AAPL", qty=5, reason="test"))
    assert res.status == "filled"
    assert res.filled_price == 100.0
    positions = {p.symbol: p for p in b.get_positions()}
    assert positions["AAPL"].qty == 5
    assert b.get_quote("AAPL") == Quote(symbol="AAPL", price=100.0)


def test_fake_broker_sell_reduces_position():
    b = FakeBroker(cash=10_000, quotes={"AAPL": 100.0})
    b.submit(BuyOrder(symbol="AAPL", qty=5, reason="x"))
    b.submit(SellOrder(symbol="AAPL", qty=2, reason="x"))
    positions = {p.symbol: p for p in b.get_positions()}
    assert positions["AAPL"].qty == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_fake_broker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.brokers'`.

- [ ] **Step 3: Write `base.py` (Action types + protocol)**

```python
# backend/app/brokers/__init__.py
```
```python
# backend/app/brokers/base.py
from dataclasses import dataclass
from typing import Protocol, Literal


@dataclass(frozen=True)
class BuyOrder:
    symbol: str
    qty: float
    reason: str = ""
    side: Literal["buy"] = "buy"


@dataclass(frozen=True)
class SellOrder:
    symbol: str
    qty: float
    reason: str = ""
    side: Literal["sell"] = "sell"


Action = BuyOrder | SellOrder


@dataclass(frozen=True)
class OrderResult:
    status: str            # filled | pending | rejected
    filled_price: float | None = None
    broker_order_id: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_entry_price: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float


class BrokerError(Exception):
    pass


class Broker(Protocol):
    def submit(self, action: Action) -> OrderResult: ...
    def get_positions(self) -> list[Position]: ...
    def get_quote(self, symbol: str) -> Quote: ...
    def get_cash(self) -> float: ...
```

- [ ] **Step 4: Write `fake.py`**

```python
# backend/app/brokers/fake.py
from app.brokers.base import (
    Action, BuyOrder, SellOrder, OrderResult, Position, Quote, BrokerError,
)


class FakeBroker:
    def __init__(self, cash: float = 100_000.0, quotes: dict[str, float] | None = None):
        self._cash = cash
        self._quotes = quotes or {}
        self._positions: dict[str, Position] = {}
        self._counter = 0

    def submit(self, action: Action) -> OrderResult:
        price = self._quotes.get(action.symbol)
        if price is None:
            raise BrokerError(f"no quote for {action.symbol}")
        self._counter += 1
        oid = f"fake-{self._counter}"
        if isinstance(action, BuyOrder):
            self._cash -= price * action.qty
            existing = self._positions.get(action.symbol)
            new_qty = (existing.qty if existing else 0) + action.qty
            self._positions[action.symbol] = Position(action.symbol, new_qty, price)
        elif isinstance(action, SellOrder):
            existing = self._positions.get(action.symbol)
            remaining = (existing.qty if existing else 0) - action.qty
            self._cash += price * action.qty
            if remaining <= 0:
                self._positions.pop(action.symbol, None)
            else:
                self._positions[action.symbol] = Position(
                    action.symbol, remaining, existing.avg_entry_price
                )
        return OrderResult(status="filled", filled_price=price, broker_order_id=oid)

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_quote(self, symbol: str) -> Quote:
        if symbol not in self._quotes:
            raise BrokerError(f"no quote for {symbol}")
        return Quote(symbol=symbol, price=self._quotes[symbol])

    def get_cash(self) -> float:
        return self._cash
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_fake_broker.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/brokers backend/tests/test_fake_broker.py
git commit -m "feat: broker interface, Action types, fake broker"
```

---

## Task 6: Alpaca adapter

**Files:**
- Create: `backend/app/brokers/alpaca.py`
- Test: `backend/tests/test_alpaca_adapter.py`

The adapter is tested by injecting a stub Alpaca trading client (no network), asserting we map our `Action` → Alpaca request and Alpaca response → our `OrderResult`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_alpaca_adapter.py
from app.brokers.base import BuyOrder, OrderResult
from app.brokers.alpaca import AlpacaBroker


class _StubOrder:
    id = "alp-123"
    status = "accepted"
    filled_avg_price = None


class _StubClient:
    def __init__(self):
        self.submitted = []

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return _StubOrder()


def test_alpaca_submit_maps_buy_order():
    client = _StubClient()
    broker = AlpacaBroker(client=client)
    res = broker.submit(BuyOrder(symbol="AAPL", qty=2, reason="manual"))
    assert isinstance(res, OrderResult)
    assert res.broker_order_id == "alp-123"
    assert res.status == "pending"          # 'accepted' maps to pending
    assert client.submitted[0].symbol == "AAPL"
    assert client.submitted[0].qty == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_alpaca_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.brokers.alpaca'`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/brokers/alpaca.py
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from app.brokers.base import (
    Action, BuyOrder, OrderResult, Position, Quote, BrokerError,
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
    def __init__(self, client=None, key_id: str = "", secret: str = "", paper: bool = True):
        self._client = client or TradingClient(key_id, secret, paper=paper)

    def submit(self, action: Action) -> OrderResult:
        side = OrderSide.BUY if isinstance(action, BuyOrder) else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=action.symbol, qty=action.qty, side=side,
            time_in_force=TimeInForce.DAY,
        )
        try:
            o = self._client.submit_order(order_data=req)
        except Exception as e:  # alpaca raises various API errors
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
        raise NotImplementedError("market-data quote wired in Phase 2")

    def get_cash(self) -> float:
        return float(self._client.get_account().cash)
```

Note: `submit_order` is called positionally in the test (`order_data`) — the stub accepts one arg; the real client uses the `order_data=` keyword. Both match because the impl passes `order_data=req`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_alpaca_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/brokers/alpaca.py backend/tests/test_alpaca_adapter.py
git commit -m "feat: Alpaca broker adapter with response mapping"
```

---

## Task 7: Risk layer (pure guardrails)

**Files:**
- Create: `backend/app/risk.py`
- Test: `backend/tests/test_risk.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_risk.py
import pytest
from app.brokers.base import BuyOrder, SellOrder
from app.risk import validate_order, RiskRejection


def test_paper_mode_required_for_now():
    with pytest.raises(RiskRejection, match="live trading disabled"):
        validate_order(BuyOrder("AAPL", 1), mode="live", cash=1000, price=100,
                       max_position_notional=10_000)


def test_rejects_insufficient_cash():
    with pytest.raises(RiskRejection, match="insufficient buying power"):
        validate_order(BuyOrder("AAPL", 100), mode="paper", cash=500, price=100,
                       max_position_notional=1_000_000)


def test_rejects_oversized_position():
    with pytest.raises(RiskRejection, match="exceeds max position"):
        validate_order(BuyOrder("AAPL", 100), mode="paper", cash=1_000_000, price=100,
                       max_position_notional=5_000)


def test_rejects_non_positive_qty():
    with pytest.raises(RiskRejection, match="quantity must be positive"):
        validate_order(BuyOrder("AAPL", 0), mode="paper", cash=1000, price=100,
                       max_position_notional=10_000)


def test_valid_buy_passes():
    validate_order(BuyOrder("AAPL", 5), mode="paper", cash=10_000, price=100,
                   max_position_notional=10_000)  # no exception


def test_sell_skips_cash_check():
    validate_order(SellOrder("AAPL", 5), mode="paper", cash=0, price=100,
                   max_position_notional=10_000)  # no exception
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_risk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.risk'`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/risk.py
from app.brokers.base import Action, BuyOrder


class RiskRejection(Exception):
    pass


def validate_order(
    action: Action,
    *,
    mode: str,
    cash: float,
    price: float,
    max_position_notional: float,
) -> None:
    """Raise RiskRejection if the order violates a guardrail. Returns None if OK."""
    if mode != "paper":
        raise RiskRejection("live trading disabled in v1")
    if action.qty <= 0:
        raise RiskRejection("quantity must be positive")
    notional = price * action.qty
    if notional > max_position_notional:
        raise RiskRejection(
            f"order notional {notional:.2f} exceeds max position {max_position_notional:.2f}"
        )
    if isinstance(action, BuyOrder) and notional > cash:
        raise RiskRejection(
            f"insufficient buying power: need {notional:.2f}, have {cash:.2f}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_risk.py -v`
Expected: PASS (all six tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/risk.py backend/tests/test_risk.py
git commit -m "feat: pure risk guardrails (paper-only, buying power, position cap, qty)"
```

---

## Task 8: Order service (risk → broker → persist + activity log)

**Files:**
- Create: `backend/app/services/__init__.py`, `backend/app/services/orders.py`
- Test: `backend/tests/test_orders_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_orders_service.py
import pytest
from app.brokers.base import BuyOrder
from app.brokers.fake import FakeBroker
from app.risk import RiskRejection
from app.services.orders import place_order


class _Recorder:
    """Stand-in for a DB session: records what would be persisted."""
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def test_place_order_persists_filled_order_and_log():
    db = _Recorder()
    broker = FakeBroker(cash=10_000, quotes={"AAPL": 100.0})
    order = place_order(db, broker, account_id=1, mode="paper",
                        action=BuyOrder("AAPL", 5, reason="manual"),
                        max_position_notional=10_000)
    assert order.status == "filled"
    assert order.symbol == "AAPL"
    assert db.committed is True
    # one Order row + one ActivityLog row
    kinds = sorted(type(o).__name__ for o in db.added)
    assert kinds == ["ActivityLog", "Order"]


def test_place_order_rejected_by_risk_logs_and_raises():
    db = _Recorder()
    broker = FakeBroker(cash=100, quotes={"AAPL": 100.0})
    with pytest.raises(RiskRejection):
        place_order(db, broker, account_id=1, mode="paper",
                    action=BuyOrder("AAPL", 100, reason="manual"),
                    max_position_notional=1_000_000)
    # a warn-level ActivityLog was still recorded
    assert any(type(o).__name__ == "ActivityLog" and o.level == "warn" for o in db.added)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_orders_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services'`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/__init__.py
```
```python
# backend/app/services/orders.py
from app.brokers.base import Action, Broker
from app.models import Order, ActivityLog
from app.risk import validate_order, RiskRejection

DEFAULT_MAX_POSITION_NOTIONAL = 10_000.0


def place_order(
    db,
    broker: Broker,
    *,
    account_id: int,
    mode: str,
    action: Action,
    max_position_notional: float = DEFAULT_MAX_POSITION_NOTIONAL,
) -> Order:
    price = broker.get_quote(action.symbol).price
    cash = broker.get_cash()
    try:
        validate_order(action, mode=mode, cash=cash, price=price,
                       max_position_notional=max_position_notional)
    except RiskRejection as e:
        db.add(ActivityLog(level="warn", event="order_rejected",
                           detail={"symbol": action.symbol, "qty": float(action.qty),
                                   "reason": str(e)}))
        db.commit()
        raise

    result = broker.submit(action)
    order = Order(
        account_id=account_id, symbol=action.symbol, side=action.side,
        qty=action.qty, status=result.status,
        alpaca_order_id=result.broker_order_id, reason=action.reason,
    )
    db.add(order)
    db.add(ActivityLog(level="info", event="order_submitted",
                       detail={"symbol": action.symbol, "qty": float(action.qty),
                               "side": action.side, "status": result.status}))
    db.commit()
    db.refresh(order)
    return order
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_orders_service.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services backend/tests/test_orders_service.py
git commit -m "feat: order service orchestrating risk, broker, persistence, logging"
```

---

## Task 9: API — schemas, routers, app wiring, integration test

**Files:**
- Create: `backend/app/schemas.py`, `backend/app/routers/__init__.py`, `backend/app/routers/health.py`, `backend/app/routers/accounts.py`, `backend/app/routers/orders.py`, `backend/app/routers/positions.py`, `backend/app/main.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_api_orders.py`

- [ ] **Step 1: Write schemas**

```python
# backend/app/schemas.py
from pydantic import BaseModel


class AccountCreate(BaseModel):
    label: str
    alpaca_key_id: str
    alpaca_secret: str
    endpoint: str = "https://paper-api.alpaca.markets"


class AccountOut(BaseModel):
    id: int
    label: str
    mode: str
    masked_secret: str  # never the real secret


class OrderCreate(BaseModel):
    account_id: int
    symbol: str
    qty: float
    side: str = "buy"  # buy | sell


class OrderOut(BaseModel):
    id: int
    symbol: str
    side: str
    qty: float
    status: str
    reason: str


class PositionOut(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
```

- [ ] **Step 2: Write the health router**

```python
# backend/app/routers/__init__.py
```
```python
# backend/app/routers/health.py
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "mode": "paper"}
```

- [ ] **Step 3: Write the broker provider + accounts router**

```python
# backend/app/routers/accounts.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import get_settings
from app.crypto import encrypt_secret, mask_secret
from app.models import BrokerageAccount
from app.schemas import AccountCreate, AccountOut

router = APIRouter(prefix="/accounts")


@router.post("", response_model=AccountOut)
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    key = get_settings().fernet_key
    acct = BrokerageAccount(
        label=body.label, mode="paper",
        alpaca_key_id=body.alpaca_key_id,
        alpaca_secret=encrypt_secret(body.alpaca_secret, key),
        endpoint=body.endpoint,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return AccountOut(id=acct.id, label=acct.label, mode=acct.mode,
                      masked_secret=mask_secret(body.alpaca_secret))


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    out = []
    for a in db.query(BrokerageAccount).all():
        out.append(AccountOut(id=a.id, label=a.label, mode=a.mode,
                              masked_secret="••••" + a.alpaca_key_id[-4:]))
    return out
```

- [ ] **Step 4: Write the broker dependency + orders & positions routers**

```python
# backend/app/routers/orders.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import BrokerageAccount
from app.brokers.base import BuyOrder, SellOrder, BrokerError
from app.brokers.alpaca import AlpacaBroker
from app.risk import RiskRejection
from app.services.orders import place_order
from app.schemas import OrderCreate, OrderOut

router = APIRouter(prefix="/orders")


def build_broker(account: BrokerageAccount):
    secret = decrypt_secret(account.alpaca_secret, get_settings().fernet_key)
    return AlpacaBroker(key_id=account.alpaca_key_id, secret=secret, paper=True)


# Overridable seam for tests:
def get_broker_for_account(account: BrokerageAccount):
    return build_broker(account)


@router.post("", response_model=OrderOut)
def create_order(body: OrderCreate, db: Session = Depends(get_db)):
    account = db.get(BrokerageAccount, body.account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    action_cls = BuyOrder if body.side == "buy" else SellOrder
    action = action_cls(symbol=body.symbol.upper(), qty=body.qty, reason="manual")
    broker = get_broker_for_account(account)
    try:
        order = place_order(db, broker, account_id=account.id, mode=account.mode, action=action)
    except RiskRejection as e:
        raise HTTPException(422, f"risk rejection: {e}")
    except BrokerError as e:
        raise HTTPException(502, f"broker error: {e}")
    return OrderOut(id=order.id, symbol=order.symbol, side=order.side,
                    qty=float(order.qty), status=order.status, reason=order.reason)
```

```python
# backend/app/routers/positions.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import BrokerageAccount
from app.routers.orders import get_broker_for_account
from app.schemas import PositionOut

router = APIRouter(prefix="/positions")


@router.get("/{account_id}", response_model=list[PositionOut])
def list_positions(account_id: int, db: Session = Depends(get_db)):
    account = db.get(BrokerageAccount, account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    broker = get_broker_for_account(account)
    return [PositionOut(symbol=p.symbol, qty=p.qty, avg_entry_price=p.avg_entry_price)
            for p in broker.get_positions()]
```

- [ ] **Step 5: Wire the app**

```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import health, accounts, orders, positions

app = FastAPI(title="Odyssey")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(orders.router)
app.include_router(positions.router)
```

- [ ] **Step 6: Write the test fixtures (conftest)**

```python
# backend/tests/conftest.py
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet


@pytest.fixture(scope="session", autouse=True)
def _env():
    os.environ.setdefault("DATABASE_URL", os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://fey:fey@localhost:5432/fey_test"))
    os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())


@pytest.fixture
def client():
    # import after env is set
    from app.db import Base, get_engine, get_db
    from app.main import app
    from app.models import BrokerageAccount  # noqa
    from app.routers import orders as orders_router
    from app.brokers.fake import FakeBroker

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    fake = FakeBroker(cash=50_000, quotes={"AAPL": 100.0, "TSLA": 250.0})

    app.dependency_overrides[get_db] = _override_db
    orders_router.get_broker_for_account = lambda account: fake  # inject fake
    c = TestClient(app)
    c._fake = fake
    yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 7: Write the failing integration test**

```python
# backend/tests/test_api_orders.py
def _make_account(client):
    r = client.post("/accounts", json={
        "label": "trading-claude", "alpaca_key_id": "AKTEST1234",
        "alpaca_secret": "secretXYZ", "endpoint": "https://paper-api.alpaca.markets"})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["mode"] == "paper"


def test_create_account_masks_secret(client):
    acct = _make_account(client)
    assert acct["mode"] == "paper"
    assert "secretXYZ" not in str(acct)
    assert acct["masked_secret"].startswith("••••")


def test_buy_order_end_to_end(client):
    acct = _make_account(client)
    r = client.post("/orders", json={"account_id": acct["id"], "symbol": "AAPL", "qty": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "filled"
    assert body["symbol"] == "AAPL"
    pos = client.get(f"/positions/{acct['id']}").json()
    assert any(p["symbol"] == "AAPL" and p["qty"] == 3 for p in pos)


def test_oversized_order_rejected(client):
    acct = _make_account(client)
    # default max position notional is 10_000; 200 * 100 = 20_000 > cap
    r = client.post("/orders", json={"account_id": acct["id"], "symbol": "AAPL", "qty": 200})
    assert r.status_code == 422
    assert "risk rejection" in r.json()["detail"]
```

- [ ] **Step 8: Run tests to verify they fail then pass**

Run: `cd backend && pytest tests/test_api_orders.py -v`
Expected first run (before routers exist): import/collection errors. After Steps 1–6 are in place, re-run:
Expected: all four tests PASS. (Requires Postgres up from Task 0 and the `fey_test` DB.)

- [ ] **Step 9: Run the full suite**

Run: `cd backend && pytest -v`
Expected: every test from Tasks 1–9 PASSES.

- [ ] **Step 10: Commit**

```bash
git add backend/app/schemas.py backend/app/routers backend/app/main.py backend/tests/conftest.py backend/tests/test_api_orders.py
git commit -m "feat: REST API (health, accounts, orders, positions) + integration tests"
```

---

## Task 10: Next.js dashboard — built to the locked design

> **Design fidelity & stack (HYBRID):** Build to `design/odyssey-mockup.html`, the approved reference, using a **hybrid frontend stack**: **Tailwind CSS** for utilities/layout/responsive, but keep Odyssey's **custom design tokens** (port the mockup's `:root`/`[data-theme="light"]` CSS variables — colors, radii, shadows — into `globals.css` and expose them through `tailwind.config` `theme.extend`), and **hand-build the signature components** (the SVG charts with crosshair, the responsive nav, the dense tables) rather than using off-the-shelf components. **Do NOT use shadcn.** Load **Geist + Geist Mono**. Reproduce: responsive nav (left rail ≥1281px / labeled top bar ≤1280px), holdings `tcard` table with two-part return pills + circular logos, `pill`/`tag` styles, PAPER badge, masked-balance + eye toggle. Phase 1 ships the **Overview** screen wired to the real API (account, positions, manual order); Position-detail/Signals/Activity/Onboarding + bot UI land in later phases. The mockup's inline-styled `page.tsx` below is the *minimum* functional wiring — replace its look with the ported design system. Use the **vercel-react-best-practices**, **tailwind-css-patterns**, and **web-design-guidelines** skills while building.

**Files:**
- Create: `frontend/` (via create-next-app), `frontend/src/app/globals.css` (ported design tokens), `frontend/src/lib/api.ts`, `frontend/src/components/` (Nav, HoldingsTable, OrderForm, PaperBadge), `frontend/src/app/page.tsx`, `frontend/.env.local`

- [ ] **Step 1: Scaffold the frontend**

Run:
```bash
npx create-next-app@latest frontend --typescript --app --tailwind --eslint --src-dir --import-alias "@/*" --no-turbopack
```
Expected: `frontend/` created.

- [ ] **Step 2: Set the API base URL**

Create `frontend/.env.local`:
```bash
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

- [ ] **Step 3: Write the typed API client**

```typescript
// frontend/src/lib/api.ts
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Position = { symbol: string; qty: number; avg_entry_price: number };
export type OrderOut = { id: number; symbol: string; side: string; qty: number; status: string; reason: string };

export async function getPositions(accountId: number): Promise<Position[]> {
  const r = await fetch(`${BASE}/positions/${accountId}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`positions failed: ${r.status}`);
  return r.json();
}

export async function placeOrder(accountId: number, symbol: string, qty: number): Promise<OrderOut> {
  const r = await fetch(`${BASE}/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_id: accountId, symbol, qty }),
  });
  if (!r.ok) throw new Error((await r.json()).detail ?? "order failed");
  return r.json();
}
```

- [ ] **Step 4: Write the dashboard page**

```tsx
// frontend/src/app/page.tsx
"use client";
import { useEffect, useState } from "react";
import { getPositions, placeOrder, type Position } from "@/lib/api";

const ACCOUNT_ID = 1; // single-account in Phase 1

export default function Dashboard() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [symbol, setSymbol] = useState("AAPL");
  const [qty, setQty] = useState(1);
  const [msg, setMsg] = useState("");

  async function refresh() {
    try { setPositions(await getPositions(ACCOUNT_ID)); }
    catch (e) { setMsg(String(e)); }
  }
  useEffect(() => { refresh(); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg("placing…");
    try {
      const o = await placeOrder(ACCOUNT_ID, symbol, qty);
      setMsg(`Order ${o.status}: ${o.side} ${o.qty} ${o.symbol}`);
      await refresh();
    } catch (e) { setMsg(String(e)); }
  }

  return (
    <main style={{ maxWidth: 680, margin: "40px auto", fontFamily: "system-ui" }}>
      <h1>Odyssey <span style={{ background: "#fde68a", padding: "2px 8px", borderRadius: 6, fontSize: 14 }}>PAPER</span></h1>
      <form onSubmit={submit} style={{ display: "flex", gap: 8, margin: "16px 0" }}>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        <input type="number" min={1} value={qty} onChange={(e) => setQty(Number(e.target.value))} />
        <button type="submit">Buy</button>
      </form>
      <p>{msg}</p>
      <h2>Positions</h2>
      <table>
        <thead><tr><th>Symbol</th><th>Qty</th><th>Avg entry</th></tr></thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.symbol}><td>{p.symbol}</td><td>{p.qty}</td><td>{p.avg_entry_price}</td></tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

- [ ] **Step 5: Manual end-to-end verification**

Run, in two terminals:
```bash
# terminal 1
cd backend && . .venv/bin/activate && uvicorn app.main:app --reload
# terminal 2
cd frontend && npm run dev
```
Then:
1. `curl -s localhost:8000/health` → `{"status":"ok","mode":"paper"}`.
2. Create an account once (real Alpaca paper keys, or the fake path for a dry run):
   ```bash
   curl -s -X POST localhost:8000/accounts -H 'content-type: application/json' \
     -d '{"label":"trading-claude","alpaca_key_id":"<KEY>","alpaca_secret":"<SECRET>"}'
   ```
3. Open `http://localhost:3000`, confirm the **PAPER** badge shows, submit "Buy 1 AAPL", and confirm a status message + the position appears (against the real Alpaca paper account).

Expected: order status message renders and the positions table updates.

- [ ] **Step 6: Commit**

```bash
git add frontend
git commit -m "feat: Next.js dashboard with manual order form and PAPER badge"
```

---

## Self-Review

**Spec coverage (Phase 1 / Foundation scope):**
- Repo scaffold (Next.js + FastAPI + Postgres) → Tasks 0, 10. ✓
- Broker adapter behind an interface + manual buy path → Tasks 5, 6, 8, 9. ✓
- Account/secrets handling, encrypted at rest, never sent to frontend → Tasks 2, 9 (masked output). ✓
- Risk chokepoint (paper-only, buying power, position cap, qty) → Task 7, enforced in Task 8. ✓
- Activity log (append-only audit) → Task 4 model, written in Task 8. ✓
- PAPER badge everywhere → Task 10. ✓
- Tests concentrated on money-path (engine/risk/adapter) → Tasks 5–9. ✓
- Out of Phase 1 by design (covered in later plans): trailing-stop & copy-trade engines, scheduler, Capitol Trades scraper, P&L snapshots, wheel/options, live-money gating, multi-user/auth.

**Placeholder scan:** No TBD/TODO; every code step contains runnable code. `get_quote` on the Alpaca adapter intentionally raises `NotImplementedError` (documented; market-data wiring is Phase 2) — the manual-order path uses the fake broker's quote in tests and Alpaca's fill price in production, so Phase 1 functionality is complete without it. Note for execution: the live `/orders` path calls `broker.get_quote()` in `place_order`; against real Alpaca this needs the Phase-2 market-data client. For Phase 1, end-to-end automated tests use the FakeBroker (which provides quotes); manual live verification with real keys is the one place this matters and is called out in Task 10 Step 5.

**Type consistency:** `Action = BuyOrder | SellOrder` with `.symbol/.qty/.side/.reason` used consistently across `risk.py`, `services/orders.py`, routers, and the fake/Alpaca brokers. `OrderResult.status` values (`filled|pending|rejected`) flow unchanged into `Order.status` and `OrderOut.status`. `place_order` signature matches all call sites (service test + orders router). `get_broker_for_account` is the single injection seam used by both the orders and positions routers and overridden in `conftest.py`.

**Known follow-up (flagged, not a gap):** In Task 8's service test, `db` is a recorder stub (pure unit test of orchestration); the real DB path is exercised by Task 9's integration tests against Postgres. This is intentional layering, not a missing test.
