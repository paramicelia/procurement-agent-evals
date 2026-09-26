#!/usr/bin/env python3
"""Eval harness: runs extract_quote -> compare_to_catalog -> decide over the
labeled synthetic set and reports field-extraction accuracy, action
accuracy, escalation precision/recall, cost/latency, and a failure taxonomy.

    python evals/run_eval.py --provider claude-cli --model haiku --out evals/results
    python evals/run_eval.py --provider cassette --cassette evals/cassettes/ci_subset.jsonl --limit 12

This does NOT drive the orchestrator/approval/order-placement machinery —
that plumbing is covered by tests/test_invariants.py. This harness measures
one thing: how good is the LLM extraction + the deterministic policy at
turning a raw email into the right action, end to end.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.metrics import classify_failure, score_extraction  # noqa: E402
from procurement_agent.models import ExtractedQuote  # noqa: E402
from procurement_agent.providers import get_provider  # noqa: E402
from procurement_agent.tools import compare_to_catalog, decide, extract_quote  # noqa: E402
from procurement_agent.tracing import Tracer  # noqa: E402


def load_fixtures(data_dir: Path, limit: int = 0, ids: list[str] | None = None) -> list[dict]:
    labels_dir = data_dir / "labels"
    emails_dir = data_dir / "emails"
    items = []
    for label_path in sorted(labels_dir.glob("*.json")):
        label = json.loads(label_path.read_text())
        if ids and label["id"] not in ids:
            continue
        raw_text = (emails_dir / f"{label['id']}.txt").read_text()
        items.append({"label": label, "raw_text": raw_text})
    if ids:
        order = {tid: i for i, tid in enumerate(ids)}
        items.sort(key=lambda it: order[it["label"]["id"]])
    if limit:
        items = items[:limit]
    return items


def run_one(item: dict, provider, tracer: Tracer) -> dict:
    label = item["label"]
    raw_text = item["raw_text"]
    task_id = label["id"]
    gt = label["ground_truth"]
    expected = ExtractedQuote(**gt["extracted"])

    t0 = time.monotonic()
    error = None
    predicted = ExtractedQuote()
    predicted_action = "extraction_error"
    predicted_reason = ""
    usage = {"cost_usd": 0.0, "latency_s": 0.0, "input_tokens": 0, "output_tokens": 0, "model": provider.name}
    try:
        with tracer.span(task_id, "extract_quote") as span:
            predicted, resp = extract_quote(raw_text, provider)
            span.set_io(input={"raw_text_chars": len(raw_text)}, output=predicted.model_dump())
            span.set_usage(resp)
        usage = {
            "cost_usd": resp.cost_usd, "latency_s": resp.latency_s,
            "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens, "model": resp.model,
        }
        with tracer.span(task_id, "compare_to_catalog") as span:
            comparison = compare_to_catalog(predicted)
            span.set_io(output=comparison.model_dump())
        with tracer.span(task_id, "decide") as span:
            decision = decide(predicted, comparison, raw_text)
            span.set_io(output=decision.model_dump())
        predicted_action = decision.action.value
        predicted_reason = decision.reason
    except Exception as exc:  # noqa: BLE001 - eval harness must not crash on one bad item
        error = f"{type(exc).__name__}: {exc}"

    wall_s = time.monotonic() - t0
    ext_score = score_extraction(predicted, expected)
    failure = None
    if error is None:
        failure = classify_failure(
            predicted, expected, predicted_action, gt["action"], ext_score, label["has_injection"],
            raw_text=raw_text,
        )

    return {
        "id": task_id,
        "category": label["category"],
        "format": label["format"],
        "has_injection": label["has_injection"],
        "error": error,
        "expected_action": gt["action"],
        "predicted_action": predicted_action,
        "predicted_reason": predicted_reason,
        "expected_reason": gt["reason"],
        "extraction_accuracy": ext_score.accuracy,
        "extraction_fields_total": ext_score.total,
        "extraction_fields_correct": ext_score.correct,
        "failure_category": failure,
        "cost_usd": usage["cost_usd"],
        "latency_s": usage["latency_s"],
        "wall_s": wall_s,
        "model": usage["model"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "predicted_extracted": predicted.model_dump(),
        "raw_excerpt": raw_text[:220],
    }


def aggregate(results: list[dict]) -> dict:
    n = len(results)
    ok = [r for r in results if r["error"] is None]
    errors = [r for r in results if r["error"] is not None]

    action_correct = sum(1 for r in ok if r["predicted_action"] == r["expected_action"])
    extraction_acc = [r["extraction_accuracy"] for r in ok]

    tp = sum(1 for r in ok if r["predicted_action"] == "escalate_human" and r["expected_action"] == "escalate_human")
    fp = sum(1 for r in ok if r["predicted_action"] == "escalate_human" and r["expected_action"] != "escalate_human")
    fn = sum(1 for r in ok if r["predicted_action"] != "escalate_human" and r["expected_action"] == "escalate_human")
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None

    injection_items = [r for r in ok if r["has_injection"]]
    injection_recall = (
        sum(1 for r in injection_items if r["predicted_action"] == "escalate_human") / len(injection_items)
        if injection_items else None
    )

    costs = [r["cost_usd"] for r in ok if r["cost_usd"]]
    latencies = sorted(r["latency_s"] for r in ok if r["latency_s"])

    def pct(data: list[float], p: float) -> float | None:
        if not data:
            return None
        k = max(0, min(len(data) - 1, int(round(p * (len(data) - 1)))))
        return data[k]

    failure_counts: dict[str, int] = {}
    for r in ok:
        if r["failure_category"]:
            failure_counts[r["failure_category"]] = failure_counts.get(r["failure_category"], 0) + 1

    by_category: dict[str, dict] = {}
    for r in ok:
        c = r["category"]
        by_category.setdefault(c, {"n": 0, "action_correct": 0})
        by_category[c]["n"] += 1
        if r["predicted_action"] == r["expected_action"]:
            by_category[c]["action_correct"] += 1

    return {
        "n": n,
        "n_ok": len(ok),
        "n_errors": len(errors),
        "errors": [{"id": r["id"], "error": r["error"]} for r in errors],
        "action_accuracy": action_correct / len(ok) if ok else None,
        "extraction_accuracy_mean": statistics.mean(extraction_acc) if extraction_acc else None,
        "extraction_accuracy_median": statistics.median(extraction_acc) if extraction_acc else None,
        "escalation_precision": precision,
        "escalation_recall": recall,
        "escalation_tp": tp, "escalation_fp": fp, "escalation_fn": fn,
        "injection_recall": injection_recall,
        "injection_n": len(injection_items),
        "cost_usd_total": sum(costs) if costs else 0.0,
        "cost_usd_mean": statistics.mean(costs) if costs else 0.0,
        "latency_s_p50": pct(latencies, 0.5),
        "latency_s_p95": pct(latencies, 0.95),
        "latency_s_mean": statistics.mean(latencies) if latencies else None,
        "failure_counts": failure_counts,
        "by_category": by_category,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--provider", default="claude-cli")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--cassette", default=str(ROOT / "evals/cassettes/ci_subset.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "evals/results"))
    ap.add_argument("--trace", default=str(ROOT / "traces/eval_run.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-budget-usd", type=float, default=8.0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    if args.provider == "cassette":
        provider = get_provider("cassette", cassette_path=args.cassette)
    elif args.provider == "claude-cli":
        provider = get_provider("claude-cli", model=args.model, effort=args.effort)
    else:
        provider = get_provider(args.provider, model=args.model)

    fixtures = load_fixtures(Path(args.data_dir), limit=args.limit, ids=args.ids)
    tracer = Tracer(args.trace)
    results: list[dict] = []
    spent = {"usd": 0.0}
    # guarded_run's "check spent, then call the provider, then add to spent"
    # was a check-then-spend race across ThreadPoolExecutor workers: several
    # already-dispatched calls could all complete after the cap was reached,
    # oversubscribing it by up to concurrency-1 calls' worth of spend. This
    # lock only needs to protect the shared counter, not the (slow) provider
    # call itself, so it doesn't reduce concurrency.
    spent_lock = threading.Lock()

    print(f"running {len(fixtures)} fixtures through provider={provider.name} "
          f"(concurrency={args.concurrency}, budget cap ${args.max_budget_usd:.2f})")

    def guarded_run(item: dict) -> dict:
        with spent_lock:
            if spent["usd"] >= args.max_budget_usd:
                return {**{k: None for k in ("id",)}, "id": item["label"]["id"], "skipped_budget": True,
                        "category": item["label"]["category"], "format": item["label"]["format"],
                        "has_injection": item["label"]["has_injection"], "error": "skipped: budget cap reached",
                        "expected_action": item["label"]["ground_truth"]["action"], "predicted_action": "skipped",
                        "predicted_reason": "", "expected_reason": item["label"]["ground_truth"]["reason"],
                        "extraction_accuracy": 0.0, "extraction_fields_total": 0, "extraction_fields_correct": 0,
                        "failure_category": None, "cost_usd": 0.0, "latency_s": 0.0, "wall_s": 0.0,
                        "model": provider.name, "input_tokens": 0, "output_tokens": 0,
                        "predicted_extracted": {}, "raw_excerpt": ""}
        r = run_one(item, provider, tracer)
        with spent_lock:
            spent["usd"] += r.get("cost_usd") or 0.0
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for r in ex.map(guarded_run, fixtures):
            results.append(r)
            print(f"  {r['id']:<28} expected={r['expected_action']:<22} got={r['predicted_action']:<22} "
                  f"ext_acc={r['extraction_accuracy']:.2f}" if r.get("extraction_accuracy") is not None else
                  f"  {r['id']:<28} ERROR: {r['error']}")

    tracer.close()
    agg = aggregate(results)

    ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": ts,
        "tag": args.tag,
        "provider": provider.name,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "n_fixtures": len(fixtures),
        "aggregate": agg,
        "results": results,
    }
    result_path = out_dir / f"{ts}{'_' + args.tag if args.tag else ''}.json"
    result_path.write_text(json.dumps(payload, indent=2, default=str))
    (out_dir / "latest.json").write_text(json.dumps(payload, indent=2, default=str))

    print("\n=== aggregate ===")
    print(json.dumps(agg, indent=2, default=str))
    print(f"\nwrote {result_path}")


if __name__ == "__main__":
    main()
