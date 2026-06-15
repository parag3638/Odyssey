# Odyssey — Design Spec

**Date:** 2026-06-02
**Status:** Approved (design); pending implementation plan

## Origin

Inspired by the YouTube tutorial *"Claude Just Changed the Stock Market Forever!"* by
Samin Yasar (`https://www.youtube.com/watch?v=lH5wrfNwL3k`). The video demonstrates
driving Claude + Alpaca + scheduled tasks to run trading strategies (trailing-stop,
copy-trading of US politicians via Capitol Trades, and the options "wheel" strategy).

Odyssey productizes that idea as a real, trustworthy, always-on app — replacing the
"talk to an LLM live" approach with a **deterministic strategy engine**.

A full transcript of the source video is retained at `/tmp/odyssey_transcript_timestamped.txt`
(reference only; not part of the repo).

## What Odyssey is

A paper-trading-first web application that runs deterministic trading strategies on an
always-on, market-hours scheduler.

### Locked decisions

- **Standalone app**, not a personal Claude automation or a Claude skill/MCP toolkit.
- **Audience:** built personal now (single user, own credentials, no signup/billing),
  but architected so multi-user/auth can be added later without a rewrite.
- **Deterministic engine:** no LLM in the money-path. Strategies are explicit, testable
  rules. An LLM may later be added *only* for plain-English summaries/insights.
