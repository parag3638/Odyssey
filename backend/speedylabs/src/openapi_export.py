"""Export the OpenAPI document to a committed artifact, and detect drift.

Committing `openapi.json` buys two things. The frontend can generate its types without
a running backend, which keeps `pnpm check` independent of Docker. And CI can prove the
two have not drifted: a route added without regenerating fails the build naming the
stale file, rather than shipping a frontend whose types quietly describe an API that no
longer exists.

`BACKEND.md` §11 forbids hand-written frontend types that copy the API schema. This is
the machinery that makes generation the only path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final

#: `backend/openapi.json`, resolved from this file so it works from any directory.
ARTIFACT: Final = Path(__file__).resolve().parents[1] / "openapi.json"


def document() -> dict[str, Any]:
    # Imported lazily: building the app reads config, and `--help` should not need a
    # valid environment just to print usage.
    from src.main import create_app

    return create_app().openapi()


def serialise(doc: dict[str, Any]) -> str:
    """Canonical JSON, so an unrelated reordering never shows up as a diff."""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = serialise(document())

    if "--check" in args:
        if not ARTIFACT.exists():
            print(f"{ARTIFACT.name} is missing; run `make openapi`", file=sys.stderr)
            return 1
        if ARTIFACT.read_text(encoding="utf-8") != text:
            print(
                f"{ARTIFACT.name} is stale - a route changed without regenerating it.\n"
                f"Run `make openapi` and commit the result, then regenerate the\n"
                f"frontend types with `pnpm gen:api`.",
                file=sys.stderr,
            )
            return 1
        print(f"{ARTIFACT.name} is current")
        return 0

    ARTIFACT.write_text(text, encoding="utf-8")
    print(f"wrote {ARTIFACT.name} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
