"""Ask the model for a mapping - cached, capped, and degradable.

The shape of this module is set by three things that must all be true at once:

1. **A repeat proposal for the same file costs exactly nothing.** That is the demo
   property, so the cache lookup comes before anything that could spend money, and the
   cache key is checked under an advisory lock so two simultaneous identical requests
   result in one call rather than two.
2. **Every path settles or releases its reservation.** A failed call must not burn budget
   it never spent, and a call that *did* happen must be paid for even when its output was
   unusable - the ledger is honest about failures or it is not a ledger.
3. **Nothing here is required for the product to work.** Every refusal path ends with the
   same advice: map the columns by hand. P5 shipped first precisely so that advice is true.

The four failure branches are separate on purpose, because each means something different
to the person reading it: a refusal and an outage are both "no proposal" but only one of
them is worth retrying, and a truncation is the only one where we know what to change.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.canonical import service as canonical_service
from src.features.canonical.model import CANONICAL_SCHEMA_VERSION
from src.features.mapping import prompt as prompt_builder
from src.features.mapping import repo, spend
from src.features.mapping.pricing import prices_from_settings
from src.features.mapping.proposal_guard import (
    clean_ambiguities,
    dropped_as_json,
    flag_indistinguishable,
    guard,
)
from src.features.mapping.proposal_schema import MappingProposal
from src.lib import idempotency, llm, logger, ratelimit
from src.lib.errors import (
    DependencyUnavailable,
    LlmProposalInvalid,
    MappingLocked,
    NotFound,
    ProfileNotReady,
)
from src.lib.ids import uuid7

log = logger.get_logger(__name__)

#: Output ceiling for a first attempt. Sixteen fields with a sentence of reasoning each
#: fits comfortably; the retry below doubles it once if the model disagreed.
MAX_OUTPUT_TOKENS = 4_000
#: One retry at a raised ceiling, then stop. Blind retrying is wrong here: the same prompt
#: truncates the same way, so the only useful retry is one that changes something.
TRUNCATION_RETRY_MULTIPLIER = 2

_FILE_FACTS = text(
    """
    select f.sha256, f.id
    from source_files f
    where f.id = :id
    """
)

#: Serialises identical concurrent requests. `pg_advisory_xact_lock` releases on commit or
#: rollback, so a crashed request cannot hold it - unlike the session-level lock the
#: sweeper uses, which it releases by hand.
_LOCK_CACHE_KEY = text("select pg_advisory_xact_lock(:key)")


async def _replay(
    session: AsyncSession,
    file_id: UUID,
    cache_key: str,
    settings: config.Settings,
    columns: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The answer a remembered key produced, or `None` if it is no longer stored."""
    row = await repo.cached_proposal(session, cache_key)
    if row is None:
        return None
    entries, ambiguities = _finish(dict(row["proposal"]), list(row["ambiguities"]), columns)
    budget = await spend.budget(session, settings.llm_daily_spend_cap_usd)
    return {
        # `cache`, not a third origin: from the caller's side this *is* the stored answer
        # being served again, and inventing a value would mean a new case in every client.
        "origin": "cache",
        "entries": entries,
        "dropped": list(row["dropped_fields"]),
        "ambiguities": ambiguities,
        "spend": spend.spend_block(budget, Decimal(0)),
    }


