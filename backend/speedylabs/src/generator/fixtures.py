"""Where the generator writes its committed oracles.

An oracle is a file the tests score against: the layout's true offsets, the seeded
issues, the expected variance attribution. They are committed uncompressed because
tests read them and a diff has to be reviewable — if an oracle changes, that change is
the most important thing in the commit and must be visible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from src.generator.source_layout import record_width, to_oracle

#: `backend/fixtures/` - resolved from this file so the CLI works from any directory.
FIXTURES_DIR: Final = Path(__file__).resolve().parents[2] / "fixtures"
ORACLE_DIR: Final = FIXTURES_DIR / "oracle"


def canonical_json(payload: Any) -> str:
    """Canonical JSON: sorted keys, two-space indent, trailing newline.

    Separate from `write_json` so `--verify` can compare bytes without writing any -
    a verification that mutates the thing it is verifying proves nothing.
    """
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload), encoding="utf-8")
    return path


def write_layout_oracle() -> Path:
    """The layout oracle P4's fixed-width boundary inference is scored against."""
    return write_json(
        ORACLE_DIR / "expected_layout.json",
        {
            "recordWidth": record_width(),
            "columns": to_oracle(),
        },
    )
