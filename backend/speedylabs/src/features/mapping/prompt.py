"""Building the prompt, and the two cache keys that must not be confused.

The premise of this phase, stated once
--------------------------------------
This file's column names are `IVHDR01`, `UMCODE`, `FLD017`, `CLSCD1` - and `CUSTNO`,
which is the ship-to account where `customer_id` wants the bill-to `BILTNO`. A naive
name matcher scores under 40% on it (measured in P0). So the *samples and patterns* are
the signal and the name is weak evidence, and the prompt is built to say so: each column
block puts its type, pattern and five sample values **before** its name, and the
instructions state plainly that the names come from a custom AS/400 ERP, are cryptic, and
may actively mislead.

The Phase 5 measurement makes one of those instructions load-bearing rather than polite.
`CUSTNO` and `BILTNO` are *indistinguishable* from the file alone - 25 distinct six-digit
values each, same pattern, a perfect 1:1 pairing, both determining the customer name. The
model will have exactly the evidence we have, which is none. So it is asked to report the
ambiguity in `ambiguities` and to keep its confidence honest, rather than to pick and
sound sure. A high-confidence wrong answer here would misattribute every rebate on the
file and reconcile perfectly against the wrong customer.

Two caches, and they are not the same thing
-------------------------------------------
* **`prompt_cache_key`** is the provider's prefix cache. It is derived from the schema
  version, the prompt template version and the model - *nothing file-specific*, because a
  per-file key would defeat prefix caching entirely. It buys a discount and some latency.
  Best-effort: never asserted, never relied on.
* **`cache_key`** is our own row in `mapping_proposals`. It includes the file's digest and
  layout version, because a boundary edit genuinely changes the right answer. It buys the
  property the demo depends on: a repeat proposal costs exactly nothing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Final, cast

from src.features.canonical.model import CANONICAL_SCHEMA_VERSION
from src.features.mapping.proposal_schema import PROMPT_TEMPLATE_VERSION
from src.features.mapping.transforms import DATE_FORMATS, TRANSFORM_KINDS

#: Samples are truncated to this, matching the profiler's own `SAMPLE_LENGTH`. The cap is
#: a privacy boundary as much as a size one: it is the most of any one cell that leaves
#: this system.
MAX_SAMPLE_CHARS: Final = 64
#: Samples per column. Five is what the profiler stores and what the plan specifies.
MAX_SAMPLES_PER_COLUMN: Final = 5
#: Columns per prompt. Past this the proposal would truncate rather than help, and the
#: route says so instead of pretending.
MAX_COLUMNS: Final = 200
#: Pattern signatures per column.
MAX_PATTERNS_PER_COLUMN: Final = 3


@dataclass(frozen=True)
class ColumnBlock:
    """One column as the model sees it. Field order here is the order it is rendered in,
    and that order is deliberate: evidence first, name last."""

    type_guess: str
    null_pct: str
    distinct_count: int
    patterns: tuple[str, ...]
    samples: tuple[str, ...]
    name: str


def _truncate(value: str) -> str:
    text = value.strip()
    return text if len(text) <= MAX_SAMPLE_CHARS else text[:MAX_SAMPLE_CHARS]


def column_blocks(profile_columns: list[dict[str, Any]]) -> list[ColumnBlock]:
    """Turn stored column profiles into prompt blocks, bounded in every dimension."""
    blocks: list[ColumnBlock] = []
    for column in profile_columns[:MAX_COLUMNS]:
        # Cast at the boundary: these came out of jsonb, so every value is `Any`, and
        # narrowing with isinstance leaves pyright holding `Unknown` instead.
        pattern_entries = cast("list[dict[str, Any]]", column.get("patterns") or [])
        sample_values = cast("list[Any]", column.get("sample_values") or [])
        patterns = [
            str(entry.get("signature", "")) for entry in pattern_entries[:MAX_PATTERNS_PER_COLUMN]
        ]
        samples = [_truncate(str(value)) for value in sample_values[:MAX_SAMPLES_PER_COLUMN]]
        blocks.append(
            ColumnBlock(
                type_guess=str(column.get("type_guess", "")),
                null_pct=f"{column.get('null_pct', 0):f}",
                distinct_count=int(column.get("distinct_count", 0) or 0),
                patterns=tuple(patterns),
                samples=tuple(samples),
                name=str(column.get("column_name", "")),
            )
        )
    return blocks


def render_columns(blocks: list[ColumnBlock]) -> str:
    """The volatile half of the prompt: this file's columns and nothing else.

    No file path, no run id, no environment value - only what the profiler already
    computed and the samples it already truncated.
    """
    lines: list[str] = []
    for index, block in enumerate(blocks, start=1):
        lines.append(f"Column {index}")
        lines.append(f"  inferred type: {block.type_guess}")
        lines.append(f"  empty: {block.null_pct} of rows, {block.distinct_count} distinct values")
        if block.patterns:
            lines.append(f"  value shapes: {', '.join(block.patterns)}")
        if block.samples:
            lines.append(f"  sample values: {' | '.join(block.samples)}")
        else:
            lines.append("  sample values: (none - the column is empty)")
        # Last on purpose. See the module docstring.
        lines.append(f"  column name (weak evidence): {block.name}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_instructions(canonical_fields: list[dict[str, Any]]) -> str:
    """The stable half: the canonical schema, the transform vocabulary, and the rules.

    Stable so the provider's prefix cache can do its job, and in `instructions` rather
    than prepended to `input` so the separation is structural rather than a lucky
    ordering.
    """
    field_lines: list[str] = []
    for field in canonical_fields:
        required = "required" if field.get("required") else "optional"
        enum = field.get("enum")
        suffix = f", one of {', '.join(enum)}" if enum else ""
        field_lines.append(
            f"- {field['name']} ({field.get('type', 'string')}, {required}{suffix}):"
            f" {field.get('description', '')}"
        )

    return f"""\
