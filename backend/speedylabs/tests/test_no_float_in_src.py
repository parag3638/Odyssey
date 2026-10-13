"""Step 0.5 acceptance criterion, verbatim: "Given a grep of `backend/src` for
`float(`, `: float` and `Float(`, then zero hits outside explicitly-allowed profiling
statistics." BACKEND.md §11 and the project spec's core principle both forbid floats
for money outright; this is the blanket check behind the targeted refusal tests in
`test_money.py`.

A word-boundary regex is used for `float(` rather than a plain substring search:
`src/lib/money.py`'s own guard was once named `_refuse_float`, whose definition
(`_refuse_float(`) contains the literal substring `float(` and would have falsely
tripped a naive grep on the very function that enforces the no-float rule. It has
since been renamed to `_reject_float_value`, which no longer collides - but the
word-boundary form stays robust regardless of what anything is named next.

No profiling-statistics exception exists today (the grep is zero hits outright); if
one is ever added deliberately, it should be carved out here by name, not by loosening
the pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

_PATTERNS = {
    "float(": re.compile(r"\bfloat\("),
    ": float": re.compile(r": ?float\b"),
    "Float(": re.compile(r"\bFloat\("),
}


def _matches_in_src() -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {name: [] for name in _PATTERNS}
    for path in sorted(SRC_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, pattern in _PATTERNS.items():
                if pattern.search(line):
                    hits[name].append(
                        f"{path.relative_to(SRC_DIR.parent)}:{line_no}: {line.strip()}"
                    )
    return hits


def test_src_contains_no_float_literal_calls() -> None:
    hits = _matches_in_src()["float("]
    assert hits == [], f"float( found outside an allowed exception: {hits}"


def test_src_contains_no_float_type_annotations() -> None:
    hits = _matches_in_src()[": float"]
    assert hits == [], f": float found outside an allowed exception: {hits}"


def test_src_contains_no_sqlalchemy_float_column_type() -> None:
    hits = _matches_in_src()["Float("]
    assert hits == [], f"Float( found outside an allowed exception: {hits}"


def test_the_word_boundary_pattern_does_not_self_trip_on_the_money_guard() -> None:
    """Pins the exact regression named in the docstring: a naive substring search for
    `float(` would flag `_reject_float_value(`'s own definition and call sites."""
    pattern = _PATTERNS["float("]
    assert pattern.search("_reject_float_value(value, name)") is None
    assert pattern.search("def _reject_float_value(value: object, name: str) -> None:") is None
    # and it must still catch a real, disallowed call
    assert pattern.search("x = float(value)") is not None
