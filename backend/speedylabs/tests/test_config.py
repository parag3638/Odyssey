"""src/config.py (BACKEND.md §7): invalid or missing config refuses to start, naming
the variable, with exit code 78 (`EX_CONFIG`). Step 0.3 grew this from 8 fields to 23
and added three cross-field `model_validator`s (DB URL scheme, OpenAI prices required
alongside a model, proxy hops required outside local) - this file covers both the
original guard and the three new ones.

This can't be exercised in-process: `load()` is `lru_cache`d (so a prior test's
already-loaded settings would mask a missing variable), and a bad config calls
`SystemExit(78)` directly, which would tear down the whole test run rather than fail
one test. Each case instead runs `from src.config import load; load()` as a real
subprocess with its own, fully-controlled environment - the same shape as "a clean
checkout, DATABASE_URL unset" from the acceptance criteria.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# A URL both database-URL fields accept: right scheme, otherwise meaningless (nothing
# ever connects to it in these tests - `load()` only parses and validates the string).
VALID_DB_URL = "postgresql+psycopg://x:x@127.0.0.1:1/x"


def _run_load(
    *, unset: Iterable[str] = (), **env_overrides: str
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for key in unset:
        env.pop(key, None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "from src.config import load; load()"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_exits_78_naming_database_url_when_only_it_is_unset() -> None:
    result = _run_load(
        unset=["DATABASE_URL"],
        MIGRATION_DATABASE_URL=VALID_DB_URL,
    )

    assert result.returncode == 78
    assert "DATABASE_URL" in result.stderr
    assert "MIGRATION_DATABASE_URL" not in result.stderr
    assert result.stdout == ""


def test_exits_78_naming_every_missing_variable_at_once() -> None:
    result = _run_load(unset=["DATABASE_URL", "MIGRATION_DATABASE_URL"])

    assert result.returncode == 78
    assert "DATABASE_URL" in result.stderr
    assert "MIGRATION_DATABASE_URL" in result.stderr


def test_no_stack_trace_reaches_stdout_or_stderr() -> None:
    result = _run_load(unset=["DATABASE_URL", "MIGRATION_DATABASE_URL"])

    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_succeeds_with_both_required_variables_set() -> None:
    result = _run_load(DATABASE_URL=VALID_DB_URL, MIGRATION_DATABASE_URL=VALID_DB_URL)

    assert result.returncode == 0, result.stderr


# --- step 0.3: IP_HASH_SALT and TRUSTED_PROXY_HOPS are now required too ---------------


def test_exits_78_naming_ip_hash_salt_when_it_is_unset() -> None:
    result = _run_load(unset=["IP_HASH_SALT"])

    assert result.returncode == 78
    assert "IP_HASH_SALT" in result.stderr


def test_exits_78_when_ip_hash_salt_is_under_16_characters() -> None:
    """Floor raised 8 -> 16 after the 0.1/0.3 review round: 8 random bytes was thin for
    a salt meant to defeat precomputation against the 2^32 IPv4 keyspace."""
    result = _run_load(IP_HASH_SALT="123456789012345")  # 15 chars

    assert result.returncode == 78
    assert "IP_HASH_SALT" in result.stderr


def test_succeeds_with_a_16_character_ip_hash_salt() -> None:
    result = _run_load(IP_HASH_SALT="1234567890123456")  # 16 chars, the new floor

    assert result.returncode == 0, result.stderr


def test_exits_78_naming_trusted_proxy_hops_when_it_is_unset() -> None:
    result = _run_load(unset=["TRUSTED_PROXY_HOPS"])

    assert result.returncode == 78
    assert "TRUSTED_PROXY_HOPS" in result.stderr


# --- the database URL scheme guard ----------------------------------------------------


def test_exits_78_when_database_url_uses_the_asyncpg_scheme() -> None:
    """procrastinate has no asyncpg connector; this must fail at boot, not at worker
    start (BACKEND.md §7, and the module docstring's whole reason for existing)."""
    result = _run_load(DATABASE_URL="postgresql+asyncpg://x:x@127.0.0.1:1/x")

    assert result.returncode == 78
    assert "postgresql+psycopg" in result.stderr


def test_exits_78_when_migration_database_url_uses_the_asyncpg_scheme() -> None:
    result = _run_load(MIGRATION_DATABASE_URL="postgresql+asyncpg://x:x@127.0.0.1:1/x")

    assert result.returncode == 78
    assert "postgresql+psycopg" in result.stderr


def test_exits_78_when_database_url_has_no_host() -> None:
    result = _run_load(DATABASE_URL="postgresql+psycopg:///somedb")

    assert result.returncode == 78
    assert "DATABASE_URL" in result.stderr
    assert "host" in result.stderr.lower()


def test_exits_78_when_database_url_names_no_database() -> None:
    result = _run_load(DATABASE_URL="postgresql+psycopg://speedy@localhost:5432")

    assert result.returncode == 78
    assert "DATABASE_URL" in result.stderr
    assert "database" in result.stderr.lower()


def test_succeeds_with_a_syntactically_valid_but_unreachable_host() -> None:
    """Deliberate residual gap, confirmed rather than assumed: a URL with a host and a
    database name that are syntactically fine but wrong (nothing listens there, or the
    name doesn't exist) is not caught at boot - only at connect time, i.e. /readyz.
    Proving reachability is /readyz's job, not config's; this test pins that boundary
    so a future change narrowing it is a deliberate decision, not a silent one."""
    result = _run_load(DATABASE_URL="postgresql+psycopg://nobody:nobody@127.0.0.1:59999/nothing")

    assert result.returncode == 0, result.stderr


# --- CORS: an exact list, never a wildcard --------------------------------------------


def test_exits_78_when_cors_allowed_origins_is_a_bare_wildcard() -> None:
    result = _run_load(CORS_ALLOWED_ORIGINS="*")

    assert result.returncode == 78
    assert "CORS_ALLOWED_ORIGINS" in result.stderr


def test_exits_78_when_cors_allowed_origins_mixes_a_real_origin_with_a_wildcard() -> None:
    result = _run_load(CORS_ALLOWED_ORIGINS="http://localhost:3000,*")

    assert result.returncode == 78
    assert "CORS_ALLOWED_ORIGINS" in result.stderr


def test_succeeds_with_an_empty_cors_allowed_origins() -> None:
    """Legal on purpose: under P17's path-based routing the API and frontend share an
    origin, so an empty list is a real, intended configuration, not an oversight."""
    result = _run_load(CORS_ALLOWED_ORIGINS="")

    assert result.returncode == 0, result.stderr


def test_succeeds_with_a_real_origin_list() -> None:
    result = _run_load(CORS_ALLOWED_ORIGINS="http://localhost:3000,https://app.example.com")

    assert result.returncode == 0, result.stderr


# --- OpenAI prices required whenever a model is configured -----------------------------


def test_exits_78_when_a_model_is_set_with_no_prices() -> None:
    result = _run_load(OPENAI_MODEL="gpt-5")

    assert result.returncode == 78
    assert "OPENAI_PRICE_INPUT_PER_MTOK" in result.stderr
    assert "OPENAI_PRICE_CACHED_INPUT_PER_MTOK" in result.stderr
    assert "OPENAI_PRICE_OUTPUT_PER_MTOK" in result.stderr


def test_exits_78_when_a_model_is_set_with_only_some_prices() -> None:
    result = _run_load(OPENAI_MODEL="gpt-5", OPENAI_PRICE_INPUT_PER_MTOK="1.00")

    assert result.returncode == 78
    assert "OPENAI_PRICE_CACHED_INPUT_PER_MTOK" in result.stderr
    assert "OPENAI_PRICE_OUTPUT_PER_MTOK" in result.stderr
    # already supplied, so it must not be reported as missing
    assert "OPENAI_PRICE_INPUT_PER_MTOK" not in result.stderr


def test_succeeds_when_a_model_is_set_with_all_three_prices() -> None:
    result = _run_load(
        OPENAI_MODEL="gpt-5",
        OPENAI_PRICE_INPUT_PER_MTOK="1.00",
        OPENAI_PRICE_CACHED_INPUT_PER_MTOK="0.50",
        OPENAI_PRICE_OUTPUT_PER_MTOK="2.00",
    )

    assert result.returncode == 0, result.stderr


def test_succeeds_with_no_model_and_no_prices() -> None:
    """Decision 14: an absent key/model is a degraded feature, not a boot failure."""
    result = _run_load()

    assert result.returncode == 0, result.stderr


# --- proxy hops required outside local --------------------------------------------------


def test_exits_78_when_app_env_is_production_and_proxy_hops_is_zero() -> None:
    result = _run_load(APP_ENV="production", TRUSTED_PROXY_HOPS="0")

    assert result.returncode == 78
    assert "TRUSTED_PROXY_HOPS" in result.stderr


def test_succeeds_when_app_env_is_production_and_proxy_hops_is_one() -> None:
    result = _run_load(APP_ENV="production", TRUSTED_PROXY_HOPS="1")

    assert result.returncode == 0, result.stderr


def test_succeeds_when_app_env_is_local_and_proxy_hops_is_zero() -> None:
    result = _run_load(APP_ENV="local", TRUSTED_PROXY_HOPS="0")

    assert result.returncode == 0, result.stderr


# --- a plain type error also names the variable and the expected type ------------------


def test_exits_78_naming_max_upload_bytes_and_the_expected_type_when_not_an_integer() -> None:
    result = _run_load(MAX_UPLOAD_BYTES="abc")

    assert result.returncode == 78
    assert "MAX_UPLOAD_BYTES" in result.stderr
    assert "integer" in result.stderr.lower()


# --- .env.example <-> Settings fields: both directions ----------------------------------


def _env_example_variable_names() -> set[str]:
    names: set[str] = set()
    for line in (REPO_ROOT / ".env.example").read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        names.add(stripped.split("=", 1)[0].strip())
    return names


def _settings_field_env_names() -> set[str]:
    from src.config import Settings

    return {name.upper() for name in Settings.model_fields}


def test_every_env_example_variable_is_a_settings_field() -> None:
    extra_in_file = _env_example_variable_names() - _settings_field_env_names()

    assert extra_in_file == set(), (
        f"{sorted(extra_in_file)} are in .env.example but config.py never reads them"
    )


def test_settings_declares_no_field_absent_from_env_example() -> None:
    extra_in_config = _settings_field_env_names() - _env_example_variable_names()

    assert extra_in_config == set(), (
        f"{sorted(extra_in_config)} are config fields with no line in .env.example"
    )


def test_settings_has_exactly_26_fields() -> None:
    """Pins the count from the handover so a silent field addition/removal shows up as
    a changed number here, not just as a passing set-equality check above.

    23 -> 24 on 2026-10-06: `MAX_BODY_BYTES`, BACKEND.md §4's request body cap. It was
    enforced only inside `POST /v1/uploads` until P4's layout PATCH added a second
    body-accepting route and did not inherit it.

    24 -> 25 on 2026-10-07: `RATE_LIMIT_SPEC_APPROVAL_PER_HOUR` for P9's approval route. This
    test and its sibling both caught the addition before it shipped — the number here, and the
    missing line in `.env.example`, which is the half that would actually have bitten somebody
    deploying.

    25 -> 26 on 2026-10-08: `RATE_LIMIT_DEMO_RESET_PER_HOUR` for P15's reset route. Caught the
    same way and in the same halves as the entry above: the field existed in `config.py` with a
    working default, so nothing failed at runtime, and the `.env.example` line was simply absent.
    """
    assert len(_settings_field_env_names()) == 26


# --- regression: config must not read a dotenv from a parent directory ----------------


def test_does_not_read_a_dotenv_from_the_parent_directory(tmp_path: Path) -> None:
    """`env_file` briefly became `("../.env", ".env")` so host-run alembic could find
    the repo-root `.env`. That let a parent dotenv silently supply a variable a test
    had unset to prove a guard fires - the test passed locally (where the repo-root
    `.env` exists) and would have failed in CI (where it doesn't), which is the worst
    direction for that difference to run.

    Reproduced here without touching the real repo-root `.env`: a fake "backend"
    directory with no dotenv of its own, one level under a fake "repo root" that does
    have one. `config.load()` must still exit 78 naming DATABASE_URL - picking up the
    parent file's value instead would be exactly the regression.
    """
    repo_root_like = tmp_path
    backend_like = repo_root_like / "backend"
    backend_like.mkdir()
    (repo_root_like / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://leaked:leaked@127.0.0.1:1/leaked\n"
    )

    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.setdefault("MIGRATION_DATABASE_URL", VALID_DB_URL)
    env.setdefault("IP_HASH_SALT", "1234567890123456")
    env.setdefault("TRUSTED_PROXY_HOPS", "0")

    result = subprocess.run(
        [sys.executable, "-c", "from src.config import load; load()"],
        cwd=backend_like,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 78
    assert "DATABASE_URL" in result.stderr
    assert "leaked" not in result.stderr
