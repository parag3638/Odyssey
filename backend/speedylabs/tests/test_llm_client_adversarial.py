"""Adversarial tester pass on `src/lib/llm.py` (mvp.md 6.1), which had **zero** direct
test coverage before this file: every existing test that touches the propose flow
monkeypatches `llm.parse` itself, replacing the retry loop, the backoff, the refusal/
truncation branch detection and the key-redaction promise wholesale rather than
exercising any of them.

This file tests the module directly: the branch-detection helpers against real SDK
types (not hand-rolled dicts, so a wrong accessor would actually fail), the retry/
backoff policy by stubbing the client the module builds, and the timeout budget and
key-redaction promises against a real stalling server and real log output.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIStatusError, BadRequestError
from openai.types.responses import ResponseOutputMessage, ResponseOutputRefusal, ResponseOutputText
from openai.types.responses.response import IncompleteDetails
from pydantic import BaseModel

from src import config
from src.lib import llm
from src.lib.errors import DependencyUnavailable

SECRET_KEY = "sk-test-SUPER-SECRET-DO-NOT-LEAK-1234567890"


class _Toy(BaseModel):
    ok: bool


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET_KEY)
    monkeypatch.setenv("OPENAI_MODEL", "stub-model")
    monkeypatch.setenv("OPENAI_PRICE_INPUT_PER_MTOK", "2.50")
    monkeypatch.setenv("OPENAI_PRICE_CACHED_INPUT_PER_MTOK", "0.25")
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_MTOK", "10.00")
    monkeypatch.setenv("LLM_DAILY_SPEND_CAP_USD", "5.00")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    config.load.cache_clear()


def _status_error(status: int, cls: type[Exception] = APIStatusError) -> Exception:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status, request=request)
    return cls("boom", response=response, body=None)  # type: ignore[call-arg]


# ----------------------------------------------------------------------------------------
# Branch detection, against real SDK types - the highest-risk claim in the module's
# docstring ("a refusal is not `message.refusal`"). Verified independently in this
# session against the installed openai==3.24.0 before writing these:
#   ResponseOutputMessage.content: list[ResponseOutputText | ResponseOutputRefusal]
#   ResponseOutputMessage.type: Literal["message"]
#   ResponseOutputRefusal.type: Literal["refusal"]
#   IncompleteDetails.reason: Literal["max_output_tokens","max_messages",
#                                      "content_filter","steered"] | None
# ----------------------------------------------------------------------------------------


def _message(*parts: Any) -> ResponseOutputMessage:
    return ResponseOutputMessage.model_construct(
        type="message", id="msg_1", role="assistant", status="completed", content=list(parts)
    )


def _refusal_text(response: object) -> str | None:
    """Thin wrapper around the private helper under test.

    `llm._refusal_text` only reads `.output` off its argument, so a `SimpleNamespace`
    carrying that one attribute is a faithful stand-in for the real `ParsedResponse` -
    narrower than constructing a full response object, but exactly the surface the
    function actually touches. The two `pyright: ignore`s below are this file's whole
    concession to that: private-member access (deliberate - that is the function under
    test) and the duck-typed argument.
    """
    return llm._refusal_text(response)  # type: ignore[arg-type]


def _incomplete_reason(response: object) -> str | None:
    return llm._incomplete_reason(response)  # type: ignore[arg-type]


def test_refusal_text_fires_on_the_real_nested_shape_not_message_refusal() -> None:
    refusal_part = ResponseOutputRefusal.model_construct(
        type="refusal", refusal="I can't help with that."
    )
    response = SimpleNamespace(output=[_message(refusal_part)])
    assert _refusal_text(response) == "I can't help with that."


def test_refusal_text_is_none_when_the_message_only_has_text() -> None:
    text_part = ResponseOutputText.model_construct(type="output_text", text="hi", annotations=[])
    response = SimpleNamespace(output=[_message(text_part)])
    assert _refusal_text(response) is None


def test_refusal_text_ignores_non_message_output_items() -> None:
    """A reasoning or function-call item must not be walked as if it had `.content`
    shaped like a message's - the real failure mode of reading the wrong accessor."""
    other_item = SimpleNamespace(type="reasoning")
    response = SimpleNamespace(output=[other_item])
    assert _refusal_text(response) is None


