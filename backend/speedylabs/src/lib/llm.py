"""The one place the model is called. One client, one timeout, one retry policy.

Everything about talking to OpenAI lives here so that the rest of the app can treat a
proposal as data that either arrived or did not. Callers get a tagged result, not an
exception, for the failures that are part of normal operation - a refusal, a truncation,
a content filter - because each of those maps to a different answer for the user *and*
still has a real cost that the spend ledger has to settle. Only genuine unavailability
(timeout, 5xx, no network, no key) raises.

Shapes verified against the installed SDK rather than inferred, as mvp.md's P6 note
insists, because the Responses API differs from Chat Completions in exactly the place it
matters:

* `client.responses.parse(...)` accepts `text_format`, `instructions`, `input`,
  `prompt_cache_key`, `max_output_tokens`, `reasoning` and `timeout` - confirmed on
  `openai==3.24.0`.
* **A refusal is not `message.refusal`.** It arrives as a `ResponseOutputRefusal` item
  nested in a `ResponseOutputMessage`'s `content` list. Reading it the Chat Completions
  way would silently never fire.
* Truncation is `status == "incomplete"` with `incomplete_details.reason ==
  "max_output_tokens"`; a content filter is the same field reading `"content_filter"`.
  `LengthFinishReasonError` exists in the SDK but belongs to `chat.completions.parse`.
* Cached-prefix accounting is `usage.input_tokens_details.cached_tokens` and
  `.cache_write_tokens`.

The API key never appears in a log line, an error message, or a stored row. The model id
appears in all three, on purpose: when a call fails because the id is wrong, that is the
one fact that shortens the diagnosis.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Final, Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    OpenAI,
)
from openai.types.responses import ParsedResponse
from pydantic import BaseModel

from src import config
from src.features.mapping.pricing import TokenUsage
from src.lib import logger
from src.lib.errors import DependencyUnavailable

log = logger.get_logger(__name__)

#: Attempts in total, not retries after the first. Three is the §7 ceiling.
MAX_ATTEMPTS: Final = 3
#: Base for the exponential backoff, with jitter. Small, because the caller is a user
#: watching a spinner inside a 30s budget - not a background job with all night.
BACKOFF_BASE_MS: Final = 500
BACKOFF_JITTER_MS: Final = 250

#: The shortest attempt worth starting. Below this there is no time for a round trip, so
#: the budget is spent and the honest answer is "unavailable" rather than one more doomed
#: call.
#:
#: Milliseconds, as an integer, and the whole budget is tracked the same way. Step 0.5's
#: criterion bans float annotations from `src/` outright, and while the reason is money, a
#: blanket rule with no carve-outs is the one that stays true - so the arithmetic here is
#: integer nanoseconds from `time.monotonic_ns()`, and the only division is the one handing
#: seconds to the SDK at the boundary.
MIN_ATTEMPT_MS: Final = 250
MS_PER_SECOND: Final = 1_000
NS_PER_MS: Final = 1_000_000

Outcome = Literal["ok", "refused", "truncated", "content_filtered"]


@dataclass(frozen=True)
class LlmResult:
    """What came back, including from a failed call: the cost is real either way."""

    outcome: Outcome
    #: The parsed model on `ok`, `None` otherwise.
    parsed: BaseModel | None
    usage: TokenUsage
    latency_ms: int
    model: str
    #: The reasoning effort actually sent, or `None` when it was omitted.
    effort: str | None
    #: The provider's own words on a refusal. Logged, never shown to a user verbatim.
    refusal: str | None = None


def is_available() -> bool:
    """Whether a call could be attempted at all.

    A missing key is a degraded feature, not a boot failure: the whole manual mapping
    path works without it, and the app must start so that path is reachable.
    """
    settings = config.load()
    return settings.llm_enabled


def _client(api_key: str, timeout_seconds: int) -> OpenAI:
    # Built per call rather than cached at import: importing this module must not require
    # a key, and the timeout belongs to the client so every request inherits it.
    return OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)


def _usage_from(response: ParsedResponse[Any]) -> TokenUsage:
    """Token counts, defaulting to zero when the provider omitted them.

    Zeros rather than `None`: a cost of nothing is the honest reading of "the provider
    did not say", and it keeps the ledger arithmetic free of special cases.
    """
    usage = response.usage
    if usage is None:
        return TokenUsage(input_tokens=0, output_tokens=0)
    details = usage.input_tokens_details
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=details.cached_tokens if details else 0,
        cache_write_tokens=(details.cache_write_tokens or 0) if details else 0,
    )


def _refusal_text(response: ParsedResponse[Any]) -> str | None:
    """Walk the output for a refusal item.

    This is where a refusal actually lives on the Responses API:
    `response.output[*].content[*]` where a content part may be a
    `ResponseOutputRefusal`. Reading `message.refusal`, which is the Chat Completions
    shape, would compile and never fire.
    """
    for item in response.output:
        if item.type != "message":
            continue
        for part in item.content:
            if part.type == "refusal" and part.refusal:
                return part.refusal
    return None


def _incomplete_reason(response: ParsedResponse[Any]) -> str | None:
    if response.status != "incomplete":
        return None
    details = response.incomplete_details
    return None if details is None or details.reason is None else details.reason


def parse[ModelT: BaseModel](
    *,
    instructions: str,
    user_input: str,
    text_format: type[ModelT],
    prompt_cache_key: str,
    max_output_tokens: int,
) -> LlmResult:
    """One structured call. Raises `DependencyUnavailable`; never raises for a refusal.

    `instructions` is the stable prefix and `user_input` the volatile per-file block -
    kept in separate parameters so the cacheable part is physically separate from the
    part that changes, rather than relying on message order.
    """
    settings = config.load()
    if not settings.llm_enabled or settings.openai_api_key is None:
        raise DependencyUnavailable(
            "Mapping proposals are not configured. Map the columns by hand - every "
            "control on the mapping page works without the model."
        )

    model = settings.openai_model or ""
    effort = settings.llm_reasoning_effort or None
    client = _client(settings.openai_api_key, settings.llm_timeout_seconds)

    started_ns = time.monotonic_ns()
    # One budget for the whole call, retries and backoff included. `LLM_TIMEOUT_SECONDS` is
    # promised to the user as a hard ceiling (mvp.md 6.1: `DependencyUnavailable` within the
    # timeout plus a second), and a per-attempt timeout does not deliver that: the P6 tester
    # pointed a stalling socket at this and measured 3.1-3.4s against a 2s budget, because a
    # stall is an `APITimeoutError`, which was retried three times with the *full* timeout
    # each. At the real 30s default that is a user watching a spinner for 90 seconds.
    #
    # Retrying is still right for a failure that happens *fast* - a refused connection, a
    # 503 - which is where a second attempt costs little and often succeeds. The deadline is
    # what distinguishes the two cases without needing to enumerate them.
    budget_ms = settings.llm_timeout_seconds * MS_PER_SECOND
    deadline_ns = started_ns + budget_ms * NS_PER_MS
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining_ms = (deadline_ns - time.monotonic_ns()) // NS_PER_MS
        if remaining_ms < MIN_ATTEMPT_MS:
            break
        try:
            response = _call(
                client,
                model=model,
                effort=effort,
                instructions=instructions,
                user_input=user_input,
                text_format=text_format,
                prompt_cache_key=prompt_cache_key,
                max_output_tokens=max_output_tokens,
                timeout_ms=remaining_ms,
            )
        except BadRequestError as error:
            # A 400 is our fault - a bad model id, a schema the provider rejected. The
            # same request will fail the same way, so retrying only wastes the user's
            # 30 seconds. The model id is named because it is the usual cause.
            log.warning("llm_bad_request", model=model, status=400)
            raise DependencyUnavailable(
                "Mapping proposals are misconfigured, so the model was not asked. Map "
                "the columns by hand for now."
            ) from error
        except (APITimeoutError, APIConnectionError, APIStatusError) as error:
            status = getattr(error, "status_code", None)
            if isinstance(error, APIStatusError) and status is not None and status < 500:
                # 401, 403, 404, 429 from the provider: not transient either.
                log.warning("llm_provider_refused", model=model, status=status)
                raise DependencyUnavailable(
                    "Mapping proposals are unavailable right now. Map the columns by "
                    "hand - the manual path is unaffected."
                ) from error
            last_error = error
            delay_ms = BACKOFF_BASE_MS * (2 ** (attempt - 1))
            delay_ms += random.randint(0, BACKOFF_JITTER_MS)
            left_ms = (deadline_ns - time.monotonic_ns()) // NS_PER_MS
            if attempt < MAX_ATTEMPTS and left_ms - delay_ms >= MIN_ATTEMPT_MS:
                log.warning(
                    "llm_retrying",
                    model=model,
                    attempt=attempt,
                    status=status,
                    delay_ms=delay_ms,
                    budget_left_ms=left_ms,
                )
                time.sleep(delay_ms / MS_PER_SECOND)
                continue
            break

        latency_ms = (time.monotonic_ns() - started_ns) // NS_PER_MS
        usage = _usage_from(response)

        refusal = _refusal_text(response)
        if refusal is not None:
            log.warning("llm_refused", model=model, latency_ms=latency_ms)
            return LlmResult(
                outcome="refused",
                parsed=None,
                usage=usage,
                latency_ms=latency_ms,
                model=model,
                effort=effort,
                refusal=refusal,
            )

        reason = _incomplete_reason(response)
        if reason == "content_filter":
            # Logged without any cell content: the filter fired on data we sent, and
            # repeating it into a log is the one thing not to do about that.
            log.warning("llm_content_filtered", model=model, latency_ms=latency_ms)
            return LlmResult(
                outcome="content_filtered",
                parsed=None,
                usage=usage,
                latency_ms=latency_ms,
                model=model,
                effort=effort,
            )
        if reason is not None:
            log.warning(
                "llm_truncated", model=model, reason=reason, max_output_tokens=max_output_tokens
            )
            return LlmResult(
                outcome="truncated",
                parsed=None,
                usage=usage,
                latency_ms=latency_ms,
                model=model,
                effort=effort,
            )

        parsed = getattr(response, "output_parsed", None)
        log.info(
            "llm_parsed",
            model=model,
            latency_ms=latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
        )
        return LlmResult(
            outcome="ok" if parsed is not None else "truncated",
            parsed=parsed,
            usage=usage,
            latency_ms=latency_ms,
            model=model,
            effort=effort,
        )

    log.error(
        "llm_unavailable",
        model=model,
        elapsed_ms=(time.monotonic_ns() - started_ns) // NS_PER_MS,
        budget_ms=budget_ms,
    )
    raise DependencyUnavailable(
        "Mapping proposals are unavailable right now. Map the columns by hand - the "
        "manual path is unaffected."
    ) from last_error


def _call[ModelT: BaseModel](
    client: OpenAI,
    *,
    model: str,
    effort: str | None,
    instructions: str,
    user_input: str,
    text_format: type[ModelT],
    prompt_cache_key: str,
    max_output_tokens: int,
    #: Milliseconds left in the caller's budget, not the configured timeout: a retry gets
    #: what remains, so the whole call stays inside one ceiling.
    timeout_ms: int,
) -> ParsedResponse[ModelT]:
    """The single provider call.

    `reasoning` is sent only when an effort is configured, and a provider that rejects it
    is handled by the caller's `BadRequestError` branch - the plan's instruction is to
    verify support rather than send it hopefully, and the honest version of "verify" for
    an unreleased model id is "send it and believe the provider's answer".
    """
    arguments: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "text_format": text_format,
        "prompt_cache_key": prompt_cache_key,
        "max_output_tokens": max_output_tokens,
        # The SDK takes seconds. This quotient is the one place a fractional number appears,
        # and it is a duration handed to a third-party client - not a quantity anybody is paid
        # in, which is what the no-float rule is protecting.
        "timeout": timeout_ms / MS_PER_SECOND,
    }
    if effort:
        arguments["reasoning"] = {"effort": effort}
    return client.responses.parse(**arguments)
