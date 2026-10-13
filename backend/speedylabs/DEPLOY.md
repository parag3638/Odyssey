# Deploying the Speedy Onboard backend

This folder is self-contained: its own `Dockerfile`, `pyproject.toml`/`uv.lock`, Alembic migrations
and fixtures. Build it with **this folder as the root / build context**. The source of truth is
`parag3638/speedylabs` (`backend/`); a copy lives in `parag3638/Odyssey` at `backend/speedylabs/`,
refreshed with `scripts/sync-to-odyssey.sh` from the speedylabs repo. Edit in speedylabs, then sync.

## Three processes, one image

| Process | Command | Notes |
|---|---|---|
| migrate (pre-deploy / release step) | `alembic upgrade head` | Needs `MIGRATION_DATABASE_URL` (the migrator role). Run before api/worker start. |
| api (web service) | `uvicorn src.main:app --host 0.0.0.0 --port 8000` | The image's default `CMD`. If your platform injects `$PORT`, override with `--port $PORT`. Health: `GET /healthz` (alive), `GET /readyz` (DB reachable). |
| worker (background service) | `procrastinate --app=src.jobs.app.app worker` | Required: upload parsing, profiling, mapping, rebates and attribution all run here. No HTTP port. |

## Postgres 16, two roles

The app connects as a role with no DDL rights; migrations run as a separate owner role. Create both
once, as an admin, with the committed script:

```
psql "$ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -v app_role=speedy -v app_password='…' \
  -v migrator_role=speedy_migrator -v migrator_password='…' \
  -f sql/roles.sql
```

Use a **separate database** from any other app's: migrations here manage their own schema
(`procrastinate` tables included).

## Storage

Uploads are written to `UPLOAD_DIR` (default in the image: `/var/lib/speedy/uploads`). **api and
worker must share it** — mount the same persistent volume on both. Platforms without shared
volumes need the object-storage swap (`src/lib/storage.py`, the `Storage` seam), which is not
built yet.

## Environment

Required — the app refuses to start without them, naming the variable:

- `DATABASE_URL` — `postgresql+psycopg://speedy:…@host:5432/db` (app role)
- `MIGRATION_DATABASE_URL` — same database, migrator role (migrate step; harmless on api/worker)
- `IP_HASH_SALT` — any long random string; never reuse another app's
- `APP_ENV=production` — hides `/docs`
- `TRUSTED_PROXY_HOPS` — number of proxies in front (usually `1` behind a platform load balancer).
  `0` is refused outside `local`.
- `CORS_ALLOWED_ORIGINS` — the frontend's exact origin(s), comma-separated; never `*`

OpenAI (optional — without it, mapping proposals degrade to manual mapping):

- `OPENAI_API_KEY`, `OPENAI_MODEL` (e.g. `gpt-5.5`), and **all three prices**
  `OPENAI_PRICE_INPUT_PER_MTOK`, `OPENAI_PRICE_CACHED_INPUT_PER_MTOK`,
  `OPENAI_PRICE_OUTPUT_PER_MTOK` (USD per million tokens). A model without prices is refused at
  startup; the daily cap (`LLM_DAILY_SPEND_CAP_USD`) is computed from them.

Everything else has a default; `.env.example` in the speedylabs repo lists them all.

## Before it is public — read this

The demo has **no sign-in** (a deliberate decision for a local demo). On a public URL:

- `POST /v1/demo/reset` deletes and rebuilds the demo data. It is off unless
  `DEMO_RESET_ENABLED=true` (the default is `false`; local `.env` turns it on) — leave it off in
  public, and seed once by enabling it, calling it, then disabling it again.
- `POST /v1/source-files/{id}/mapping/propose` makes **paid OpenAI calls**. It is rate-limited
  per IP (`RATE_LIMIT_LLM_PER_HOUR`) and capped per day (`LLM_DAILY_SPEND_CAP_USD`, default $5),
  and successful answers are cached — but leave the key unset if the deployment is public and
  unauthenticated, or put the service behind your platform's auth.
- Uploads accept files up to `MAX_UPLOAD_BYTES` (64 MB default); lower it on a small instance.

## Frontend

The Next.js frontend stays in the speedylabs repo (`frontend/`). Point it at this service with
`NEXT_PUBLIC_API_BASE_URL=https://<this service>` at build time, and add that frontend's origin to
`CORS_ALLOWED_ORIGINS` here.
