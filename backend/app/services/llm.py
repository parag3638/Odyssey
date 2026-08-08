"""OpenAI LLM client — a thin, best-effort wrapper mirroring services/finnhub.py.

With no OPENAI_API_KEY (or on any error) methods return None / yield nothing, so
the AI advisory layer never breaks the app.

READ-ONLY SIDECAR: this module (and services/ai.py, routers/ai.py) must never be
imported by the order/risk path — app/risk.py, app/services/orders.py,
app/services/runner.py, app/services/copy_runner.py. The AI layer only reads
already-produced outcomes; it is never in the decision loop.
"""
from __future__ import annotations

import json
from typing import Iterator

from app.config import get_settings


class LlmClient:
    def __init__(self, token: str | None = None, model: str | None = None):
        s = get_settings()
        self.token = token if token is not None else s.openai_api_key
        self.model = model or s.openai_model

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _client(self):
        # Imported lazily so the app boots without the `openai` package installed
        # (the AI layer simply stays disabled until a key + package are present).
        from openai import OpenAI

        return OpenAI(api_key=self.token)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict,
        model: str | None = None,
        max_tokens: int = 700,
    ) -> dict | None:
        """One structured-output call. Returns a dict matching `schema`, or None on
        any failure (no key, network error, refusal, malformed JSON)."""
        if not self.enabled:
            return None
        try:
            # GPT-5.x models only accept `max_completion_tokens` (not `max_tokens`)
            # and the default temperature, so we pass neither `max_tokens` nor
            # `temperature`. This form is also accepted by the 4o-class models.
            resp = self._client().chat.completions.create(
                model=model or self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "ai_response", "strict": True, "schema": schema},
                },
                max_completion_tokens=max_tokens,
            )
            content = resp.choices[0].message.content
            return json.loads(content) if content else None
        except Exception:
            return None

    def chat(
        self,
        *,
        messages: list[dict],
        system: str,
        model: str | None = None,
        max_tokens: int = 900,
    ) -> str | None:
        """One non-streaming chat completion over a message list (used as the
        streaming fallback). Returns the text, or None on any failure."""
        if not self.enabled:
            return None
        try:
            resp = self._client().chat.completions.create(
                model=model or self.model,
                messages=[{"role": "system", "content": system}, *messages],
                max_completion_tokens=max_tokens,
            )
            return resp.choices[0].message.content or None
        except Exception:
            return None

    def stream_messages(
        self,
        *,
        messages: list[dict],
        system: str,
        model: str | None = None,
        max_tokens: int = 900,
    ) -> Iterator[str]:
        """Yield text tokens for a chat message list. Yields nothing on failure."""
        if not self.enabled:
            return
        try:
            stream = self._client().chat.completions.create(
                model=model or self.model,
                messages=[{"role": "system", "content": system}, *messages],
                max_completion_tokens=max_tokens,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception:
            return


def get_llm() -> LlmClient:
    return LlmClient()
