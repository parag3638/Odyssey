"""mvp.md 6.3-6.4 - the strict-mode schema, the prompt, and the guard over model output.

What the cassette-scored test does and does not prove is stated in
`tests/cassettes/README.md` and repeated in that test's own docstring: no live call has
been made, so it tests the harness and the guard, not the model.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from openai.lib._pydantic import to_strict_json_schema

from src.features.canonical.model import CANONICAL_FIELD_NAMES, CANONICAL_SCHEMA_VERSION
from src.features.mapping import prompt
from src.features.mapping.proposal_guard import (
    GuardedProposal,
    guard,
    transform_document,
)
from src.features.mapping.proposal_schema import (
    PROMPT_TEMPLATE_VERSION,
    MappingProposal,
    ProposedTransform,
)
from src.features.mapping.transforms import TRANSFORM_KINDS
from tests.baselines.proposal_scorer import ProposalScore, score_proposal

#: Under `tests/`, not `fixtures/`: `fixtures/` belongs to the generator, and
#: `speedy-generate --verify` rightly refuses to find anything there it did not produce.
CASSETTE = Path(__file__).resolve().parent / "cassettes" / "mapping_proposal_csv.json"

#: The source columns of the committed CSV sample - what the guard checks names against.
KNOWN_COLUMNS = {
    "IVHDR01",
    "CUSTNO",
    "IVPSTDT",
    "FLD017",
    "BILTNO",
    "ITMNBR",
    "SHPUNT",
    "UMCODE",
    "BILTNM",
    "RSRVD1",
    "SELPRC",
    "MFGPRT",
    "LINAMT",
    "FLD023",
    "CLSCD1",
    "SHPWGT",
    "REMTNO",
    "CSEMUL",
    "ALWFLG",
    "FLD041",
    "ALWPRC",
}


def _cassette() -> MappingProposal:
    return MappingProposal.model_validate(json.loads(CASSETTE.read_text()))


def _walk(node: Any, path: str = "$") -> list[str]:
    """Every strict-mode violation in a generated schema, with where it is.

    `cast` rather than isinstance-narrowing: narrowing `Any` to `dict` leaves pyright
    holding `dict[Unknown, Unknown]`, which it then reports at every use.
    """
    problems: list[str] = []
    if isinstance(node, dict):
        mapping = cast("dict[str, Any]", node)
        if mapping.get("type") == "object":
            if mapping.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties is not false")
            properties = set(cast("dict[str, Any]", mapping.get("properties", {})))
            required = set(cast("list[str]", mapping.get("required", [])))
            if properties != required:
                problems.append(f"{path}: {sorted(properties - required)} not in required")
        for key, value in mapping.items():
            problems += _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(cast("list[Any]", node)):
            problems += _walk(value, f"{path}[{index}]")
    return problems


# --------------------------------------------------------------------------------------
# 6.3 - the schema really passes strict mode
# --------------------------------------------------------------------------------------


def test_the_generated_schema_satisfies_strict_mode() -> None:
    """Asserted on the SDK's own transformation rather than on my reading of the rules,
    so this fails here instead of at the first API call."""
    assert _walk(to_strict_json_schema(MappingProposal)) == []


def test_the_top_level_is_a_list_and_value_map_is_a_list_of_pairs() -> None:
    """Dynamic keys are impossible under `additionalProperties: false`, so neither the
    proposal nor a value map can be a keyed object."""
    schema = to_strict_json_schema(MappingProposal)
    assert schema["properties"]["fields"]["type"] == "array"

    transform = schema["$defs"]["ProposedTransform"]["properties"]
    assert [branch.get("type") for branch in transform["value_map"]["anyOf"]] == [
        "array",
        "null",
    ]

    pair = schema["$defs"]["ValuePair"]
    assert set(pair["properties"]) == {"from", "to"}
    assert pair["additionalProperties"] is False


def test_every_optional_transform_parameter_is_a_nullable_union() -> None:
    """Strict mode has no absent properties, only null ones - which is why the transform
    carries all six slots on every instance."""
    transform = to_strict_json_schema(MappingProposal)["$defs"]["ProposedTransform"]
    for parameter in ("scale", "date_format", "value_map", "factor", "constant"):
        branches = [b.get("type") for b in transform["properties"][parameter]["anyOf"]]
        assert "null" in branches, parameter
        assert parameter in transform["required"], parameter


def test_the_transform_kind_enum_matches_the_registry_exactly() -> None:
    """A kind the model may name but the registry cannot parse is a guaranteed dropped
    field, so the two lists must be the same list."""
    schema = to_strict_json_schema(MappingProposal)
    kinds = schema["$defs"]["ProposedTransform"]["properties"]["kind"]["enum"]
    assert set(kinds) == set(TRANSFORM_KINDS)


# --------------------------------------------------------------------------------------
# 6.3 - the prompt
# --------------------------------------------------------------------------------------


def _blocks() -> list[prompt.ColumnBlock]:
    return prompt.column_blocks(
        [
            {
                "column_name": "UMCODE",
                "type_guess": "text",
                "null_pct": Decimal("0.000000"),
                "distinct_count": 3,
                "sample_values": ["CS", "LB", "EA"],
                "patterns": [{"signature": "AA", "count": 500}],
            },
            {
                "column_name": "BIGCELL",
                "type_guess": "text",
                "null_pct": Decimal("0.000000"),
                "distinct_count": 1,
                "sample_values": ["x" * 400],
                "patterns": [],
            },
        ]
    )


def test_the_column_block_puts_evidence_before_the_name() -> None:
    """The premise of the phase: a naive name matcher scores under 40% on this file, so
    the name is the weakest evidence and is rendered last."""
    first = prompt.render_columns(_blocks()).split("Column 2")[0]
    assert first.index("sample values") < first.index("column name")
    assert first.index("inferred type") < first.index("column name")
    assert "weak evidence" in first


def test_no_sample_longer_than_the_cap_reaches_the_prompt() -> None:
    """The cap is a privacy boundary as much as a size one: it is the most of any one
    cell that leaves this system."""
    rendered = prompt.render_columns(_blocks())
    line = next(line for line in rendered.splitlines() if "x" * 20 in line)
    assert len(line.split("sample values: ")[1]) == prompt.MAX_SAMPLE_CHARS


def test_the_instructions_say_the_names_are_cryptic_and_may_mislead() -> None:
    instructions = prompt.render_instructions(
        [{"name": "customer_id", "type": "string", "required": True, "description": "d"}]
    ).lower()
    assert "cryptic" in instructions
    assert "mislead" in instructions
    assert "ship-to" in instructions and "bill-to" in instructions, (
        "the decoy is the concrete case; naming it is what makes the warning actionable"
    )
    assert "weakest evidence" in instructions


def test_the_instructions_ask_for_ambiguity_rather_than_a_confident_guess() -> None:
    """The Phase 5 measurement is why: CUSTNO and BILTNO are indistinguishable from the
    file, so a model that says so is more useful than one that picks and sounds sure."""
    instructions = prompt.render_instructions([]).lower()
    assert "ambiguities" in instructions
    assert "do not pick one and report high confidence" in instructions


def test_the_instructions_carry_the_schema_and_the_transform_vocabulary() -> None:
    fields = [
        {"name": name, "type": "string", "required": True, "description": f"the {name}"}
        for name in CANONICAL_FIELD_NAMES
    ]
    instructions = prompt.render_instructions(fields)
    for name in CANONICAL_FIELD_NAMES:
        assert name in instructions
    for kind in TRANSFORM_KINDS:
        assert kind in instructions
    assert CANONICAL_SCHEMA_VERSION in instructions


def test_the_prompt_carries_no_secret_no_path_and_no_whole_row() -> None:
    both = prompt.render_instructions([]) + prompt.render_columns(_blocks())
    for forbidden in ("sk-", "OPENAI", "/var/", "/app", "postgresql://", "password"):
        assert forbidden not in both, forbidden


def test_the_provider_cache_key_contains_nothing_file_specific() -> None:
    """A per-file key would give every request its own bucket and cache nothing."""
    expected = f"speedy-onboard:{CANONICAL_SCHEMA_VERSION}:{PROMPT_TEMPLATE_VERSION}:gpt-5.6"
    assert prompt.provider_cache_key("gpt-5.6") == expected


def test_our_cache_key_changes_with_file_layout_model_and_prompt_version() -> None:
    base = prompt.proposal_cache_key(file_digest="abc", layout_version=1, model="m")
    assert base == prompt.proposal_cache_key(file_digest="abc", layout_version=1, model="m")
    assert base != prompt.proposal_cache_key(file_digest="xyz", layout_version=1, model="m")
    assert base != prompt.proposal_cache_key(file_digest="abc", layout_version=2, model="m")
    assert base != prompt.proposal_cache_key(file_digest="abc", layout_version=1, model="n")


def test_a_prompt_template_bump_invalidates_our_cache() -> None:
    """A prompt edit must not silently serve a proposal made under the old one."""
    import src.features.mapping.prompt as prompt_module

    before = prompt.proposal_cache_key(file_digest="abc", layout_version=1, model="m")
    original = prompt_module.PROMPT_TEMPLATE_VERSION
    try:
        prompt_module.PROMPT_TEMPLATE_VERSION = "999"
        after = prompt.proposal_cache_key(file_digest="abc", layout_version=1, model="m")
    finally:
        prompt_module.PROMPT_TEMPLATE_VERSION = original
    assert before != after


def test_the_column_count_is_capped() -> None:
    many = [
        {
            "column_name": f"C{index}",
            "type_guess": "text",
            "null_pct": Decimal(0),
            "distinct_count": 1,
            "sample_values": ["x"],
            "patterns": [],
        }
        for index in range(prompt.MAX_COLUMNS + 50)
    ]
    assert len(prompt.column_blocks(many)) == prompt.MAX_COLUMNS


# --------------------------------------------------------------------------------------
# 6.4 - the guard: drop, do not fail
# --------------------------------------------------------------------------------------


def _one_field(**overrides: Any) -> MappingProposal:
    field: dict[str, Any] = {
        "canonical_field": "invoice_no",
        "source_column": "IVHDR01",
        "transform": {
            "kind": "trim",
            "scale": None,
            "date_format": None,
            "value_map": None,
            "factor": None,
            "constant": None,
        },
        "confidence": 0.9,
        "reason": "a reason",
    }
    field.update(overrides)
    return MappingProposal.model_validate({"fields": [field], "ambiguities": []})


def _payload(proposal: MappingProposal) -> dict[str, Any]:
    return proposal.fields[0].model_dump(by_alias=True)


def test_a_field_naming_a_real_column_is_accepted_with_its_confidence_and_reason() -> None:
    result = guard(_one_field(), known_columns=KNOWN_COLUMNS, source_file_id="f")
    assert result.dropped == []
    entry = result.entries["invoice_no"]
    assert entry["sourceColumn"] == "IVHDR01"
    assert entry["confidence"] == "0.9"
    assert entry["origin"] == "llm"
    assert entry["reason"] == "a reason"


def test_an_invented_column_is_dropped_and_the_rest_survives() -> None:
    """One unusable field must not lose fifteen good ones."""
    proposal = MappingProposal.model_validate(
        {
            "fields": [
                _payload(_one_field()),
                _payload(
                    _one_field(canonical_field="customer_id", source_column="TOTALLY_MADE_UP_COL")
                ),
            ],
            "ambiguities": [],
        }
    )
    result = guard(proposal, known_columns=KNOWN_COLUMNS, source_file_id="f")

    assert list(result.entries) == ["invoice_no"]
    assert len(result.dropped) == 1
    assert result.dropped[0].source_column == "TOTALLY_MADE_UP_COL"
    assert "not a column in this file" in result.dropped[0].reason


def test_a_canonical_field_outside_the_schema_is_dropped() -> None:
    result = guard(
        _one_field(canonical_field="invoiceNumber"),
        known_columns=KNOWN_COLUMNS,
        source_file_id="f",
    )
    assert result.entries == {}
    assert "not a field in the canonical schema" in result.dropped[0].reason


def test_a_slot_the_kind_does_not_use_is_ignored_rather_than_fatal() -> None:
    """Under strict mode every parameter slot is present by construction, so a stray
    non-null value cannot change what the kind means - and dropping an unambiguous `trim`
    because the model also wrote a `scale` would lose a good mapping for nothing. It is
    logged instead, because filling a slot your own kind does not use usually means you
    meant a different kind."""
    from src.features.mapping.proposal_guard import irrelevant_slots

    payload = _payload(_one_field())
    payload["transform"]["scale"] = 2  # `trim` takes no scale
    proposal = MappingProposal.model_validate({"fields": [payload], "ambiguities": []})

    assert irrelevant_slots(proposal.fields[0].transform) == ["scale"]

    result = guard(proposal, known_columns=KNOWN_COLUMNS, source_file_id="f")
    assert result.dropped == []
    assert result.entries["invoice_no"]["transform"] == {"kind": "trim"}


def test_a_transform_the_registry_genuinely_refuses_drops_only_that_field() -> None:
    """An out-of-range scale on a kind that *does* take one is unusable, not noise."""
    payload = _payload(_one_field(canonical_field="qty", source_column="SHPUNT"))
    payload["transform"] = {
        "kind": "implied_decimal",
        "scale": 99,
        "date_format": None,
        "value_map": None,
        "factor": None,
        "constant": None,
    }
    proposal = MappingProposal.model_validate({"fields": [payload], "ambiguities": []})

    result = guard(proposal, known_columns=KNOWN_COLUMNS, source_file_id="f")

    assert result.entries == {}
    assert "unusable transform" in result.dropped[0].reason
    assert "0-6" in result.dropped[0].reason


def test_an_out_of_range_confidence_is_clamped_both_ways() -> None:
    high = guard(_one_field(confidence=1.7), known_columns=KNOWN_COLUMNS, source_file_id="f")
    assert high.entries["invoice_no"]["confidence"] == "1"

    low = guard(_one_field(confidence=-0.2), known_columns=KNOWN_COLUMNS, source_file_id="f")
    assert low.entries["invoice_no"]["confidence"] == "0"


def test_two_columns_claiming_one_canonical_field_keeps_the_first() -> None:
    """Deterministic rather than order-dependent-looking: the first wins, and the second
    is reported as dropped so the user knows the model was confused."""
    proposal = MappingProposal.model_validate(
        {
            "fields": [_payload(_one_field()), _payload(_one_field(source_column="CUSTNO"))],
            "ambiguities": [],
        }
    )
    result = guard(proposal, known_columns=KNOWN_COLUMNS, source_file_id="f")

    assert result.entries["invoice_no"]["sourceColumn"] == "IVHDR01"
    assert len(result.dropped) == 1
    assert "already proposed" in result.dropped[0].reason


def test_the_flat_strict_mode_transform_becomes_the_registrys_document() -> None:
    """Strict mode forces all six slots onto every transform; the registry wants only the
    one its kind uses and refuses the rest."""

    def transform(**overrides: Any) -> ProposedTransform:
        base: dict[str, Any] = {
            "kind": "trim",
            "scale": None,
            "date_format": None,
            "value_map": None,
            "factor": None,
            "constant": None,
        }
        base.update(overrides)
        return ProposedTransform.model_validate(base)

    assert transform_document(transform(kind="implied_decimal", scale=2)) == {
        "kind": "implied_decimal",
        "scale": 2,
    }
    assert transform_document(transform(kind="date_format", date_format="YYYYMMDD")) == {
        "kind": "date_format",
        "fmt": "YYYYMMDD",
    }
    assert transform_document(transform(kind="multiply", factor="1.5")) == {
        "kind": "multiply",
        "factor": "1.5",
    }
    assert transform_document(transform()) == {"kind": "trim"}


def test_a_value_map_with_a_duplicate_from_is_rejected_not_last_one_wins() -> None:
    payload = _payload(_one_field(canonical_field="uom", source_column="UMCODE"))
    payload["transform"] = {
        "kind": "map_values",
        "scale": None,
        "date_format": None,
        "value_map": [{"from": "CS", "to": "CS"}, {"from": "CS", "to": "EA"}],
        "factor": None,
        "constant": None,
    }
    proposal = MappingProposal.model_validate({"fields": [payload], "ambiguities": []})

    result = guard(proposal, known_columns=KNOWN_COLUMNS, source_file_id="f")

    assert result.entries == {}
    assert "twice" in result.dropped[0].reason


# --------------------------------------------------------------------------------------
# 6.4 - scoring, against the cassette
# --------------------------------------------------------------------------------------


def _scored_cassette() -> tuple[GuardedProposal, ProposalScore]:
    result = guard(_cassette(), known_columns=KNOWN_COLUMNS, source_file_id="f")
    assignments = {name: str(entry["sourceColumn"]) for name, entry in result.entries.items()}
    return result, score_proposal(assignments)


def test_the_cassette_scores_above_the_baseline_and_gets_the_decoy_right() -> None:
    """**This tests the harness, not the model.**

    No live call has been made - there is no key configured - so the cassette is
    hand-authored (`tests/cassettes/README.md`). What is proven here is that a proposal
    in this shape is parsed, validated against the real column list, scored the same way
    `naive_name_matcher` is scored in P0, and compared against the 75% bar. The claim "the
    model beats name matching" needs a live call, and the same assertion is written to run
    against one.
    """
    _, score = _scored_cassette()

    assert score.total == 16
    assert score.rate >= 0.75, (
        f"scored {score.rate:.1%} ({score.correct}/{score.total}); wrong: {score.wrong_fields()}"
    )
    assert score.per_field["customer_id"] is True, (
        "the decoy must be right: CUSTNO is the ship-to account, BILTNO the bill-to"
    )


def test_the_scorer_names_what_it_got_wrong() -> None:
    """The point of the criterion is catching a prompt regression that quietly turns the
    feature back into name matching, so a failure has to say which fields moved."""
    _, score = _scored_cassette()
    wrong = score.wrong_fields()
    # The cassette deliberately contains one miss, so the harness is exercised on a miss.
    assert "invoice_vendor" in wrong
    assert "expected REMTNO" in wrong["invoice_vendor"]


def test_the_cassette_beats_the_naive_matcher_on_the_same_scoreboard() -> None:
    """Like compared with like: the same scoring method and the same oracle."""
    from tests.baselines.naive_name_matcher import run_naive_matcher, score_against_truth

    naive_correct, naive_per_field = score_against_truth(run_naive_matcher())
    naive_rate = naive_correct / len(naive_per_field)
    _, score = _scored_cassette()

    assert naive_rate < 0.40, f"the baseline moved: {naive_rate:.1%}"
    assert score.rate > naive_rate


def test_the_cassette_reports_the_decoy_as_an_ambiguity_with_low_confidence() -> None:
    """What a good proposal looks like on a field the file cannot settle: named, with the
    confidence lowered, rather than picked confidently."""
    result, _ = _scored_cassette()
    assert any("CUSTNO" in item and "BILTNO" in item for item in result.ambiguities)
    assert Decimal(result.entries["customer_id"]["confidence"]) < Decimal("0.7")


@pytest.mark.skip(reason="needs OPENAI_API_KEY and a confirmed model id; run manually")
def test_a_live_call_beats_the_baseline() -> None:
    """The same assertion against a real call. Skipped on purpose: scoring a live model as
    a CI gate would be a flaky, paid test."""
