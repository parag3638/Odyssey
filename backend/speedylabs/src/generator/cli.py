"""`speedy-generate` — writes every fixture and oracle the test suite reads.

Two modes:

- **write** (default) regenerates everything from the seed.
- **`--verify`** compares what is on disk against what the seed produces and exits
  non-zero naming any file that differs. `make check` runs this, so a corrupted or
  hand-edited fixture fails loudly instead of being silently regenerated over on
  someone's next run — the committed oracles are the contract three later test suites
  depend on, and a quiet overwrite would hide a broken contract rather than report it.

Byte-identical output matters and is not free: gzip stores a modification time in its
header, so the archives are written with `mtime=0`. Without that, two runs of the same
seed produce different bytes and `git status` is never clean.
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path
from typing import Final

from src.generator.catalog import build_catalog
from src.generator.defects import build_seeded_file
from src.generator.drift import build_drift_oracle, build_month2, drifted_layout
from src.generator.fixtures import FIXTURES_DIR, canonical_json
from src.generator.legacy import build_legacy, build_variance_oracle, write_legacy_csv
from src.generator.mappings import build_mapping_document, build_spec_document
from src.generator.oracle import build_readiness_oracle
from src.generator.rebates import build_rebate_oracle, build_rebate_program_document
from src.generator.reference import build_reference_document
from src.generator.seed import SEED
from src.generator.source_layout import record_width, to_oracle
from src.generator.writers import write_csv, write_fixed_width
from src.lib import canonjson

#: Rows in the plain, human-openable samples. Small enough to read in an editor, big
#: enough to contain examples of every seeded defect class.
SAMPLE_ROWS: Final = 500

#: Each committed archive must stay under this, so a clone stays cheap.
MAX_ARCHIVE_BYTES: Final = 2 * 1024 * 1024


def _gzip_bytes(text: str) -> bytes:
    """Deterministic gzip: no embedded timestamp, fixed compression level."""
    import io

    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as handle:
        handle.write(text.encode("ascii"))
    return buffer.getvalue()


def build_payloads(seed: int = SEED) -> dict[str, bytes]:
    """Every fixture file, as bytes, keyed by path relative to the fixtures directory."""
    catalog = build_catalog(seed)
    month1 = build_seeded_file(seed)
    month2 = build_month2(seed, catalog)
    layout2 = drifted_layout()
    legacy = build_legacy(month1, seed)

    m1_fw = write_fixed_width(month1.lines, month1.footer_total, seed)
    m1_csv = write_csv(month1.lines, seed)
    m2_fw = write_fixed_width(month2.lines, month2.footer_total, seed, layout2)
    m2_csv = write_csv(month2.lines, seed, layout2)
    # The same month-2 **data** written through month 1's **undrifted** layout. This is what A3
    # needs and the drifted file cannot provide: mvp.md asks the same file to both "map itself
    # from the month-1 spec" and to be refused for naming an absent column, which it cannot do.
    # So the drifted file stays P10's material and this one proves mapping memory. No new oracle:
    # the drift oracle is built from month 1 and the drifted month 2 and is untouched.
    m2_nodrift_fw = write_fixed_width(month2.lines, month2.footer_total, seed)
    m2_nodrift_csv = write_csv(month2.lines, seed)

    sample_m1 = month1.lines[:SAMPLE_ROWS]
    sample_m2 = month2.lines[:SAMPLE_ROWS]

    payloads: dict[str, bytes] = {
        # Plain samples: openable in any editor, which is how a reviewer first meets
        # the AS/400 quirks.
        "sample/month1_invoices.txt": write_fixed_width(
            sample_m1, month1.footer_total, seed
        ).encode("ascii"),
        "sample/month1_invoices.csv": write_csv(sample_m1, seed).encode("ascii"),
        "sample/month2_invoices.txt": write_fixed_width(
            sample_m2, month2.footer_total, seed, layout2
        ).encode("ascii"),
        "sample/month2_invoices.csv": write_csv(sample_m2, seed, layout2).encode("ascii"),
        "sample/month2_nodrift_invoices.txt": write_fixed_width(
            sample_m2, month2.footer_total, seed
        ).encode("ascii"),
        "sample/month2_nodrift_invoices.csv": write_csv(sample_m2, seed).encode("ascii"),
        "sample/legacy_rebates.csv": write_legacy_csv(legacy, catalog).encode("ascii"),
        # Full files: gzipped, because 15 MB of ASCII in a clone is rude.
        "full/month1_invoices.txt.gz": _gzip_bytes(m1_fw),
        "full/month1_invoices.csv.gz": _gzip_bytes(m1_csv),
        "full/month2_invoices.txt.gz": _gzip_bytes(m2_fw),
        "full/month2_invoices.csv.gz": _gzip_bytes(m2_csv),
        "full/month2_nodrift_invoices.txt.gz": _gzip_bytes(m2_nodrift_fw),
        "full/month2_nodrift_invoices.csv.gz": _gzip_bytes(m2_nodrift_csv),
    }
    return payloads


def build_reference_payloads(seed: int = SEED) -> dict[str, bytes]:
    """Reference data the demo seeder loads. Not an oracle - it is an *input* to the
    product, where an oracle is an answer the product is scored against - so it is built
    and verified separately, under its own directory."""
    return {
        "reference/customer_manufacturers.json": canonical_json(
            build_reference_document(build_catalog(seed))
        ).encode("utf-8"),
        "reference/rebate_programs.json": canonical_json(
            build_rebate_program_document(build_catalog(seed))
        ).encode("utf-8"),
        "reference/mapping_csv.json": canonical_json(build_mapping_document()).encode("utf-8"),
        # The production serialiser, not the generator's own. This payload is the exact document a
        # `mapping_specs` row stores, and `spec_sha256` is computed over it by `lib/canonjson` -
        # which additionally sorts each `map_values` table by `from` (docs/decisions.md, P9). The
        # generator's `canonical_json` does not, so emitting this one with it would commit a
        # fixture that production hashes differently from the bytes on disk.
        "reference/mapping_spec.json": (canonjson.dumps(build_spec_document()).encode("utf-8")),
    }


def build_oracle_payloads(seed: int = SEED) -> dict[str, bytes]:
    """The five oracles, as bytes, built in memory so `--verify` writes nothing."""
    catalog = build_catalog(seed)
    month1 = build_seeded_file(seed)
    month2 = build_month2(seed, catalog)
    legacy = build_legacy(month1, seed)
    return {
        "oracle/expected_layout.json": canonical_json(
            {"recordWidth": record_width(), "columns": to_oracle()}
        ).encode("utf-8"),
        "oracle/expected_readiness.json": canonical_json(build_readiness_oracle(month1)).encode(
            "utf-8"
        ),
        "oracle/expected_drift.json": canonical_json(
            build_drift_oracle(month1.lines, month2)
        ).encode("utf-8"),
        "oracle/expected_variance_attribution.json": canonical_json(
            build_variance_oracle(legacy)
        ).encode("utf-8"),
        "oracle/expected_rebate_calcs.json": canonical_json(build_rebate_oracle(month1)).encode(
            "utf-8"
        ),
    }


def all_payloads(seed: int = SEED) -> dict[str, bytes]:
    return {
        **build_payloads(seed),
        **build_oracle_payloads(seed),
        **build_reference_payloads(seed),
    }


def generate(out: Path) -> list[Path]:
    written: list[Path] = []
    for relative, payload in sorted(all_payloads().items()):
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        written.append(path)
    return written


def verify(out: Path) -> list[str]:
    """Differences between disk and the seed. Writes nothing.

    Names the offending file, so a corrupted or hand-edited oracle is reported rather
    than quietly overwritten the next time someone regenerates.
    """
    problems: list[str] = []
    expected = all_payloads()
    for relative, payload in sorted(expected.items()):
        path = out / relative
        if not path.exists():
            problems.append(f"{relative}: missing")
        elif path.read_bytes() != payload:
            problems.append(f"{relative}: differs from the seed's output")
    for path in sorted(out.rglob("*")):
        if path.is_file():
            relative = str(path.relative_to(out))
            if relative not in expected:
                problems.append(f"{relative}: not produced by the generator")
    return problems


def check_sizes(out: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted((out / "full").glob("*.gz")):
        size = path.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            problems.append(f"full/{path.name}: {size / 1e6:.1f} MB exceeds the 2 MB cap")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="speedy-generate", description=__doc__)
    parser.add_argument("--out", type=Path, default=FIXTURES_DIR, help="fixtures directory")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="compare on-disk fixtures with the seed's output; write nothing",
    )
    args = parser.parse_args(argv)
    out: Path = args.out

    if args.verify:
        problems = verify(out) + check_sizes(out)
        if problems:
            print("fixture verification FAILED:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print(f"fixtures verified against seed {SEED}")
        return 0

    written = generate(out)
    problems = check_sizes(out)
    if problems:
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path.relative_to(out.parent)} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
