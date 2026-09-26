#!/usr/bin/env python3
"""Record a small cassette of real claude-cli responses for a fixed subset of
fixtures, so CI and fast local runs never need network/CLI access.

    python scripts/record_cassette.py --ids q000_auto_accept q001_auto_accept ... \
        --out evals/cassettes/ci_subset.jsonl

Re-run this whenever the extraction prompt (procurement_agent/tools.py) or
the CI subset's fixture ids change — a stale cassette is a hard KeyError at
eval time (see providers/cassette.py), never a silent pass.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from procurement_agent.providers import get_provider  # noqa: E402
from procurement_agent.providers.cassette import CassetteProvider  # noqa: E402
from procurement_agent.tools import EXTRACT_SYSTEM_PROMPT, QUOTE_JSON_SCHEMA  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--out", default=str(ROOT / "evals/cassettes/ci_subset.jsonl"))
    args = ap.parse_args()

    provider = get_provider("claude-cli", model=args.model, effort=args.effort)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    for task_id in args.ids:
        raw_text = (Path(args.data_dir) / "emails" / f"{task_id}.txt").read_text()
        resp = provider.complete(raw_text, system=EXTRACT_SYSTEM_PROMPT, json_schema=QUOTE_JSON_SCHEMA)
        CassetteProvider.append(str(out_path), raw_text, EXTRACT_SYSTEM_PROMPT, resp)
        print(f"recorded {task_id}: cost=${resp.cost_usd:.4f} latency={resp.latency_s:.1f}s")

    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
