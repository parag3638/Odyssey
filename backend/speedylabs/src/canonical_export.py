"""Export the canonical JSON Schema to a committed artifact, and detect drift.

Same arrangement as `openapi_export.py`, for the same reason: the Pydantic model in
`features/canonical/model.py` is the one source of truth, and this writes the file the
rest of the world reads. `make check` runs it with `--check`, so a field added to the
model without regenerating fails the build naming the stale file rather than shipping a
published schema that quietly describes a different shape from the API.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final

from src.features.attribution.glass_export import schema_document as glass_schema_document
from src.features.canonical.model import schema_document
from src.features.rebates.published import engine_document
from src.features.specs.model import schema_document as spec_schema_document

CANONICAL_DIR: Final = Path(__file__).resolve().parents[1] / "canonical"

#: Every published schema, as `path -> builder`. Two now: the canonical invoice line a customer's IT
#: team reads, and the mapping spec that decides how their files are read. The second was asked
#: for by
#: Phase 9's plan and missed on the first pass - the Phase 8-10 tester found the gap.
#: The canonical invoice line's own path. Kept as a name because the schema is a published contract
#: several tests assert against directly, and `ARTIFACTS[...]` at every call site would read worse.
ARTIFACT: Final = CANONICAL_DIR / "invoice_line.schema.json"
SPEC_ARTIFACT: Final = CANONICAL_DIR / "mapping_spec.schema.json"
#: Not a schema - the rebate engine's identity and rule table. Published through the same mechanism
#: because it needs the same guarantee: the committed file cannot drift from the code that produced
#: a stored number.
ENGINE_ARTIFACT: Final = CANONICAL_DIR / "rebate_engine.json"
#: The attributed-variance export's shape. A **proposed** integration shape, published so adapting
#: to a real downstream tool is a mapping exercise rather than reverse engineering - never a
#: contract anyone has agreed to.
GLASS_ARTIFACT: Final = CANONICAL_DIR / "glass_variance_export.schema.json"

ARTIFACTS: Final = {
    ARTIFACT: schema_document,
    SPEC_ARTIFACT: spec_schema_document,
    ENGINE_ARTIFACT: engine_document,
    GLASS_ARTIFACT: glass_schema_document,
}


def serialise(document: dict[str, Any]) -> str:
    """Canonical JSON, so an unrelated reordering never shows up as a diff."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    checking = "--check" in args
    failures = 0

    for path, builder in ARTIFACTS.items():
        text = serialise(builder())
        if checking:
            if not path.exists():
                print(f"{path.name} is missing; run `make canonical`", file=sys.stderr)
                failures += 1
                continue
            if path.read_text(encoding="utf-8") != text:
                print(
                    f"{path.name} is stale - its model changed without regenerating it. "
                    f"Run `make canonical` and commit the result.",
                    file=sys.stderr,
                )
                failures += 1
                continue
            print(f"{path.name} is current")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.name} ({len(text):,} bytes)")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
