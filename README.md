# Odyssey

> A paper-trading automation web app — manual trades, an automated trailing-stop bot, and
> congressional copy-trading — built on **FastAPI + Next.js + Postgres**, executing through
> the **Alpaca paper-trading** API.

**Paper trading only.** Odyssey never touches real money or live brokerage accounts. It is
an educational project, not financial advice.

---

## What it is

Odyssey turns a near-monochrome dark/light dashboard into a small trading-automation
platform. Strategy engines are **deterministic** (no LLM in the money-path); every order
flows through a single pure **risk chokepoint** and a **swappable broker adapter** (real
Alpaca paper, or an in-memory fake for tests).

### Features
- **Manual trading** — accounts (broker API keys Fernet-encrypted at rest), a risk-checked
  order path, positions, activity log, a stock screener, watchlist, and a stock-detail view
  (price/volume chart, KPIs, news, earnings, analyst ratings).
- **Trailing-stop bot** — a pure trailing-stop engine driven by Alpaca quotes + market
  clock, ticked every 5 minutes during market hours by an in-process scheduler.
- **Copy-trade** — mirrors real U.S. congressional trades scraped hourly from
  [Capitol Trades](https://www.capitoltrades.com), routed through the same risk + order path.

---

## Tech stack

| Layer    | Stack |
|----------|-------|
| Frontend | Next.js 16, React 19, Tailwind CSS v4, TypeScript (Geist / Geist Mono) |
| Backend  | FastAPI, SQLAlchemy 2, Alembic, Pydantic Settings, APScheduler (Python 3.12+) |
| Data     | PostgreSQL 16 |
| Broker   | Alpaca (`alpaca-py`, paper endpoint) |
| Crypto   | `cryptography` (Fernet) for stored broker keys |

---

## Prerequisites

- **Python 3.12+**
- **Node.js 20+** and npm
- **PostgreSQL 16** — either via Docker (`docker-compose.yml` included) or a native install
- *(optional)* an **Alpaca paper** key/secret for live quotes + order fills
- *(optional)* a free **Finnhub** API key for real fundamentals, earnings, and logos

---

## Setup

### 1. Database

Easiest — start Postgres with the bundled compose file (role `fey`, db `fey`, port 5432):

```bash
docker compose up -d db
```

Or use a native Postgres 16 with a `fey` role and `fey` / `fey_test` databases.

> The internal database role/db is named `fey` for historical reasons; it is not
> user-facing and can stay as-is.

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env          # then fill in FERNET_KEY (see below)
.venv/bin/alembic upgrade head
```

Generate a `FERNET_KEY` for `.env`:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create the test database once (needed for the suite):

```bash
createdb fey_test           # or: psql -c "CREATE DATABASE fey_test;"
```

### 3. Frontend

```bash
cd frontend
npm install
# .env.local already points at the backend:
#   NEXT_PUBLIC_API_BASE=http://localhost:8000
```

---

## Running the app

Run the backend and frontend in two terminals (Postgres must be up first):

```bash
# Terminal 1 — API  →  http://localhost:8000  (interactive docs at /docs)
cd backend && .venv/bin/uvicorn app.main:app --reload

# Terminal 2 — Web  →  http://localhost:3000
cd frontend && npm run dev
```

Then open **http://localhost:3000**.

To connect a real Alpaca paper account, add it through the app (Accounts) or the
`POST /accounts` API — keys are encrypted with your `FERNET_KEY` before being stored.

*(Optional)* seed the ticker universe with real fundamentals/logos (needs `FINNHUB_API_KEY`):

```bash
cd backend && .venv/bin/python -m app.seed_tickers
```

---

## Environment variables

| Variable | File | Purpose |
|----------|------|---------|
| `DATABASE_URL` | `backend/.env` | Postgres DSN (default `postgresql+psycopg://fey:fey@localhost:5432/fey`) |
| `TEST_DATABASE_URL` | `backend/.env` | Test DB DSN (`…/fey_test`) |
| `FERNET_KEY` | `backend/.env` | 32-byte urlsafe base64 key encrypting stored broker keys |
| `ALPACA_ENDPOINT` | `backend/.env` | Alpaca base URL (`https://paper-api.alpaca.markets`) |
| `FINNHUB_API_KEY` | `backend/.env` | *(optional)* real fundamentals/earnings/ratings/logos |
| `NEXT_PUBLIC_API_BASE` | `frontend/.env.local` | Backend URL the browser calls (`http://localhost:8000`) |
| `ODYSSEY_DISABLE_SCHEDULER` | env | Set to `1` to skip starting APScheduler (used in tests) |

---

## Testing

```bash
cd backend && .venv/bin/pytest -q     # 46 tests, requires Postgres + fey_test DB
```

---

## Project structure

```
Odyssey/
├── backend/                 # FastAPI + SQLAlchemy + Alembic
│   ├── app/
│   │   ├── main.py          # FastAPI app + APScheduler lifespan
│   │   ├── brokers/         # adapter: alpaca.py · fake.py · base.py
│   │   ├── strategies/      # pure engines: trailing_stop.py · copy_trade.py
│   │   ├── services/        # runner · copy_runner · scheduler · market_data · signals_sync
│   │   ├── routers/         # REST endpoints (accounts, orders, positions, bots, …)
│   │   ├── data/            # capitol_trades.py · universe.py
│   │   ├── risk.py          # single pure risk chokepoint
│   │   ├── crypto.py        # Fernet encryption for broker keys
│   │   └── models.py · schemas.py · db.py · config.py
│   ├── alembic/             # migrations
│   └── tests/               # pytest suite
├── frontend/                # Next.js 16 app (src/app, src/components, src/lib)
├── design/                  # DESIGN_SYSTEM.md + mockups (UI source of truth)
├── docker-compose.yml       # Postgres 16
└── README.md
```

---

## API overview

Base URL `http://localhost:8000` · OpenAPI docs at `/docs`.

| Area | Endpoints |
|------|-----------|
| Health | `GET /health` |
| Accounts | `GET/POST /accounts` |
| Orders | `POST /orders` |
| Positions | `GET /positions/{account_id}` · `/quotes` · `/summary` |
| Bots | `GET/POST /bots` · `GET/PATCH /bots/{id}` · `POST /bots/{id}/run` |
| Signals | `GET /signals` · `POST /signals/sync` |
| Activity | `GET /activity` |
| Stocks | `GET /stocks` · `/metrics` · `/industries` · `/movers` · `/{symbol}` (+ history/news/earnings/analysis/dividends/signals) |
| Watchlist | `GET/POST /watchlist` · `DELETE /watchlist/{symbol}` |

---

## Design system

A near-monochrome dark/light design language with green/coral semantics and Geist
typography. Tokens and the component library are documented in
[`design/DESIGN_SYSTEM.md`](design/DESIGN_SYSTEM.md), and every component is browsable live
at the `/design-system` route. Dark is the default theme; light is also supported.

---

## Notes & limitations

- The scheduler is **in-process** (APScheduler) — automation only runs while the backend is
  up; it is not true 24/7 until deployed.
- Live order pricing needs real Alpaca paper keys.
- Capitol Trades' JSON API is unreliable, so signals are parsed from the rendered
  trades page (see `backend/app/data/capitol_trades.py`).
- The frontend uses a **non-standard Next.js 16** with breaking changes — read
  `frontend/AGENTS.md` before editing frontend code.

---

## License

_TBD._