def _finish(
    entries: dict[str, dict[str, Any]],
    model_ambiguities: list[str],
    columns: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """What every path returns: the stored proposal with the look-alike rule applied.

    `mapping_proposals.proposal` keeps the guard-validated model output *before* this rule,
    and the rule runs on every read - fresh, cache hit, idempotent replay - so a cached row
    made before the rule existed is served capped and named, and a later change to the rule
    reaches every cached row without a data migration.
    """
    return flag_indistinguishable(entries, columns, model_ambiguities)


def _lock_number(cache_key: str) -> int:
    """A 63-bit signed int from the cache key, for the advisory lock."""
    digest = hashlib.sha256(cache_key.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


#: The route name under which propose records its idempotency keys. A constant, because the
#: same string has to be used by the write and by every later read.
IDEMPOTENCY_ROUTE = "mapping.propose"


async def propose(
    session: AsyncSession,
    file_id: UUID,
    *,
    upload_dir: Path,
    client_ip_hash: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Propose a mapping for this file, from cache if we have one.

    Two different kinds of deduplication meet here, and they answer different questions:

    * the **cache key** is content-addressed (file digest, layout version, model) and
      answers "have we already paid for this exact question?";
    * the **idempotency key** is client-supplied and answers "is this the same *request* I
      already sent?" - which matters when a client retries and the content has since moved
      on, because §3 promises the same key gives the same result.

    A key that has been seen resolves to the proposal it produced and returns it without
    consulting the cache at all. If the row it points at has since gone, the key is treated
    as new - a missing answer is not a reason to refuse to compute one.
    """
    del upload_dir  # the profile is in the database; the file itself is not needed here
    settings = config.load()

    await ratelimit.enforce(
        session,
        key=f"llm_propose:{client_ip_hash}",
        limit=settings.rate_limit_llm_per_hour,
        window_seconds=3_600,
        what="mapping proposals",
    )

    if idempotency_key is not None:
        # The fingerprint is the file: it is this route's only input, so "the same request"
        # means "the same file". Claiming before the work runs is what lets a reused key be
        # refused rather than silently served another file's proposal.
        claim = await idempotency.claim(
            session,
            route=IDEMPOTENCY_ROUTE,
            key=idempotency_key,
            fingerprint=str(file_id),
        )
        await session.commit()
        if claim.replay_ref is not None:
            # The rule needs the profile; an empty one (never expected here) just means it
            # has nothing to compare, and the stored answer is served as it is.
            replay_layout = await _layout_version(session, file_id)
            replay_columns = await _profile_columns(session, file_id, replay_layout)
            replayed = await _replay(session, file_id, claim.replay_ref, settings, replay_columns)
            if replayed is not None:
                log.info(
                    "llm_proposal_replayed",
                    source_file_id=str(file_id),
                    reason="idempotency_key",
                )
                return replayed

    layout = await _layout_version(session, file_id)
    if await repo.has_materialized_run(session, file_id):
        raise MappingLocked(
            "This file's canonical lines have already been built, so its mapping cannot "
            "be changed. Upload the file again as a new run to map it differently."
        )

    columns = await _profile_columns(session, file_id, layout)
    if not columns:
        raise ProfileNotReady(
            "This file's columns are not known yet. Wait for profiling to finish before "
            "asking for a proposal."
        )
    known_columns = {str(column["column_name"]) for column in columns}

    digest = await _file_digest(session, file_id)
    model = settings.openai_model or "unconfigured"
    cache_key = prompt_builder.proposal_cache_key(
        file_digest=digest, layout_version=layout, model=model
    )

    # Everything from here is serialised per cache key, so two identical requests arriving
    # together produce one call and two identical answers.
    await session.execute(_LOCK_CACHE_KEY, {"key": _lock_number(cache_key)})

    cached = await repo.cached_proposal(session, cache_key)
    if cached is not None:
        entries, ambiguities = _finish(
            dict(cached["proposal"]), list(cached["ambiguities"]), columns
        )
        dropped: list[dict[str, Any]] = list(cached["dropped_fields"])
        await _apply_to_draft(session, file_id, layout, entries)
        budget = await spend.budget(session, settings.llm_daily_spend_cap_usd)
        if idempotency_key is not None:
            await idempotency.fulfil(
                session, route=IDEMPOTENCY_ROUTE, key=idempotency_key, response_ref=cache_key
            )
        # The service owns the boundary (decisions.md, P11.5): without this commit the draft
        # write and the idempotency fulfilment were rolled back when the request's session
        # closed (UX/demo pass tester, 2026-10-13).
        await session.commit()
        log.info("llm_proposal_cache_hit", source_file_id=str(file_id))
        return {
            "origin": "cache",
            "entries": entries,
            "dropped": dropped,
            "ambiguities": ambiguities,
            "spend": spend.spend_block(budget, Decimal(0)),
        }

    if not llm.is_available():
        await session.rollback()
        raise DependencyUnavailable(
            "Mapping proposals are not configured, so there is nothing to propose. Map "
            "the columns by hand - every control on the mapping page works without the "
            "model."
        )

    prices = prices_from_settings(
        settings.openai_price_input_per_mtok,
        settings.openai_price_cached_input_per_mtok,
        settings.openai_price_output_per_mtok,
    )
    if prices is None:
        await session.rollback()
        raise DependencyUnavailable(
            "Mapping proposals are not configured, so there is nothing to propose. Map "
            "the columns by hand."
        )

    schema_fields = canonical_service.canonical_schema()["fields"]
    instructions = prompt_builder.render_instructions(schema_fields)
    blocks = prompt_builder.column_blocks(columns)
    user_input = prompt_builder.render_columns(blocks)
    prompt_chars = len(instructions) + len(user_input)

    result, cost, attempts = await _call_with_one_truncation_retry(
        session,
        file_id=file_id,
        instructions=instructions,
        user_input=user_input,
        prompt_chars=prompt_chars,
        prices=prices,
        cap_usd=settings.llm_daily_spend_cap_usd,
        model=model,
        idempotency_key=idempotency_key,
    )

    if result.outcome != "ok" or result.parsed is None:
        await _record_failure(session, file_id, cache_key, result, cost, attempts)
        raise _failure_error(result.outcome)

    proposal = (
        result.parsed
        if isinstance(result.parsed, MappingProposal)
        else MappingProposal.model_validate(result.parsed.model_dump())
    )
    guarded = guard(proposal, known_columns=known_columns, source_file_id=str(file_id))

    # Stored before the rule (see `_finish`), applied and returned after it. The model's own
    # ambiguities are stored cleaned, so a replay serves exactly what the fresh call did.
    model_ambiguities = clean_ambiguities(guarded.ambiguities)
    await repo.insert_proposal(
        session,
        proposal=guarded.entries,
        dropped_fields=dropped_as_json(guarded.dropped),
        ambiguities=model_ambiguities,
        id=uuid7(),
        source_file_id=file_id,
        cache_key=cache_key,
        canonical_schema_version=CANONICAL_SCHEMA_VERSION,
        origin="llm",
        model=result.model,
        effort=result.effort,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cached_input_tokens=result.usage.cached_input_tokens,
        cache_write_tokens=result.usage.cache_write_tokens,
        cost_usd=cost,
        latency_ms=result.latency_ms,
        finish_reason=result.outcome,
    )
    entries, ambiguities = _finish(guarded.entries, model_ambiguities, columns)
    await _apply_to_draft(session, file_id, layout, entries)
    if idempotency_key is not None:
        await idempotency.fulfil(
            session, route=IDEMPOTENCY_ROUTE, key=idempotency_key, response_ref=cache_key
        )
    # Committed here, not left to the route: until 2026-10-13 nothing did, so a paid proposal,
    # its cache row, the draft it wrote and the idempotency fulfilment were all rolled back
    # when the session closed - and the next identical request paid again. Only the claim
    # and the spend ledger, which commit for themselves, survived.
    await session.commit()
    budget = await spend.budget(session, settings.llm_daily_spend_cap_usd)

    log.info(
        "llm_proposal_stored",
        source_file_id=str(file_id),
        model=result.model,
        accepted=len(guarded.entries),
        dropped=len(guarded.dropped),
        cost_usd=str(cost),
    )
    return {
        "origin": "llm",
        "entries": entries,
        "dropped": dropped_as_json(guarded.dropped),
        "ambiguities": ambiguities,
        "spend": spend.spend_block(budget, cost),
    }


async def _call_with_one_truncation_retry(
    session: AsyncSession,
    *,
    file_id: UUID,
    instructions: str,
    user_input: str,
    prompt_chars: int,
    prices: Any,
    cap_usd: Decimal,
    model: str,
    idempotency_key: str | None = None,
) -> tuple[llm.LlmResult, Decimal, int]:
    """Reserve, call, settle. Retries exactly once, and only for a truncation.

    Returns the last result, the *total* cost of every attempt, and how many were made.
    Both attempts' real cost is settled: we pay for failures, so the ledger records them.
    """
    total_cost = Decimal(0)
    max_output = MAX_OUTPUT_TOKENS
    result: llm.LlmResult | None = None

    for attempt in (1, 2):
        estimate = spend.estimate_cost_usd(prompt_chars, max_output, prices)
        reservation = await spend.reserve(
            session,
            amount_usd=estimate,
            cap_usd=cap_usd,
            source_file_id=file_id,
            # Recorded on the ledger row so a spend entry can be traced back to the client
            # request that caused it - which is what the column was added for.
            idempotency_key=idempotency_key,
        )
        try:
            result = llm.parse(
                instructions=instructions,
                user_input=user_input,
                text_format=MappingProposal,
                prompt_cache_key=prompt_builder.provider_cache_key(model),
                max_output_tokens=max_output,
            )
        except Exception:
            # Nothing was spent that we know of, so the hold goes back untouched.
            await spend.release(session, reservation)
            raise

        from src.features.mapping.pricing import cost_usd

        attempt_cost = cost_usd(result.usage, prices)
        await spend.settle(session, reservation, attempt_cost)
        total_cost += attempt_cost

        if result.outcome != "truncated" or attempt == 2:
            return result, total_cost, attempt

        # The one retry worth making: the same prompt truncates the same way, so raise the
        # ceiling rather than hoping.
        max_output *= TRUNCATION_RETRY_MULTIPLIER
        log.warning(
            "llm_truncation_retry",
            source_file_id=str(file_id),
            new_max_output_tokens=max_output,
        )

    assert result is not None  # the loop always runs at least once
    return result, total_cost, 2


def _failure_error(outcome: str) -> Exception:
    """Each branch means something different to the person reading it."""
    if outcome == "truncated":
        return LlmProposalInvalid(
            "This file has too many columns for the model to propose in one pass. Map "
            "the columns by hand - every control on the mapping page works without it."
        )
    if outcome == "refused":
        return DependencyUnavailable(
            "The model declined to propose a mapping for this file. Map the columns by "
            "hand - the manual path is unaffected."
        )
    if outcome == "content_filtered":
        return DependencyUnavailable(
            "The model could not process this file's sample values. Map the columns by "
            "hand - the manual path is unaffected."
        )
    return LlmProposalInvalid(
        "The model's answer could not be read as a mapping. Map the columns by hand."
    )


async def _record_failure(
    session: AsyncSession,
    file_id: UUID,
    cache_key: str,
    result: llm.LlmResult,
    cost: Decimal,
    attempts: int,
) -> None:
    """A failed proposal is *not* cached - the next attempt should try again - but the
    spend is already settled, and the row records what happened for the operator."""
    del cache_key
    log.warning(
        "llm_proposal_failed",
        source_file_id=str(file_id),
        outcome=result.outcome,
        attempts=attempts,
        cost_usd=str(cost),
        model=result.model,
    )
    await session.commit()


async def _layout_version(session: AsyncSession, file_id: UUID) -> int:
    from src.features.layouts import repo as layouts_repo

    layout = await layouts_repo.current_layout(session, file_id)
    if layout is None:
        if not await repo.file_exists(session, file_id):
            raise NotFound("That file does not exist.")
        raise ProfileNotReady(
            "This file is still being read. Its columns appear here once parsing and "
            "profiling finish."
        )
    return int(layout["version"])


async def _profile_columns(
    session: AsyncSession, file_id: UUID, layout_version: int
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "select column_name, type_guess, null_pct, distinct_count, sample_values, "
            "patterns from column_profiles "
            "where source_file_id = :id and layout_version = :v order by column_index"
        ),
        {"id": file_id, "v": layout_version},
    )
    return [dict(row) for row in rows.mappings()]


async def _file_digest(session: AsyncSession, file_id: UUID) -> str:
    row = (await session.execute(_FILE_FACTS, {"id": file_id})).mappings().first()
    if row is None:
        raise NotFound("That file does not exist.")
    return str(row["sha256"])


async def _apply_to_draft(
    session: AsyncSession, file_id: UUID, layout_version: int, entries: dict[str, Any]
) -> None:
    """Merge the proposal into the working draft, leaving manual choices in place.

    A proposal is a suggestion, so it does not clobber a field a human already set: the
    merge skips any canonical field whose stored origin is `manual`. Without that, asking
    for a proposal after correcting two fields by hand would silently undo the correction.
    """
    if not entries:
        return
    existing = await repo.draft(session, file_id, layout_version)
    current: dict[str, Any] = dict(existing["mapping"]) if existing else {}
    patch = {
        name: entry
        for name, entry in entries.items()
        if str(current.get(name, {}).get("origin", "")) != "manual"
    }
    if not patch:
        return
    await repo.merge_draft(
        session,
        file_id,
        layout_version,
        draft_id=uuid7(),
        canonical_schema_version=CANONICAL_SCHEMA_VERSION,
        patch=patch,
    )
