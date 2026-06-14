# Fey — Project Status & Handoff

_Last updated: 2026-06-03_

## What Fey is
A paper-trading automation web app: **Next.js (frontend) + FastAPI/Python (backend) + Postgres**, trading via **Alpaca paper** API. Inspired by a YouTube tutorial. Near-monochrome dark+light UI. Deterministic strategy engines (no LLM in the money-path).

## Status: all 3 planned phases BUILT, GREEN, and LIVE-VERIFIED
- **Phase 1 — Foundation:** accounts (Fernet-encrypted), broker adapter (Alpaca + fake), pure risk chokepoint, order service, REST API; Next.js Overview.
- **Phase 2 — Trailing-stop bot:** Alpaca quotes+clock, `bots`/`positions` tables, pure trailing-stop engine, tick runner, APScheduler (5-min ticks, market-hours), bot API + bot UI.
- **Phase 3 — Copy-trade:** `signals` table, **Capitol Trades** source, copy engine, copy runner, hourly scrape job, signals API + Signals UI.
- **Backend test suite: 46 passing** (`cd backend && .venv/bin/pytest -q`).
- **Live-verified against a real Alpaca paper account:** manual AAPL buy filled; trailing-stop bot opened a position + set a stop floor; copy bot mirrored a REAL congressional trade.

## How to run
```bash
cd backend && .venv/bin/uvicorn app.main:app --reload     # http://localhost:8000
cd frontend && npm run dev                                 # http://localhost:3000
```
Postgres runs natively (brew `postgresql@16`; role `fey`, dbs `fey`/`fey_test`). NO git repo. Permissions are broad in `.claude/settings.json` (no prompts).

## Key facts / gotchas
- **Design source of truth:** `design/fey-mockup.html` (open it before changing UI). Tokens ported into `frontend/src/app/globals.css` (Tailwind v4 `@theme`). Fonts: Geist / Geist Mono. No shadcn.
- **Capitol Trades:** their `bff.capitoltrades.com` JSON API is broken (CloudFront/Lambda 503). We instead GET `www.capitoltrades.com/trades` and parse trades from the embedded Next.js RSC flight payload — see `backend/app/data/capitol_trades.py`. Sync: hourly job + `POST /signals/sync` + "Sync now" button.
- **Scheduler is in-process** (APScheduler) — only runs while the backend is up; not true 24/7 until deployed.
- **Live order pricing** uses Alpaca market data (`get_quote`); needs real paper keys (saved in `.secrets/alpaca.md`, gitignored; connected as account id 2 "my-paper").
- **Frontend Next.js version is NOT standard** — `frontend/AGENTS.md` says read `node_modules/next/dist/docs/` before writing Next code (it's Next 16 with breaking changes).
- Next.js branding removed: `devIndicators:false`, Fey flame favicon, no boilerplate SVGs.

## Docs
- Spec: `docs/superpowers/specs/2026-06-02-fey-design.md`
- Plans: `docs/superpowers/plans/2026-06-02-fey-phase{1,2,3}-*.md`
- Video transcript: `/tmp/fey_transcript_timestamped.txt` (may be cleared on reboot)

## Known follow-ups / not built
Wheel/options strategy; auth/multi-user; per-bot position segregation on a shared account; `get_positions` should return 502 (not 500) on a bad account; pixel-level UI polish on the live app; deploy for true always-on scheduling.
