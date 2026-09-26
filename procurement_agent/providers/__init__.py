from __future__ import annotations

from .base import LLMProvider, LLMResponse


def get_provider(name: str, **kwargs) -> LLMProvider:
    """Factory so the rest of the code never imports a concrete provider directly."""
    key = (name or "claude-cli").lower().replace("_", "-")
    if key in ("claude-cli", "cli", "claude"):
        from .claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(**kwargs)
    if key in ("anthropic", "anthropic-api"):
        from .anthropic_api import AnthropicAPIProvider

        return AnthropicAPIProvider(**kwargs)
    if key in ("openai", "openai-api"):
        from .openai_api import OpenAIAPIProvider

        return OpenAIAPIProvider(**kwargs)
    if key in ("cassette",):
        from .cassette import CassetteProvider

        return CassetteProvider(**kwargs)
    raise ValueError(f"unknown provider: {name!r} (known: claude-cli, anthropic, openai, cassette)")


__all__ = ["LLMProvider", "LLMResponse", "get_provider"]
