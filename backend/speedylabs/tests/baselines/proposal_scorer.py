"""Score a guarded mapping proposal against the generator's oracle.

The *same* method `naive_name_matcher.score_against_truth` uses, so "the proposal beats
name matching" is a comparison of like with like rather than two differently-generous
scoreboards. Lives beside the naive matcher for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.generator.source_layout import LAYOUT, SourceField


@dataclass(frozen=True)
class ProposalScore:
    correct: int
    total: int
    per_field: dict[str, bool]
    #: canonical field -> the column proposed, for naming what went wrong.
    proposed: dict[str, str | None]

    @property
    def rate(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def wrong_fields(self) -> dict[str, str]:
        """canonical field -> "proposed X, expected Y", for a failure message that says
        what to look at rather than just a percentage."""
        truth = {f.canonical: f.name for f in LAYOUT if f.canonical is not None}
        return {
            field: f"proposed {self.proposed.get(field) or '(nothing)'}, expected {truth[field]}"
            for field, ok in self.per_field.items()
            if not ok
        }


def score_proposal(
    assignments: dict[str, str], layout: tuple[SourceField, ...] = LAYOUT
) -> ProposalScore:
    """`assignments` is canonical field -> source column, as the guard produced it."""
    truth: dict[str, str] = {f.canonical: f.name for f in layout if f.canonical is not None}
    per_field = {
        canonical: assignments.get(canonical) == true_source
        for canonical, true_source in truth.items()
    }
    return ProposalScore(
        correct=sum(per_field.values()),
        total=len(truth),
        per_field=per_field,
        proposed={canonical: assignments.get(canonical) for canonical in truth},
    )
