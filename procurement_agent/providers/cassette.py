"""Replays recorded LLM responses from a JSONL cassette file.

Used by CI and by the fast local test/eval subset so neither needs network
access, a CLI session, or an API key. Records are keyed by a hash of
(system, prompt); an unmatched call is a hard error rather than a silent
fallback, so cassette drift (a prompt changed since the cassette was
recorded) is caught immediately instead of quietly evaluating stale data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .base import LLMProvider, LLMResponse


def cassette_key(prompt: str, system: str | None) -> str:
    h = hashlib.sha256()
    h.update((system or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    return h.hexdigest()


class CassetteProvider(LLMProvider):
    def __init__(self, cassette_path: str, strict: bool = True):
        self.cassette_path = cassette_path
        self.strict = strict
        self.name = f"cassette:{Path(cassette_path).name}"
        self._table: dict[str, dict] = {}
        p = Path(cassette_path)
        if p.exists():
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                self._table[rec["key"]] = rec

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        key = cassette_key(prompt, system)
        rec = self._table.get(key)
        if rec is None:
            if self.strict:
                raise KeyError(
                    f"no cassette entry for this prompt (key={key[:12]}...) in "
                    f"{self.cassette_path}. Re-record with scripts/record_cassette.py."
                )
            return LLMResponse(text="{}", model="cassette-miss", provider="cassette")
        return LLMResponse(
            text=rec["text"],
            model=rec.get("model", "cassette"),
            provider="cassette",
            input_tokens=rec.get("input_tokens", 0),
            output_tokens=rec.get("output_tokens", 0),
            cost_usd=0.0,
            latency_s=0.0,
            raw=rec,
        )

    @staticmethod
    def append(cassette_path: str, prompt: str, system: str | None, response: LLMResponse) -> None:
        rec = {
            "key": cassette_key(prompt, system),
            "text": response.text,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        Path(cassette_path).parent.mkdir(parents=True, exist_ok=True)
        with open(cassette_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
