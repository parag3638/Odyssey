"""The readiness oracle: what P8 must find, down to the row number.

This file is the contract between the generator and P8's test suite. It names every
seeded check code with an exact count and the exact rows affected, so "the readiness
report catches every seeded issue" becomes a comparison rather than a judgement.

One rule needs stating because it is what keeps the one-defect-per-line property true:
**the extension check only applies where the extension is computable.** A pound-priced
line whose catch weight has been removed cannot be checked against
`catch_weight_lbs x unit_price` — its input is missing. Counting it as an extension
mismatch as well as a missing weight would make it carry two defects and corrupt both
scores. P8 records it as not-applicable for that check, which is exactly what
`readiness_check_runs` exists to express.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from src.generator.defects import (
    CHECK_CODES,
    CHECK_DATE_GAP,
    CHECK_FOOTER,
    EXTENSION_ERROR_USD,
    FOOTER_DELTA_USD,
    SeededFile,
    build_seeded_file,
)
from src.generator.fixtures import ORACLE_DIR, write_json
from src.generator.seed import SEED

#: Severity as P8 must assign it. Blocking issues stop an ingest; warnings do not.
SEVERITY: Final[dict[str, str]] = {
    "mixed_uom_without_pack_size": "blocking",
    "catch_weight_missing": "blocking",
    "extension_mismatch": "blocking",
    "duplicate_line": "blocking",
    "date_gap": "warning",
    "footer_total_mismatch": "blocking",
    "redistributor_vendor_mismatch": "warning",
}


def build_readiness_oracle(seeded: SeededFile) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for code in CHECK_CODES:
        rows = seeded.affected_rows[code]
        entry: dict[str, Any] = {
            "code": code,
            "severity": SEVERITY[code],
            "count": len(rows),
            "rows": list(rows),
        }
        if code == CHECK_DATE_GAP:
            # An absence has no rows. The count is the number of missing business days.
            entry["count"] = len(seeded.gap_dates)
            entry["missingDates"] = [d.isoformat() for d in seeded.gap_dates]
        if code == CHECK_FOOTER:
            # A file-level defect: one finding, measured in dollars.
            entry["count"] = 1
            entry["deltaUsd"] = f"{FOOTER_DELTA_USD:f}"
        checks.append(entry)

    return {
        "seed": SEED,
        "rowCount": len(seeded.lines),
        "detailTotalUsd": f"{seeded.detail_total:f}",
        "footerTotalUsd": f"{seeded.footer_total:f}",
        "extensionErrorUsd": f"{EXTENSION_ERROR_USD:f}",
        "checks": checks,
    }


def write_readiness_oracle(seeded: SeededFile | None = None) -> Path:
    data = seeded if seeded is not None else build_seeded_file()
    return write_json(ORACLE_DIR / "expected_readiness.json", build_readiness_oracle(data))


def detail_total(seeded: SeededFile) -> Decimal:
    return seeded.detail_total
