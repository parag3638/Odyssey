"""Environment read and validated once, at startup.

BACKEND.md §2: config has exactly one home. §7: invalid or missing config means refuse
to start, with a clear message naming the variable. That is what `load()` does — it exits
rather than raising, so the operator sees one line per bad variable instead of a
traceback through pydantic.

Every variable here exists in `.env.example`, and nothing in that file is missing here;
a test asserts both directions. Three guards are deliberate and load-bearing:

- the database URL scheme must be `postgresql+psycopg`, because procrastinate has no
  asyncpg connector and an asyncpg URL fails at *worker start* rather than at boot;
- prices are required whenever a model is configured, because a missing price costs every
  call $0.00 and makes the spend cap unreachable while appearing to work;
- `TRUSTED_PROXY_HOPS` may not be 0 outside local, because 0 behind a proxy collapses
  every visitor to one address and silently turns rate limiting off.

Each of those fails loudly at boot. The alternative in all three cases is a system that
looks healthy while a protection is off.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["debug", "info", "warning", "error"]
AppEnv = Literal["local", "ci", "production"]

_REQUIRED_DB_SCHEME = "postgresql+psycopg"


class Settings(BaseSettings):
    """Validated application configuration."""

    model_config = SettingsConfigDict(
        # Only `.env` in the current directory, deliberately. Reaching up to the repo
        # root would make a dotenv file outside a test's control leak into it: a test
        # that unsets a variable to prove a guard fires would silently read the real
        # value and pass. That failure is invisible in CI (no .env there) and only
        # appears locally, which is the worst shape for a test to have.
        #
        # Host-run commands from `backend/` therefore take real environment variables -
        # see the `migrate` and `run` targets in the Makefile. Compose injects them
        # directly via `env_file`, so containers are unaffected.
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Postgres. Also the procrastinate job queue. ---
    database_url: str
    migration_database_url: str

    # --- OpenAI. Absent key is a degraded feature, not a boot failure (decision 14). ---
    openai_api_key: str | None = None
    openai_model: str | None = None
    # Not a Literal: the provider owns this vocabulary, and refusing to start on a value
    # OpenAI accepts would be worse than passing it through for OpenAI to reject.
    llm_reasoning_effort: str = Field(default="low", min_length=1)
    llm_timeout_seconds: int = Field(default=30, gt=0)

    # --- Spend cap. Prices are config because the model id is config. ---
    llm_daily_spend_cap_usd: Decimal = Field(default=Decimal("5.00"), gt=0)
    openai_price_input_per_mtok: Decimal | None = Field(default=None, ge=0)
    openai_price_cached_input_per_mtok: Decimal | None = Field(default=None, ge=0)
    openai_price_output_per_mtok: Decimal | None = Field(default=None, ge=0)

    # --- Uploads ---
    upload_dir: Path = Path("/var/lib/speedy/uploads")
    max_upload_bytes: int = Field(default=67_108_864, gt=0)

    #: BACKEND.md §4's default request body cap, applied to every route except uploads,
    #: which set their own above. 1 MB is generous for a field list: the 200-field
    #: maximum with every field named and every quirk claimed is a few tens of KB.
    max_body_bytes: int = Field(default=1_048_576, gt=0)
    max_upload_rows: int = Field(default=250_000, gt=0)

    # --- Edge protection. There is no sign-in, so these are the whole defence. ---
    rate_limit_upload_per_hour: int = Field(default=20, gt=0)
    rate_limit_llm_per_hour: int = Field(default=10, gt=0)
    #: Spec approvals per hour per IP. Lower than uploads: approving is rare by nature - once
    #: per customer per format, then only when a format changes - and it is the write with the
    #: longest reach, since the result governs every later file.
    rate_limit_spec_approval_per_hour: int = Field(default=10, gt=0)
    ip_hash_salt: str = Field(min_length=16)
    trusted_proxy_hops: int = Field(ge=0)

    # --- App ---
    app_env: AppEnv = "local"
    log_level: LogLevel = "info"
    cors_allowed_origins: str = "http://localhost:3000"
    request_timeout_seconds: int = Field(default=10, gt=0)
    canonical_schema_version: str = Field(default="1.0.0", min_length=1)
    # **False by default.** This is the only destructive route in the system, it has no sign-in in
    # front of it, and BACKEND.md §4 says deny by default. The deployed demo turns it on
    # deliberately; a local checkout does not have it on by accident.
    demo_reset_enabled: bool = False
    rate_limit_demo_reset_per_hour: int = Field(default=3, gt=0)

    @property
    def cors_origins(self) -> list[str]:
        """CORS as an exact list. BACKEND.md §4 forbids `*` with credentials."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key and self.openai_model)

    @model_validator(mode="after")
    def _check_db_scheme(self) -> Settings:
        for name, url in (
            ("DATABASE_URL", self.database_url),
            ("MIGRATION_DATABASE_URL", self.migration_database_url),
        ):
            if not url.startswith(f"{_REQUIRED_DB_SCHEME}://"):
                raise ValueError(
                    f"{name} must use the {_REQUIRED_DB_SCHEME}:// scheme "
                    "(procrastinate has no asyncpg connector, so an asyncpg URL would "
                    "fail at worker start instead of here)"
                )
            # The scheme alone is not enough: a URL that is junk after it parsed fine
            # here and failed at first connect, which is the "looks healthy while a
            # protection is off" shape the other guards exist to prevent.
            parts = urlsplit(url)
            if not parts.hostname:
                raise ValueError(f"{name} has no host")
            if len(parts.path.lstrip("/")) == 0:
                raise ValueError(f"{name} names no database")
        return self

    @model_validator(mode="after")
    def _reject_wildcard_cors(self) -> Settings:
        """BACKEND.md §4 wants an exact list, not `*` tolerated while credentials are off.

        `main.py` sets `allow_credentials=False` today, so `*` is not an active hole --
        but that is one edit away from being one, and the edit would not look dangerous.
        An empty list stays legal: under P17's path-based routing the browser and API
        share an origin, so there is genuinely nothing cross-origin to allow.
        """
        if any(o == "*" for o in self.cors_origins):
            raise ValueError(
                "CORS_ALLOWED_ORIGINS may not contain '*'; list the exact origins "
                "(an empty value is valid when the API and frontend share an origin)"
            )
        return self

    @model_validator(mode="after")
    def _check_prices_present_when_model_set(self) -> Settings:
        """A configured model with no prices makes the daily cap decoration."""
        if self.openai_model is None:
            return self
        missing = [
            name
            for name, value in (
                ("OPENAI_PRICE_INPUT_PER_MTOK", self.openai_price_input_per_mtok),
                ("OPENAI_PRICE_CACHED_INPUT_PER_MTOK", self.openai_price_cached_input_per_mtok),
                ("OPENAI_PRICE_OUTPUT_PER_MTOK", self.openai_price_output_per_mtok),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                f"OPENAI_MODEL is set but {', '.join(missing)} missing; without prices "
                "every call costs $0.00 and LLM_DAILY_SPEND_CAP_USD can never be reached"
            )

        # All three present but all zero has exactly the effect this check exists to
        # prevent, and slips past it by being present. Warned rather than refused,
        # because a genuinely free self-hosted model is a legitimate configuration - but
        # said out loud, since the quiet version is a demo that believes it has a spend
        # cap and does not. `.env.example` ships zeros as placeholders.
        if all(
            price == 0
            for price in (
                self.openai_price_input_per_mtok,
                self.openai_price_cached_input_per_mtok,
                self.openai_price_output_per_mtok,
            )
        ):
            print(
                "WARNING: every OPENAI_PRICE_* is 0.00, so every call costs $0.00 and "
                "LLM_DAILY_SPEND_CAP_USD can never be reached. Set real prices before "
                "relying on the cap.",
                file=sys.stderr,
            )
        return self

    @model_validator(mode="after")
    def _check_proxy_hops(self) -> Settings:
        """0 hops behind a proxy means one rate-limit bucket for the whole internet."""
        if self.app_env != "local" and self.trusted_proxy_hops == 0:
            raise ValueError(
                "TRUSTED_PROXY_HOPS must be at least 1 when APP_ENV is not 'local'; "
                "0 trusts the socket peer, which behind a load balancer is the proxy "
                "itself and collapses every visitor into one rate-limit bucket"
            )
        return self


def _report_and_exit(exc: ValidationError) -> None:
    """Print one line per bad variable, naming it, then exit non-zero.

    Exit 78 is `EX_CONFIG` from sysexits(3) — it distinguishes "you configured me wrong"
    from "I crashed", which matters when a container restarts in a loop.
    """
    lines = ["Configuration error - refusing to start:"]
    for err in exc.errors():
        loc = err.get("loc") or ()
        name = str(loc[0]).upper() if loc else "<configuration>"
        lines.append(f"  {name}: {err['msg']}")
    print("\n".join(lines), file=sys.stderr)
    raise SystemExit(78)


@lru_cache(maxsize=1)
def load() -> Settings:
    """The settings singleton. Read once; never re-read per request."""
    try:
        return Settings()  # pyright: ignore[reportCallIssue]
    except ValidationError as exc:
        _report_and_exit(exc)
        raise  # unreachable; keeps the return type honest for the type checker