def test_refusal_text_handles_a_mixed_content_list() -> None:
    """A real refusal response mixes a leading explanation and the refusal part in one
    message's content list - the loop must not stop at the first (non-refusal) part."""
    text_part = ResponseOutputText.model_construct(
        type="output_text", text="I will not", annotations=[]
    )
    refusal_part = ResponseOutputRefusal.model_construct(type="refusal", refusal="policy")
    response = SimpleNamespace(output=[_message(text_part, refusal_part)])
    assert _refusal_text(response) == "policy"


@pytest.mark.parametrize(
    ("status", "reason", "expect_truncated", "expect_filtered"),
    [
        ("incomplete", "max_output_tokens", True, False),
        ("incomplete", "content_filter", False, True),
        ("incomplete", "max_messages", True, False),  # falls into "truncated", not silently ignored
        ("completed", None, False, False),
    ],
)
def test_incomplete_reason_reads_status_and_details_not_a_finish_reason_field(
    status: str, reason: str | None, expect_truncated: bool, expect_filtered: bool
) -> None:
    details = None if reason is None else IncompleteDetails.model_construct(reason=reason)
    response = SimpleNamespace(status=status, incomplete_details=details)
    got = _incomplete_reason(response)
    if status != "incomplete":
        assert got is None
    else:
        assert got == reason
    assert (got == "max_output_tokens" or got == "max_messages") is expect_truncated
    assert (got == "content_filter") is expect_filtered


def test_incomplete_reason_is_none_when_incomplete_details_itself_is_missing() -> None:
    """`status == "incomplete"` with no `incomplete_details` at all must not crash -
    the provider is allowed to say "incomplete" without saying why."""
    response = SimpleNamespace(status="incomplete", incomplete_details=None)
    assert _incomplete_reason(response) is None


