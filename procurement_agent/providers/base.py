"""Provider interface. Every backend (CLI, Anthropic API, OpenAI API, cassette
replay) implements this so the agent and eval harness are backend-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        """One bounded, single-turn completion. No tool use, no filesystem or
        network access is granted to the model by any provider here — the
        agent's tools live in Python, not inside the LLM call."""
        raise NotImplementedError
