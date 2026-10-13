"""A naive, name-only column matcher — the premise test for mvp.md step 0.6.

This is deliberately the simplest thing that could plausibly be called "mapping by
name": normalise case, strip underscores, score string similarity against each of the
16 canonical field names with stdlib `difflib`, and take the best-scoring canonical
field for each source column. No sample-value sniffing, no synonym dictionary, no LLM.

The step's premise is that `source_layout.py`'s cryptic AS/400 column names defeat this
approach badly enough that the tool is solving a real problem rather than restating
`difflib`. A tester, not the builder, owns this file on purpose: the generator's author
grading their own premise test would be marking their own homework.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from src.generator.source_layout import CANONICAL_FIELDS, LAYOUT, SourceField


def _normalise(name: str) -> str:
    """Case-fold and strip underscores — the two cheapest normalisations there are."""
    return name.lower().replace("_", "")


_CANON_NORMALISED: dict[str, str] = {field: _normalise(field) for field in CANONICAL_FIELDS}


def best_canonical_match(source_name: str) -> tuple[str, float]:
    """The single best-scoring canonical field for one source column name, by
    `difflib.SequenceMatcher` ratio on the normalised strings. Ties resolve to
    whichever canonical field sorts first in `CANONICAL_FIELDS` (stable, not favourable
    to either side of the premise)."""
    normalised = _normalise(source_name)
    best_field = CANONICAL_FIELDS[0]
    best_score = -1.0
    for field in CANONICAL_FIELDS:
        score = difflib.SequenceMatcher(None, normalised, _CANON_NORMALISED[field]).ratio()
        if score > best_score:
            best_score = score
            best_field = field
    return best_field, best_score


@dataclass(frozen=True)
class MatchResult:
    #: canonical field -> source column name the matcher assigned it (by best-match
    #: inversion), or None if no source column's best match landed on this field.
    assignments: dict[str, str | None]
    #: canonical field -> (source column, score) for every column whose best match was
    #: this field. Only the first one (layout order) is kept in `assignments`.
    raw_scores: dict[str, tuple[str, float]]


def run_naive_matcher(layout: tuple[SourceField, ...] = LAYOUT) -> MatchResult:
    """Run the naive matcher over every column in `layout`, real and filler alike —
    a name-only matcher has no way to know which columns are reserved.

    When two source columns both best-match the same canonical field (a real risk with
    only 16 short targets and 20-odd cryptic columns), the higher-scoring column wins
    the assignment — the generous reading of "naive", not the unlucky one."""
    assignments: dict[str, str | None] = dict.fromkeys(CANONICAL_FIELDS, None)
    raw_scores: dict[str, tuple[str, float]] = {}
    for field in layout:
        best_field, score = best_canonical_match(field.name)
        current = raw_scores.get(best_field)
        if current is None or score > current[1]:
            raw_scores[best_field] = (field.name, score)
            assignments[best_field] = field.name
    return MatchResult(assignments=assignments, raw_scores=raw_scores)


def score_against_truth(
    result: MatchResult, layout: tuple[SourceField, ...] = LAYOUT
) -> tuple[int, dict[str, bool]]:
    """How many of the 16 canonical fields the naive matcher pointed at their true
    source column. Returns (correct_count, per-field correctness)."""
    truth: dict[str, str] = {f.canonical: f.name for f in layout if f.canonical is not None}
    correctness: dict[str, bool] = {}
    correct = 0
    for canonical, true_source in truth.items():
        matched_source = result.assignments.get(canonical)
        is_correct = matched_source == true_source
        correctness[canonical] = is_correct
        if is_correct:
            correct += 1
    return correct, correctness


if __name__ == "__main__":
    result = run_naive_matcher()
    correct, correctness = score_against_truth(result)
    for canonical, ok in correctness.items():
        print(f"{canonical:20} -> {result.assignments[canonical]!s:12} {'OK' if ok else 'WRONG'}")
    print(
        f"\n{correct}/{len(correctness)} canonical fields correctly mapped "
        f"({correct / len(correctness):.1%})"
    )