# ----------------------------------------------------------------------------------------
# Retry policy: 3 attempts total, jittered exponential backoff, no retry on 4xx.
# ----------------------------------------------------------------------------------------


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, calls: list[Any], side_effects: list[Any]
) -> None:
    """Replace the client `llm.parse` builds with a stub whose `.responses.parse` pops
    one scripted outcome per call: an `Exception` instance is raised, anything else is
    returned as the "response"."""

    def fake_client(api_key: str, timeout_seconds: int) -> Any:
        del api_key, timeout_seconds

        def fake_parse(**kwargs: Any) -> Any:
            calls.append(kwargs)
            outcome = side_effects[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return SimpleNamespace(responses=SimpleNamespace(parse=fake_parse))

    monkeypatch.setattr(llm, "_client", fake_client)


def _recording_sleep(delays: list[float]) -> Callable[[float], None]:
    def sleep(seconds: float) -> None:
        delays.append(seconds)

    return sleep


def _forbidden_sleep(message: str) -> Callable[[float], None]:
    def sleep(seconds: float) -> None:
        del seconds
        pytest.fail(message)

    return sleep


def _noop_sleep(seconds: float) -> None:
    del seconds


def test_three_consecutive_500s_exhaust_every_attempt_then_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    calls: list[Any] = []
    delays: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", _recording_sleep(delays))
    _patch_client(monkeypatch, calls, [_status_error(500) for _ in range(3)])

    with pytest.raises(DependencyUnavailable):
        llm.parse(
            instructions="x",
            user_input="y",
            text_format=_Toy,
            prompt_cache_key="k",
            max_output_tokens=10,
        )

    assert len(calls) == 3, "exactly MAX_ATTEMPTS calls, not more and not fewer"
    assert len(delays) == 2, "a sleep between attempts 1-2 and 2-3, none after the last"
    # Exponential with jitter: base * 2**(attempt-1) <= delay < base * 2**(attempt-1) + jitter.
    # The backoff constants became **milliseconds** when the retry policy gained one deadline
    # for the whole call (`src/lib/llm.py`); `time.sleep` still takes seconds, so the bounds
    # are converted here. The assertion itself is unchanged.
    base = llm.BACKOFF_BASE_MS / llm.MS_PER_SECOND
    jitter = llm.BACKOFF_JITTER_MS / llm.MS_PER_SECOND
    assert base <= delays[0] < base + jitter
    assert base * 2 <= delays[1] < base * 2 + jitter


def test_a_400_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    calls: list[Any] = []
    monkeypatch.setattr(llm.time, "sleep", _forbidden_sleep("must not back off on a 400"))
    _patch_client(monkeypatch, calls, [_status_error(400, BadRequestError)])

    with pytest.raises(DependencyUnavailable, match="misconfigured"):
        llm.parse(
            instructions="x",
            user_input="y",
            text_format=_Toy,
            prompt_cache_key="k",
            max_output_tokens=10,
        )

    assert len(calls) == 1, "a 400 is our fault and retrying it wastes the user's 30s for nothing"


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_a_non_500_provider_status_is_never_retried(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """401/403/404/429 are all `APIStatusError` with `status_code < 500` - the branch
    that must short-circuit before the retry loop, same as the explicit 400 case."""
    _configure(monkeypatch)
    calls: list[Any] = []
    monkeypatch.setattr(llm.time, "sleep", _forbidden_sleep(f"must not back off on a {status}"))
    _patch_client(monkeypatch, calls, [_status_error(status)])

    with pytest.raises(DependencyUnavailable):
        llm.parse(
            instructions="x",
            user_input="y",
            text_format=_Toy,
            prompt_cache_key="k",
            max_output_tokens=10,
        )

    assert len(calls) == 1, f"a {status} is not transient; retrying it cannot change the answer"


def test_a_500_then_success_recovers_without_exhausting_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    calls: list[Any] = []
    monkeypatch.setattr(llm.time, "sleep", _noop_sleep)
    ok_response = SimpleNamespace(
        usage=None,
        output=[],
        status="completed",
        incomplete_details=None,
        output_parsed=_Toy(ok=True),
    )
    _patch_client(monkeypatch, calls, [_status_error(500), ok_response])

    result = llm.parse(
        instructions="x",
        user_input="y",
        text_format=_Toy,
        prompt_cache_key="k",
        max_output_tokens=10,
    )
    assert len(calls) == 2
    assert result.outcome == "ok"
    assert result.parsed == _Toy(ok=True)


# ----------------------------------------------------------------------------------------
# Secrets never appear in a log line or an error body; the model id always does.
# ----------------------------------------------------------------------------------------


def test_the_api_key_never_appears_in_logs_or_the_raised_error_on_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(monkeypatch)
    calls: list[Any] = []
    monkeypatch.setattr(llm.time, "sleep", _noop_sleep)
    _patch_client(monkeypatch, calls, [_status_error(500) for _ in range(3)])

    with pytest.raises(DependencyUnavailable) as excinfo:
        llm.parse(
            instructions="x",
            user_input="y",
            text_format=_Toy,
            prompt_cache_key="k",
            max_output_tokens=10,
        )

    captured = capsys.readouterr()
    haystacks = [captured.out, captured.err, str(excinfo.value)]
    for haystack in haystacks:
        assert SECRET_KEY not in haystack, f"the API key leaked into: {haystack!r}"
    # The model id, by contrast, is meant to be there - it is what makes the failure
    # diagnosable. If this assertion ever fails it means the positive claim ("the model
    # id appears... on purpose") regressed, not that redaction got stricter than needed.
    assert "stub-model" in captured.out or "stub-model" in captured.err


def test_the_api_key_never_appears_in_logs_on_a_400(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(monkeypatch)
    calls: list[Any] = []
    _patch_client(monkeypatch, calls, [_status_error(400, BadRequestError)])

    with pytest.raises(DependencyUnavailable):
        llm.parse(
            instructions="x",
            user_input="y",
            text_format=_Toy,
            prompt_cache_key="k",
            max_output_tokens=10,
        )

    captured = capsys.readouterr()
    assert SECRET_KEY not in captured.out
    assert SECRET_KEY not in captured.err


def test_is_available_is_false_and_the_app_does_not_crash_with_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config.load.cache_clear()
    assert llm.is_available() is False


# ----------------------------------------------------------------------------------------
# The timeout budget, against a real stalling server - not a mock of the SDK's own
# timeout handling, which would just prove the mock agrees with itself.
# ----------------------------------------------------------------------------------------


@pytest.fixture
def stalling_server() -> Iterator[int]:
    """A TCP server that accepts the connection and then never writes a byte back, so
    an HTTP client's *read* timeout (not its connect timeout) is what has to fire."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        port = sock.getsockname()[1]
        stop = threading.Event()
        accepted: list[socket.socket] = []

        def serve() -> None:
            sock.settimeout(0.2)
            while not stop.is_set():
                try:
                    conn, _ = sock.accept()
                except TimeoutError:
                    continue
                accepted.append(conn)
                conn.settimeout(5)
                with contextlib.suppress(OSError):
                    conn.recv(65536)  # read the request, then go silent forever
                # Deliberately never conn.sendall(...) - the client hangs waiting for
                # response headers until its own timeout fires.

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield port
        finally:
            stop.set()
            thread.join(timeout=2)
            for conn in accepted:
                conn.close()


def test_a_stalling_provider_raises_within_the_configured_budget_plus_one_second(
    monkeypatch: pytest.MonkeyPatch, stalling_server: int
) -> None:
    """mvp.md 6.1: 'raises `DependencyUnavailable` within that budget plus 1 s'.

    `LLM_TIMEOUT_SECONDS=1` here. The client-level timeout is what the module actually
    sets (`_client()`), so this exercises the real `openai`/`httpx` timeout machinery
    against a socket that genuinely never answers - not a mock standing in for it.
    """
    _configure(monkeypatch, LLM_TIMEOUT_SECONDS="1")
    base_url = f"http://127.0.0.1:{stalling_server}/v1"

    from openai import OpenAI

    def stalling_client(api_key: str, timeout_seconds: int) -> OpenAI:
        return OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0, base_url=base_url)

    monkeypatch.setattr(llm, "_client", stalling_client)
    # Isolate the timing claim from the separately-tested backoff delays: a fair reading
    # of "within budget plus 1s" is the time actually spent waiting on the provider.
    monkeypatch.setattr(llm.time, "sleep", _noop_sleep)

    started = time.monotonic()
    with pytest.raises(DependencyUnavailable):
        llm.parse(
            instructions="x",
            user_input="y",
            text_format=_Toy,
            prompt_cache_key="k",
            max_output_tokens=10,
        )
    elapsed = time.monotonic() - started

    budget = 1 + 1  # LLM_TIMEOUT_SECONDS + 1s, per the acceptance criterion's own wording
    assert elapsed < budget, (
        f"took {elapsed:.2f}s against a {budget}s budget - a persistent timeout is retried "
        f"MAX_ATTEMPTS={llm.MAX_ATTEMPTS} times, each allowed the *full* configured timeout "
        "again, so the real worst case is MAX_ATTEMPTS * LLM_TIMEOUT_SECONDS (plus backoff), "
        "not LLM_TIMEOUT_SECONDS + 1s. A user watching the spinner with LLM_TIMEOUT_SECONDS=30 "
        "configured can wait roughly 90s, not the ~30s the UI's hard-timeout framing promises."
    )
