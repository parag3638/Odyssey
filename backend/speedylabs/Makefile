# `make check` runs here, in backend/. Commands that need the repo-root .env load it
# explicitly rather than config reaching up for it - see the comment in src/config.py.
ENV_FILE := ../.env

# Optional: CI has no .env and passes the same variables through its own environment.
load_env = set -a; [ ! -f $(ENV_FILE) ] || . $(ENV_FILE); set +a;

check:
	uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest -q
	semgrep scan --config auto --error && trivy fs --scanners vuln,secret,misconfig --exit-code 1 .
	# The committed fixtures are the contract three later test suites score against, so a
	# hand-edited or corrupted one has to fail the build rather than wait to be noticed.
	uv run speedy-generate --verify
	# A route added without regenerating openapi.json would ship a frontend whose
	# generated types describe an API that no longer exists. Fail here instead.
	$(load_env) uv run python -m src.openapi_export --check
	# The canonical schema is a published contract served from a committed file. If the
	# model moves and the file does not, the API and the artifact describe different
	# shapes - and the artifact is what a customer's IT team reads.
	uv run python -m src.canonical_export --check

fmt:
	uv run ruff format . && uv run ruff check --fix .

openapi:
	$(load_env) uv run python -m src.openapi_export

# `.PHONY` matters more than usual here: `canonical/` is a real directory, so without it
# make calls the target up to date and does nothing.
canonical:
	uv run python -m src.canonical_export

migrate:
	$(load_env) uv run alembic upgrade head

downgrade:
	$(load_env) uv run alembic downgrade base

run:
	$(load_env) uv run uvicorn src.main:app --reload

worker:
	$(load_env) uv run procrastinate --app=src.jobs.app.app worker

.PHONY: check fmt openapi canonical migrate downgrade run worker

# A customer plus settled runs for browser checks. `make check` deletes the route tests'
# customer on teardown, so the database is empty of demo data after every gate run.
demo:
	set -a; . ../.env; set +a; uv run python -m scripts.seed_demo $(FILES)
# Seed specific fixtures with FILES="a.csv b.txt"; default is the CSV alone.
# `FULL=1 make demo` seeds the 48,150-row month instead of the 500-row sample - which is what
# the readiness and variance screens need, because the sample carries only a handful of each
# seeded defect. Inherited from the environment, so `FULL=1 make demo FILES="month1_invoices.txt"`
# works too.
.PHONY: demo
