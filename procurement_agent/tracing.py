"""Structured JSONL tracing: one line per tool-call span.

Each span records the tool name, timing, status, and (redacted) input/output.
Raw email text is never written into a trace file — only its length — so
traces stay safe to share even though the synthetic corpus is fine either way.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REDACT_KEYS = {"raw_text", "email_body", "body"}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if k in REDACT_KEYS else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


@dataclass
class _Span:
    tracer: Tracer
    task_id: str
    tool: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    start: float = field(default_factory=time.monotonic)
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None

    def set_io(self, input: dict | None = None, output: dict | None = None) -> None:
        if input is not None:
            self.input = input
        if output is not None:
            self.output = output

    def set_usage(self, resp) -> None:
        self.usage = {
            "model": resp.model,
            "provider": resp.provider,
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "cost_usd": resp.cost_usd,
            "latency_s": round(resp.latency_s, 3),
        }

    def __enter__(self) -> _Span:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_ms = (time.monotonic() - self.start) * 1000
        if exc is not None:
            self.status = "error"
            self.error = f"{exc_type.__name__}: {exc}"
        rec = {
            "run_id": self.tracer.run_id,
            "task_id": self.task_id,
            "span_id": self.span_id,
            "tool": self.tool,
            "ts": time.time(),
            "duration_ms": round(duration_ms, 2),
            "status": self.status,
            "error": self.error,
            "input": _redact(self.input),
            "output": _redact(self.output),
            "usage": self.usage,
        }
        self.tracer._write(rec)
        return False  # never swallow exceptions


class Tracer:
    def __init__(self, path: str, run_id: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._fh = open(self.path, "a")
        # evals/run_eval.py shares one Tracer across a ThreadPoolExecutor
        # (--concurrency workers); without this lock, two threads' writes to
        # the same file handle could interleave mid-line and corrupt the
        # JSONL trace file. Not just a hypothetical: this only relied on
        # CPython's GIL making a single buffered write "usually" atomic,
        # which was never guaranteed and was untested.
        self._lock = threading.Lock()

    def span(self, task_id: str, tool: str) -> _Span:
        return _Span(self, task_id, tool)

    def _write(self, rec: dict) -> None:
        line = json.dumps(rec) + "\n"
        with self._lock:
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    @staticmethod
    def null() -> Tracer:
        return _NullTracer()


class _NullTracer(Tracer):
    def __init__(self) -> None:
        self.run_id = "null"

    def span(self, task_id: str, tool: str) -> _Span:
        return _Span(self, task_id, tool)

    def _write(self, rec: dict) -> None:
        pass

    def close(self) -> None:
        pass
