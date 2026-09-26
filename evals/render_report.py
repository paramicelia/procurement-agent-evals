#!/usr/bin/env python3
"""Generate EVALS.md from a committed results JSON. Every number in EVALS.md
comes from this script reading evals/results/latest.json — nobody hand-types
a metric into that file. Re-run after every `python evals/run_eval.py`.

    python evals/render_report.py evals/results/latest.json EVALS.md
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

FAILURE_LABELS = {
    "generator_currency_not_stated_in_text": (
        "Synthetic email never states a currency (no code, no symbol) for an "
        "item whose price is otherwise missing. The model correctly returned "
        "`currency: null` instead of guessing; this is a gap in the data "
        "generator, not an extraction mistake. See LIMITATIONS.md."
    ),
    "injection_not_escalated": "An embedded prompt-injection attempt was NOT routed to escalate_human.",
    "currency_misread": "Currency IS stated in the text but the model extracted the wrong one.",
    "hallucinated_value_for_missing_field": "Model invented a qty/price value for a field the source never stated.",
    "wrongly_flagged_present_field_as_missing": "Model said a field was missing when the source did state it.",
    "item_description_misread": "Line-item description didn't match the source closely enough to score correct.",
    "total_misread": "Extracted total differs from the stated total by more than the 2% tolerance.",
    "action_mismatch_from_correct_extraction": "Extraction was fine but decide() still picked the wrong action.",
    "other": "Didn't fit a more specific bucket; see the raw per-item record.",
}


def fmt_pct(x) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def fmt_num(x, digits=2) -> str:
    return f"{x:.{digits}f}" if x is not None else "n/a"


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <results.json> <EVALS.md>", file=sys.stderr)
        raise SystemExit(2)
    results_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    payload = json.loads(results_path.read_text())
    agg = payload["aggregate"]
    results = payload["results"]

    generated_at = payload.get("generated_at", "")
    try:
        date_str = dt.datetime.fromisoformat(generated_at).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        date_str = generated_at

    # Categories are the generator's *intent*; the label is whatever decide()
    # returns on the ground-truth extraction, and the two can differ (e.g. a
    # JPY price rounded to a whole yen can move a line past the 8% band). So
    # show which expected actions each category actually contains, and a
    # separate breakdown by expected action.
    ok_results = [r for r in results if not r.get("error")]
    cat_actions: dict[str, dict[str, int]] = {}
    by_action: dict[str, dict[str, int]] = {}
    for r in ok_results:
        cat_actions.setdefault(r["category"], {}).setdefault(r["expected_action"], 0)
        cat_actions[r["category"]][r["expected_action"]] += 1
        a = by_action.setdefault(r["expected_action"], {"n": 0, "correct": 0})
        a["n"] += 1
        a["correct"] += int(r["predicted_action"] == r["expected_action"])

    def _actions(cat: str) -> str:
        return ", ".join(f"{a} ×{n}" for a, n in sorted(cat_actions.get(cat, {}).items()))

    by_cat_rows = "\n".join(
        f"| {cat} | {v['n']} | {_actions(cat)} | {v['action_correct']} | "
        f"{fmt_pct(v['action_correct'] / v['n'] if v['n'] else None)} |"
        for cat, v in sorted(agg["by_category"].items())
    )
    by_action_rows = "\n".join(
        f"| {a} | {v['n']} | {v['correct']} | {fmt_pct(v['correct'] / v['n'] if v['n'] else None)} |"
        for a, v in sorted(by_action.items())
    )
    mixed = sorted(
        (r["id"], r["category"], r["expected_action"], r["expected_reason"])
        for r in ok_results
        if len(cat_actions.get(r["category"], {})) > 1
        and r["expected_action"] != max(cat_actions[r["category"]], key=cat_actions[r["category"]].get)
    )
    mixed_note = (
        "Categories whose fixtures don't all share one expected action: "
        + "; ".join(f"`{i}` (category `{c}`) is labelled `{a}` ({why})" for i, c, a, why in mixed)
        + ". The label comes from `decide()` on the ground-truth extraction, not from the category name."
        if mixed else "Every category's fixtures share one expected action."
    )

    failure_rows = "\n".join(
        f"| {cat} | {n} | {FAILURE_LABELS.get(cat, '(undocumented category — add one to render_report.py)')} |"
        for cat, n in sorted(agg["failure_counts"].items(), key=lambda kv: -kv[1])
    ) or "| — | 0 | no mismatches in this run |"

    examples = [r for r in results if r.get("failure_category")]
    examples = examples[:5] if len(examples) > 5 else examples
    example_blocks = []
    for r in examples:
        example_blocks.append(
            f"#### `{r['id']}` — {r['failure_category']}\n\n"
            f"- format: `{r['format']}`, category: `{r['category']}`\n"
            f"- expected action: `{r['expected_action']}` ({r['expected_reason']}) — "
            f"got: `{r['predicted_action']}` ({r['predicted_reason']})\n"
            f"- extraction field accuracy on this item: {fmt_pct(r['extraction_accuracy'])}\n"
            f"- raw email excerpt:\n\n```\n{r['raw_excerpt']}\n```\n"
        )
    if not example_blocks:
        example_blocks.append("No mismatches occurred in this run (n={}). See LIMITATIONS.md for why a "
                               "100% figure at this n should not be over-read.".format(agg["n"]))

    md = f"""# Measured results

