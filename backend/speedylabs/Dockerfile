# One image, several run commands: api, worker, migrate, seed, db-bootstrap (P17).
# Building once and varying the command keeps the five deployables provably identical.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Pinned, not :latest - a floating builder makes the image non-reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies in their own layer, so source edits do not re-resolve the lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Non-root. The uploads directory is a mount point in Compose and an S3 path in
# production; it is created here so the container works either way.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /var/lib/speedy/uploads \
    && chown -R app:app /var/lib/speedy/uploads
USER app

EXPOSE 8000

# Correct for the `api` command. The worker, migrate, seed and bootstrap commands do not
# serve HTTP, so Compose and Northflank disable or override this per service rather than
# the image pretending one check fits all five.
# python, not curl: the slim base has no curl and adding one to pass a scanner would be
# a new attack surface for no benefit.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
