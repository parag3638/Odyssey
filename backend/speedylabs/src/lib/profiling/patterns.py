"""Pattern signatures: what a column's values *look* like, independent of content.

A signature replaces every digit with `9` and every letter with `A`, keeping punctuation
and case-folding nothing else. `ABC-001` becomes `AAA-999`. Two values share a signature
when they share a shape.

This is the single most useful thing to show a human mapping an opaque column. `UMCODE`
tells you nothing; `AA` appearing 49,000 times and `A` appearing 1,100 tells you it is a
two-letter code with a one-letter variant, and the sample values then tell you which.

Signatures are also how a column's *shape* can be compared across files in P10 without
comparing its values - which matters because the values legitimately change every month
while the shape should not.
"""

from __future__ import annotations

from collections import Counter
from typing import Final

#: Longer than this and a signature stops being a shape and becomes a transcript. Free
#: text would otherwise produce one unique signature per row and crowd out real shapes.
MAX_SIGNATURE_LENGTH: Final = 40

#: How many distinct signatures to track per column. An adversarial free-text column has
#: one signature per row; this keeps the counter bounded without affecting a real column,
#: which has a handful.
MAX_TRACKED_SIGNATURES: Final = 200


def signature(value: str) -> str:
    """The shape of one value.

    Truncated with a trailing `...` rather than silently cut, so a reader can tell the
    difference between a 40-character shape and a longer one.
    """
    out: list[str] = []
    for index, char in enumerate(value):
        if index >= MAX_SIGNATURE_LENGTH:
            out.append("...")
            break
        if char.isdigit():
            out.append("9")
        elif char.isalpha():
            out.append("A")
        elif char == " ":
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


class PatternCounter:
    """Counts signatures, with a hard cap on how many distinct ones it will track."""

    def __init__(self, max_tracked: int = MAX_TRACKED_SIGNATURES) -> None:
        self._counts: Counter[str] = Counter()
        self._max_tracked = max_tracked
        #: Values whose signature arrived after the cap was reached. Reported so a reader
        #: knows the top-five list is drawn from a sample, not from everything.
        self.untracked = 0

    def add(self, value: str) -> None:
        shape = signature(value)
        if shape in self._counts or len(self._counts) < self._max_tracked:
            self._counts[shape] += 1
        else:
            self.untracked += 1

    def top(self, limit: int = 5) -> list[dict[str, object]]:
        """The most common shapes, most frequent first.

        Ties break on the signature text so the output is stable across runs - a
        profile that reorders between identical runs would make drift detection
        report changes that are not there.
        """
        ordered = sorted(self._counts.items(), key=lambda item: (-item[1], item[0]))
        return [{"signature": shape, "count": count} for shape, count in ordered[:limit]]
