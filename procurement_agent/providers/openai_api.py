"""Optional backend using OPENAI_API_KEY, so this isn't Anthropic-only.

Lazy-imports `openai`. Key is read from the environment only.
"""
from __future__ import annotations

import os
import time

from .base import LLMProvider, LLMResponse


class OpenAIAPIProvider(LLMProvider):
    def __init__(self, model: str = "gpt-4o-mini", max_tokens: int = 1024):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. This backend never searches the "
                "filesystem for a key; export it in your own shell."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("pip install openai to use the openai-api backend") from exc
        self._client = OpenAI(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.name = f"openai-api:{model}"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        user_prompt = prompt
        if json_schema:
            user_prompt = (
                f"{prompt}\n\nRespond with ONLY JSON matching this schema, "
                f"no prose, no code fences:\n{json_schema}"
            )
        messages.append({"role": "user", "content": user_prompt})

        t0 = time.monotonic()
        resp = self._client.chat.completions.create(
            model=self.model, max_tokens=self.max_tokens, messages=messages
        )
        latency = time.monotonic() - t0
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return LLMResponse(
            text=text,
            model=self.model,
            provider="openai-api",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            # See anthropic_api.py: no hardcoded price table for API backends.
            cost_usd=0.0,
            latency_s=latency,
            raw={"id": resp.id},
        )
