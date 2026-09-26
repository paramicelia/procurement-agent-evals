"""Default backend: shells out to the local Claude Code CLI, headless.

This is the backend used for every measured number in EVALS.md unless noted
otherwise. It runs on the machine owner's already-authenticated `claude`
CLI session — no API key of its own, no secrets in this file.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time

from .base import LLMProvider, LLMResponse


class ClaudeCLIProvider(LLMProvider):
    def __init__(
        self,
        model: str = "haiku",
        effort: str = "low",
        binary: str = "claude",
        timeout_s: float = 90.0,
    ):
        self.model = model
        self.effort = effort
        self.binary = shutil.which(binary) or binary
        self.timeout_s = timeout_s
        self.name = f"claude-cli:{model}"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        cmd = [
            self.binary,
            "--safe-mode",  # no CLAUDE.md / skills / hooks / MCP for this call
            "--model",
            self.model,
            "--effort",
            self.effort,
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
        ]
        if system:
            cmd += ["--append-system-prompt", system]
        if json_schema:
            cmd += ["--json-schema", json.dumps(json_schema)]
        cmd.append(prompt)

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_s, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude CLI timed out after {self.timeout_s}s") from exc
        latency = time.monotonic() - t0

        if not proc.stdout.strip():
            raise RuntimeError(
                f"claude CLI produced no stdout (rc={proc.returncode}): {proc.stderr[:2000]}"
            )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"claude CLI returned non-JSON stdout: {proc.stdout[:500]!r}"
            ) from exc
        if payload.get("is_error"):
            raise RuntimeError(f"claude CLI error result: {payload.get('result')!r}")

        usage = payload.get("usage", {}) or {}
        return LLMResponse(
            text=payload.get("result", "") or "",
            model=self.model,
            provider="claude-cli",
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cost_usd=payload.get("total_cost_usd", 0.0) or 0.0,
            latency_s=latency,
            raw=payload,
        )
