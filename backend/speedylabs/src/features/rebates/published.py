"""The engine's identity and rule table, as a published document.

`canonical/rebate_engine.json` says which engine computed a number and by what rules. It exists for
the same reason `invoice_line.schema.json` does: `make check` regenerates it and fails if the
committed file disagrees with the code, so a change to the rules cannot be committed without
touching the file whose `version` sits beside them. That puts the version bump in the same diff as
the change.

What the machine can prove and what it cannot
---------------------------------------------
It proves the document matches the code. It does **not** prove the version number moved - nothing
can check that a human incremented a string on purpose. Review does that. Said plainly here rather
than implied, because an enforcement claim that is only half true is worse than none.
"""

from __future__ import annotations

from typing import Any

from src.features.rebates.codes import (
    LINE_STATUS_TITLES,
    OUTCOME_SENTENCES,
    SKIPPED_REASON_SENTENCES,
)
from src.features.rebates.reference_engine import ENGINE_NAME, ENGINE_VERSION


def engine_document() -> dict[str, Any]:
    """Everything a reader needs to know what produced a rebate number."""
    return {
        "name": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "standIn": True,
        "note": (
            "A reference stand-in, not Speedy's rebate engine. It implements the two program "
            "kinds the spec names and nothing else: no tiers, minimums, accruals or proration. "
            "Every amount it produces is stored with this name and version."
        ),
        "rounding": {
            "mode": "ROUND_HALF_EVEN",
            "amountScale": 2,
            "basisScale": 4,
            "rule": (
                "One multiplication and one rounding per line, over the unquantised rate and "
                "basis. Period totals are the sum of per-line amounts, never a rounding of an "
                "aggregate basis."
            ),
        },
        "programKinds": [
            {
                "kind": "per_case_volume",
                "uomBasis": "CS",
                "basis": (
                    "Cases: the line qty for a case-priced line, qty / pack size for an "
                    "each-priced one."
                ),
            },
            {
                "kind": "per_lb_billback",
                "uomBasis": "LB",
                "basis": "Pounds: the line's catch weight.",
            },
        ],
        "lineStatuses": [
            {"status": status, "title": title}
            for status, title in sorted(LINE_STATUS_TITLES.items())
        ],
        "skippedReasons": [
            {"reason": reason, "sentence": sentence}
            for reason, sentence in sorted(SKIPPED_REASON_SENTENCES.items())
        ],
        "programOutcomes": [
            {"outcome": outcome, "sentence": sentence}
            for outcome, sentence in sorted(OUTCOME_SENTENCES.items())
        ],
    }
