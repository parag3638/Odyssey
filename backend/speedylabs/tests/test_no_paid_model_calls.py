"""The guard rails that keep a real `OPENAI_API_KEY` in `.env` from being spent by the suite.

Two mechanisms live in `conftest.py` (`_no_paid_model_calls`, autouse; `unconfigured_llm`, opt-in)
and this file proves each does its job, because a guard nobody tests is the exact shape of the
bug it replaces: three tests assumed "no key" in a docstring and spent about $0.16.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import BaseModel

from src import config
from src.lib import llm


class _Shape(BaseModel):
    pass


@pytest.fixture
def key_everywhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """The hazard itself: a model configured in the environment *and* in a `.env` file in the
    working directory, which is how a host-run pytest started from the repo root sees it."""
    folder = tmp_path_factory.mktemp("hazard")
    (folder / ".env").write_text(
        "OPENAI_API_KEY=sk-from-dotenv\nOPENAI_MODEL=model-from-dotenv\n"
        "OPENAI_PRICE_INPUT_PER_MTOK=1\nOPENAI_PRICE_CACHED_INPUT_PER_MTOK=1\n"
        "OPENAI_PRICE_OUTPUT_PER_MTOK=1\n"
    )
    monkeypatch.chdir(folder)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-environment")
    monkeypatch.setenv("OPENAI_MODEL", "model-from-environment")
    monkeypatch.setenv("OPENAI_PRICE_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("OPENAI_PRICE_CACHED_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_MTOK", "1")
    config.load.cache_clear()
    yield
    config.load.cache_clear()


def test_the_hazard_is_real_a_configured_environment_enables_the_model(
    key_everywhere: None,
) -> None:
    """The control: without the fixture under test, the same setup makes `llm_enabled` true."""
    del key_everywhere

    assert config.load().llm_enabled


def test_unconfigured_llm_wins_over_the_environment_and_a_dotenv_file(
    key_everywhere: None, unconfigured_llm: None
) -> None:
    del key_everywhere, unconfigured_llm

    settings = config.load()

    assert not settings.llm_enabled
    assert settings.openai_api_key is None
    assert settings.openai_model is None
    assert not llm.is_available()


def test_unconfigured_llm_makes_a_call_attempt_fail_loudly_not_silently(
    unconfigured_llm: None,
) -> None:
    del unconfigured_llm

    with pytest.raises(AssertionError, match=r"unconfigured-model test called llm\.parse"):
        llm.parse(
            instructions="",
            user_input="",
            text_format=_Shape,
            prompt_cache_key="",
            max_output_tokens=1,
        )


def test_the_default_suite_cannot_build_a_real_openai_client() -> None:
    with pytest.raises(AssertionError, match="never makes a paid call"):
        llm._client("sk-anything", 5)  # pyright: ignore[reportPrivateUsage]
