"""Optional backend for anyone running this without the Claude Code CLI:
talks to the Anthropic Messages API directly using ANTHROPIC_API_KEY.

The `anthropic` package is imported lazily so it's only a hard dependency
when this backend is actually selected. The key is read from the
environment only; it is never printed, logged, or written to disk here.
"""
from __future__ import annotations

import os
import time

from .base import LLMProvider, LLMResponse


class AnthropicAPIProvider(LLMProvider):
    def __init__(self, model: str = "claude-haiku-4-5", max_tokens: int = 1024):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. This backend never searches the "
                "filesystem for a key; export it in your own shell."
            )
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("pip install anthropic to use the anthropic-api backend") from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.name = f"anthropic-api:{model}"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        user_prompt = prompt
        if json_schema:
            # The Messages API has no first-class json_schema param here;
            # ask for it in-prompt and rely on extract_quote's defensive parse.
            user_prompt = (
                f"{prompt}\n\nRespond with ONLY JSON matching this schema, "
                f"no prose, no code fences:\n{json_schema}"
            )
        t0 = time.monotonic()
        kwargs: dict = {}
        if system:
            kwargs["system"] = system
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": user_prompt}],
            **kwargs,
        )
        latency = time.monotonic() - t0
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return LLMResponse(
            text=text,
            model=self.model,
            provider="anthropic-api",
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            # Per-model $/token changes over time and this repo does not
            # hardcode a price table for the direct-API path; cost is left
            # at 0.0 here (see README) — only the CLI backend reports cost,
            # from the CLI's own usage-based billing figure.
            cost_usd=0.0,
            latency_s=latency,
            raw={"id": msg.id, "stop_reason": msg.stop_reason},
        )