You map columns from a foodservice distributor's ERP export onto a fixed canonical
invoice-line schema. You are given a profile of each source column. You return a mapping.

HOW TO READ THE INPUT, AND THIS IS THE WHOLE JOB
The column names come from a custom in-house AS/400 ERP. They are cryptic, abbreviated,
and some of them actively mislead. Names like FLD017 and RSRVD1 mean nothing. A name that
looks obvious may be the wrong field: an account-number column named for the *ship-to*
account sits next to the *bill-to* account that the canonical customer_id actually wants,
and only one of them is right.

So: decide from the SAMPLE VALUES, the VALUE SHAPES and the INFERRED TYPE. Treat the
column name as the weakest evidence you have, and never as the deciding one. The sample
values are given before the name in each block for that reason.

WHEN TWO COLUMNS LOOK EQUALLY PLAUSIBLE
Say so. Put an entry in `ambiguities` naming the canonical field and both columns, and
lower your confidence for that field. Do not pick one and report high confidence. A
confident wrong answer about which account was billed misattributes money, and a human
reading your proposal can resolve an ambiguity you name but not one you hide.

THE CANONICAL SCHEMA (version {CANONICAL_SCHEMA_VERSION})
{chr(10).join(field_lines)}

TRANSFORMS - a closed set. Use only these `kind` values:
{chr(10).join(f"- {kind}" for kind in TRANSFORM_KINDS)}

  none                   use the value as it is
  trim                   strip surrounding spaces
  upper                  strip and upper-case
  cyymmdd_to_date        7 digits where the first is the century: 1 means 20xx, 0 means
                         19xx. So 1261003 is 2026-10-03.
  date_format            a written date; set date_format to one of: {", ".join(DATE_FORMATS)}
  implied_decimal        a whole number with the decimal point left out. Set `scale` to
                         how many digits are decimals: 0001250 at scale 2 is 12.50.
  trailing_sign_decimal  the same, but the sign is the last character: 000125- is -1.25.
                         Use this, not implied_decimal, if ANY sample ends in - or +.
  strip_leading_zeros    an identifier padded with zeros
  map_values             translate each source value to a canonical one. Set `value_map`
                         to a list of {{"from": ..., "to": ...}} pairs covering EVERY
                         distinct value you were shown.
  constant               a fixed value for a field the file does not carry
  multiply               scale by `factor`, given as a decimal string

RULES
1. `source_column` must be a column name from the input, copied exactly. Never invent one.
2. `canonical_field` must be a name from the schema above, copied exactly.
3. Fill only the transform parameters that kind uses. Leave the rest null.
4. Omit a canonical field entirely rather than mapping it to a column you do not believe.
   A missing field is a question for a human; a wrong one is a silent error.
5. For map_values, cover every distinct value shown. An uncovered value fails that row.
6. Numbers are decimal strings, never JSON numbers, in `factor` and `constant`.
7. In `reason`, cite what in the sample values decided it. One sentence.
"""


def provider_cache_key(model: str) -> str:
    """The provider's prefix-cache routing key.

    Deliberately file-independent: the cacheable prefix is the schema and the rules, so a
    key containing anything per-file would give every request its own cache bucket and
    cache nothing.
    """
    return f"speedy-onboard:{CANONICAL_SCHEMA_VERSION}:{PROMPT_TEMPLATE_VERSION}:{model}"


def proposal_cache_key(*, file_digest: str, layout_version: int, model: str) -> str:
    """Our own cache key: the identity of "the right answer for this file".

    Includes the layout version because moving a field boundary changes what the columns
    *are*, and the model because a different model is a different answer. Includes the
    prompt template version so a prompt edit cannot silently serve a stale proposal.
    """
    material = "|".join(
        [
            file_digest,
            str(layout_version),
            CANONICAL_SCHEMA_VERSION,
            model,
            PROMPT_TEMPLATE_VERSION,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
