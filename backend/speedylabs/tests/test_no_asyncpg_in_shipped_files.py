"""Step 0.3 acceptance criterion, scoped: "a grep of the whole repo finds no `+asyncpg`
string" cannot pass literally - `docs/decisions.md` and `docs/plans/mvp.md` both
contain that exact string *while explaining why the project does not use it*. Grepping
those out of existence would mean deleting the record of the decision, which is a
worse outcome than the criterion being imprecise.

This is the scoped version: every file that ships (is read by the app, a container
build, or Compose) must not contain `+asyncpg`. `docs/` is exempted because it exists
to explain decisions, not to run; `tests/` is exempted because this file's own test
constants are deliberately-bad asyncpg URLs used to prove the config guard rejects
them (`test_config.py`), not a real connection string.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

EXEMPT_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    "docs",
    "tests",
}

# Binary / non-text extensions not worth opening as text.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf"}

NEEDLE = "+asyncpg"


def _shipped_files() -> list[Path]:
    found: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if EXEMPT_DIR_NAMES & set(path.relative_to(REPO_ROOT).parts):
            continue
        found.append(path)
    return found


def test_no_shipped_file_contains_the_asyncpg_scheme_string() -> None:
    offenders: list[str] = []
    for path in _shipped_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if NEEDLE in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == [], f"{NEEDLE!r} found in shipped files: {offenders}"


def test_the_literal_whole_repo_criterion_would_fail_on_the_documented_decision() -> None:
    """Pins the reason the criterion needed scoping, so this doesn't silently stop being
    true (e.g. if the decision docs get rewritten without the string, this test's
    *purpose* - not just its assertion - should be revisited)."""
    decisions = (REPO_ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    plan = (REPO_ROOT / "docs" / "plans" / "mvp.md").read_text(encoding="utf-8")

    assert NEEDLE in decisions
    assert NEEDLE in plan