Generated by `python evals/render_report.py evals/results/latest.json EVALS.md` \
from `evals/results/latest.json` — **never hand-edit the numbers below**; \
edit the harness (`evals/run_eval.py`, `evals/metrics.py`) and re-run instead.

- Run: `{payload.get('run_id')}` (`{payload.get('tag', '')}`), generated {date_str}
- Backend: `{payload.get('provider')}` (see `docs/adr/0003-provider-interface-and-default-backend.md`)
- n = {agg['n']} synthetic fixtures ({agg['n_ok']} completed, {agg['n_errors']} raised an error)
- Full raw per-item results: `evals/results/{results_path.name}`
- Related, separately-reported slices: **[EVALS_INJECTION_HOLDOUT.md](EVALS_INJECTION_HOLDOUT.md)**
  (measured: 0/10 held-out adversarial paraphrases caught, vs. 100% in-distribution below) and
  **[EVALS_HARD.md](EVALS_HARD.md)** (noisy/ambiguous inputs; data and harness committed, not yet measured)

## Measured numbers

| Metric | Value | n |
|---|---|---|
| Field-extraction accuracy (mean) | {fmt_pct(agg['extraction_accuracy_mean'])} | {agg['n_ok']} |
| Field-extraction accuracy (median) | {fmt_pct(agg['extraction_accuracy_median'])} | {agg['n_ok']} |
| Escalation precision (near-circular) | {fmt_pct(agg['escalation_precision'])} | tp={agg['escalation_tp']} fp={agg['escalation_fp']} |
| Escalation recall (near-circular) | {fmt_pct(agg['escalation_recall'])} | tp={agg['escalation_tp']} fn={agg['escalation_fn']} |
| Injection recall, in-distribution (near-circular; held-out paraphrases: 0/10) | {fmt_pct(agg['injection_recall'])} | n={agg['injection_n']} |
| Cost per task (mean, CLI-reported) | ${fmt_num(agg['cost_usd_mean'], 4)} | total ${fmt_num(agg['cost_usd_total'], 2)} |
| Latency per task (p50 / p95) | {fmt_num(agg['latency_s_p50'])}s / {fmt_num(agg['latency_s_p95'])}s | mean {fmt_num(agg['latency_s_mean'])}s |
| Action agreement with labels (near-circular, see below) | {fmt_pct(agg['action_accuracy'])} | {agg['n_ok']} |

**Read this as**: on {agg['n']} synthetic fixtures, with a JSON-schema-constrained
extraction call, the default backend (Claude Haiku via the local CLI,
`--effort low`) got {fmt_pct(agg['extraction_accuracy_mean'])} of individual extracted **fields** right.

**The action row is near-circular.** The expected action for each fixture is
produced by the same `decide()` / `compare_to_catalog()` code the agent runs
(`procurement_agent/tools.py`), applied to the ground-truth extraction. So
action agreement only shows that the policy gives the same answer on the
model's extraction as on the true one; it never tests whether the policy
itself is right. The escalation precision/recall rows inherit the same
limitation. This is a small, clean, synthetic benchmark, not an adversarial
or production one; see LIMITATIONS.md.

## By expected action

| expected action | n | agreed | agreement |
|---|---|---|---|
{by_action_rows}

## By generator category

| category | n | expected actions | agreed | agreement |
|---|---|---|---|---|
{by_cat_rows}

{mixed_note}

## Failure taxonomy

| category | n | what it means |
|---|---|---|
{failure_rows}

### Concrete examples

{chr(10).join(example_blocks)}

## Reproduce this

```bash
source .venv/bin/activate
python evals/run_eval.py --provider claude-cli --model haiku --effort low --concurrency 5
python evals/render_report.py evals/results/latest.json EVALS.md
```

Or offline, against the recorded cassette subset (no network/CLI/API key needed):

```bash
scripts/run_gate_locally.sh
```
"""
    out_path.write_text(md)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
