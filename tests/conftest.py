from __future__ import annotations

import json

import pytest

from procurement_agent.providers.base import LLMProvider, LLMResponse
from procurement_agent.store import Store


class ScriptedProvider(LLMProvider):
    """Returns pre-scripted responses in call order; counts calls per label."""

    name = "scripted"

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt: str, *, system: str | None = None, json_schema: dict | None = None) -> LLMResponse:
        self.calls.append(prompt[:40])
        if not self._responses:
            raise AssertionError("ScriptedProvider ran out of responses")
        data = self._responses.pop(0)
        text = data if isinstance(data, str) else json.dumps(data)
        return LLMResponse(text=text, model="scripted", provider="scripted")


def quote_response(supplier="Acme", currency="EUR", total=11.0,
                    items=None, missing_fields=None) -> dict:
    return {
        "supplier": supplier,
        "currency": currency,
        "total": total,
        "items": items or [{"description": "M6 Hex Bolt", "qty": 100, "unit_price": 0.11}],
        "missing_fields": missing_fields or [],
    }


@pytest.fixture()
def store() -> Store:
    s = Store(":memory:")
    yield s
    s.close()