- **Form factor:** Next.js web dashboard + always-on FastAPI backend with a scheduler
  (fixes the video's "only runs while my computer is on" limitation).
- **Stack:** Next.js (frontend) + FastAPI / Python (backend) + Postgres.
- **v1 strategies:** trailing-stop + ladder buy, and copy-trading (Congress/whales).
  The options **wheel strategy is explicitly deferred** to a later phase (it requires
  options trading support and more careful risk rules).
- **Broker:** Alpaca, **paper-trading only in v1**. A live-money path exists in the
  design but is gated off behind explicit per-account enablement + a typed UI
  confirmation; v1 ships paper-only.
- **Copy-trade data source:** scrape Capitol Trades (free) for v1. Isolated behind a
  data-provider boundary so it can be swapped for a paid API later.

### Non-goals (v1)

- Options / the wheel strategy.
- Live (real-money) trading enabled by default.
- Multi-user signup, billing, public SaaS hardening.
- LLM-driven decision making at runtime.
- Margin / leverage.

## Architecture

```
Next.js dashboard  ──REST──▶  FastAPI backend
(positions, P&L,   ◀──────    ├─ API layer (thin routers)
 bot config, logs)            ├─ Scheduler (always-on, market-hours)
                              ├─ Strategy Engine (PURE: state+data -> actions)
                              ├─ Risk layer (single chokepoint for all orders)
                              ├─ Broker adapter (Alpaca, behind interface)
                              └─ Data providers (Alpaca quotes/positions;
                                                 Capitol Trades scraper)
                                        │
                              Postgres ─┘   +   Alpaca paper API
```

**Core principle:** the engine is pure and deterministic; everything risky (network,
scraping, order submission) lives at the edges behind small interfaces. The money-path
is unit-testable; the scraper and broker are replaceable.

### Components

1. **Next.js dashboard** — view bots & status, positions, P&L, realized gains, trade
   history, live activity log; create/edit/pause bots; a prominent **PAPER** badge
   everywhere. Talks only to the FastAPI REST API.
2. **FastAPI API layer** — thin routers (`bots` CRUD, `positions`, `trades`/`orders`,
   `signals`, `logs`, `health`). No business logic here.
3. **Strategy engine** — pure functions: `evaluate(bot_config, positions, market_data,
   signals?) -> [Action]`. No I/O, no clock-reading inside (`now` and all data are
   passed in). Output is intended `Action`s (`Buy`, `Sell`, `AdjustStop`) each carrying
   a human-readable `reason`.
4. **Scheduler/worker** — always-on loop; evaluates active bots on a fast cadence
   (1–5 min) **only during US market hours**.
5. **Risk layer** — enforces guardrails before any order reaches the broker. Single
   chokepoint every order passes through.
6. **Broker adapter (Alpaca)** — wraps `alpaca-py`; place/cancel orders, fetch
   positions & quotes. Hides Alpaca behind a small interface for swappability.
7. **Data providers** — (a) Alpaca quotes/positions; (b) Capitol Trades scraper that
   normalizes disclosures into the `signals` table on its own slower cadence.
8. **Postgres** — source of truth: bots/config, position snapshots, order history,
   scraped signals, P&L snapshots, append-only activity log.

## Data model (Postgres)

**`brokerage_accounts`** — `id`, `label`, `mode` (`paper`|`live`, default `paper`),
`alpaca_key_id`, `alpaca_secret` (encrypted at rest), `endpoint`, `created_at`.
(`user_id` added later for multi-user; nullable/defaulted now.)

**`bots`** — `id`, `name`, `account_id`→`brokerage_accounts`, `strategy_type`
(`trailing_stop`|`copy_trade`), `status` (`active`|`paused`|`stopped`), `config`
(JSONB), `schedule_cadence_sec`, `created_at`, `updated_at`.

**`positions`** — `id`, `bot_id`, `symbol`, `qty`, `avg_entry_price`, `current_price`,
`market_value`, `unrealized_pl`, `stop_floor` (trailing-stop), `triggered_rungs`
(JSONB, ladder state), `updated_at`.

**`orders`** — `id`, `bot_id`, `symbol`, `side`, `qty`, `order_type`,
`limit_price`/`stop_price`, `status` (`pending`|`filled`|`rejected`|`canceled`),
`alpaca_order_id`, `reason`, `dedupe_key`, `submitted_at`, `filled_at`, `filled_price`.
Append-only.

**`signals`** — `id`, `politician`, `symbol`, `tx_type` (buy/sell), `tx_date`,
`disclosed_date`, `amount_range`, `source_url`, `scraped_at`, `hash` (unique, dedupe).

**`pnl_snapshots`** — `id`, `bot_id`, `equity`, `cash`, `realized_pl`,
`unrealized_pl`, `captured_at`.

**`activity_log`** — `id`, `bot_id` (nullable), `level` (`info`|`warn`|`error`),
`event`, `detail` (JSONB), `created_at`. Append-only audit trail.

### `config` JSONB shapes

- **trailing_stop:** `{ symbol, initial_shares, stop_pct: 0.10, trail_pct: 0.05,
  ladder: [{drop_pct: 0.20, add_shares: 20}, ...] }`
- **copy_trade:** `{ politician: "auto"|"<name>", follow_buys: true, follow_sells: true,
  per_trade_notional: 1000, symbols_filter: null }`

## Strategy engine

Signature: `evaluate(bot_config, positions, market_data, signals?) -> [Action]`.
Pure — no network/DB/clock inside.

**Trailing-stop:**
1. No position → `Buy(initial_shares)`; set `stop_floor = entry × (1 − stop_pct)`.
2. Price rose so that `price × (1 − trail_pct) > stop_floor` → `AdjustStop` raising the
   floor. Floor only ever moves **up**.
3. `price ≤ stop_floor` → `Sell(all)` (reason "stop hit").
4. Ladder: price crosses a not-yet-triggered rung → `Buy(add_shares)`; record the rung
   in `positions.triggered_rungs` so it fires once.

**Copy-trade:**
1. Read recent `signals` for the followed politician (or auto-pick the most
   active/best-performing from the data).
2. Diff against already-placed orders (dedupe via signal `hash`).
3. New buy signal → `Buy(per_trade_notional / price)`; sell signal on a held symbol →
   `Sell`. `reason` references politician + disclosure.
4. Disclosure lag is acceptable by design (Congress trades long-horizon).

## Scheduler tick (per active bot)

```
1. US market open now?  ── no ──▶ skip eval (copy-trade signals may still refresh)
2. Refresh data: Alpaca positions+quotes (reconcile DB↔broker); signals already scraped
3. actions = engine.evaluate(config, positions, market_data, signals)
4. for each action:
     risk.validate(action, account, positions)  ── reject ──▶ log warn, skip
     broker.submit(action)                       → record in `orders`
5. persist position snapshot + pnl_snapshot
6. append activity_log entries (what + why)
```

**Two cadences:** fast loop (strategy eval, 1–5 min, market hours) and a slower
**Capitol Trades scraper** job (a few times daily). The engine only *reads* `signals`,
so scraper breakage degrades gracefully and never blocks trading.

**Idempotency / crash-safety:** every order carries a `dedupe_key` (signal hash, or
bot+symbol+intent+tick); replaying a tick after a crash won't double-submit. Position
state (`stop_floor`, `triggered_rungs`) is persisted, so the engine is stateless across
restarts.

## Risk guardrails

The engine *proposes*; risk *disposes*. Every order passes `risk.validate()`:

1. **Paper-only by default.** `mode=live` requires explicit per-account enablement +
   typed UI confirmation. v1 ships paper-only (live path gated off).
2. **Sufficient buying power** — reject buys exceeding available cash (no margin v1).
3. **Max position size** — per-bot cap on notional/% of account per symbol.
4. **Max orders per tick / per day** — throttle to catch logic bugs.
5. **Order sanity** — positive qty, known symbol, market open, price within a plausible
   band of last quote (guards bad scrape/quote).
6. **Kill switch** — global `pause_all` + per-bot pause honored immediately by the
   scheduler; one-click from the dashboard.

Rejections are always logged to `activity_log` with a reason — never silently dropped.

## Secrets handling

- Alpaca keys + app secrets live in env/config, never in the repo. `.env` git-ignored;
  committed `.env.example`.
- `brokerage_accounts` credentials encrypted at rest with an app-level key (key from
  env, not the DB).
- Secrets never sent to the frontend — dashboard sees a masked label
  (e.g. `Alpaca paper ••••3F2A`). All Alpaca calls are server-side.
- Capitol Trades scraper uses no auth; realistic user-agent + conservative rate-limit.

## Error handling

Principle: **on any uncertainty, do nothing and log it.**

| Failure | Behavior |
|---|---|
| Alpaca down / 5xx | Retry w/ backoff; else skip tick (no trading on stale data), log error, dashboard banner. Next tick recovers. |
| Scrape fails / layout changed | Scraper logs error, writes nothing; engine sees no new signals → no action. "Signals stale since X" flag. |
| Order rejected by broker | Record `orders.status=rejected` + reason; bot continues; not retried in a loop. |
| Partial fill | Reconcile from Alpaca next tick (broker = source of truth for fills). |
| Crash mid-tick | `dedupe_key` makes replay safe; persisted state → no double-submit, no lost stop_floor. |
| Market closed | Scheduler skips eval; scraper may still refresh signals. |

## Testing strategy (TDD — tests first)

Test effort concentrated on the money-path.

- **Engine (highest priority):** exhaustive unit tests with fixtures. Trailing-stop —
  floor rises but never falls; stop triggers sell; ladder rungs fire once each; no
  position → initial buy. Copy-trade — new signal → order; duplicate → no order; sell
  signal on held symbol → sell.
- **Risk layer:** per-guardrail unit tests — over-buying-power rejected, oversized
  position rejected, paper-mode enforced, kill switch halts, per-tick throttle.
- **Broker adapter:** Alpaca paper sandbox + a fake/mock adapter for fast tests;
  Alpaca response → `orders` mapping.
- **Scraper:** parse against saved HTML fixtures (deterministic); one optional live
  smoke test; normalization + dedupe-hash covered.
- **Scheduler:** integration test with mock broker + fixture data + injected clock —
  full tick (data→engine→risk→broker→persist→log) + idempotency on replay.
- **API + dashboard:** router contract tests; a couple of end-to-end happy paths
  (create bot → appears → pause works).

## Phasing (suggested)

1. **Foundation** — repo scaffold (Next.js + FastAPI + Postgres), broker adapter +
   manual "buy 1 share" path, account/secrets handling, PAPER badge. Proves the Alpaca
   connection end-to-end.
2. **Trailing-stop bot** — engine + risk + scheduler + dashboard for one strategy.
3. **Copy-trade bot** — Capitol Trades scraper + `signals` + copy-trade engine.
4. **Later** — wheel/options strategy, live-money gating, multi-user/auth, LLM insights.

## Legal / disclaimer

Not financial advice. v1 is paper trading (simulated money, real market data). Any
future live-money capability must surface clear disclaimers and explicit user
acknowledgment.
