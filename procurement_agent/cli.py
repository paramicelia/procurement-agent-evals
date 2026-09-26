"""Small CLI for driving the agent by hand: run tasks, list what's pending
human approval, and approve/reject/resume them.

    python -m procurement_agent.cli run-all --data-dir data/emails --db runs.db
    python -m procurement_agent.cli pending --db runs.db
    python -m procurement_agent.cli approve TASK_ID --db runs.db
    python -m procurement_agent.cli reject TASK_ID --db runs.db
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .orchestrator import resume_after_approval, run_task
from .providers import get_provider
from .store import Store
from .tracing import Tracer


def cmd_run_all(args: argparse.Namespace) -> None:
    store = Store(args.db)
    provider = get_provider(args.provider, model=args.model) if args.provider != "cassette" else get_provider(
        "cassette", cassette_path=args.cassette
    )
    tracer = Tracer(args.trace) if args.trace else None
    data_dir = Path(args.data_dir)
    if args.ids:
        files = [data_dir / f"{task_id}.txt" for task_id in args.ids]
    else:
        files = sorted(data_dir.glob("*.txt"))
        if args.limit:
            files = files[: args.limit]
    for f in files:
        task_id = f.stem
        raw_text = f.read_text()
        result = run_task(task_id, raw_text, store, provider, tracer)
        print(f"{task_id}: {result.state}")
    if tracer:
        tracer.close()
    store.close()


def _fmt(x) -> str:
    if x is None:
        return "?"
    if isinstance(x, float):
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    return str(x)


def format_approval(row) -> str:
    """Render one pending approval so a human can judge it before approving:
    supplier, every line (item, qty, unit price, line total, catalog
    deviation), totals, policy flags, the idempotency key and a raw-email
    excerpt."""
    p = json.loads(row["payload_json"])
    ccy = p.get("currency") or "?"
    out = [
        f"approval {row['id']}  task={row['task_id']}  kind={row['kind']}",
        f"  supplier: {p.get('supplier') or '?'}    currency: {ccy}",
    ]
    for i, line in enumerate(p.get("lines", []), 1):
        dev = line.get("deviation_pct")
        dev_s = f"{dev:+.1f}% vs catalog {line.get('matched_sku')}" if dev is not None else "no catalog price"
        out.append(
            f"  {i}. {line.get('description')}: qty {_fmt(line.get('qty'))} x {_fmt(line.get('unit_price'))} {ccy}"
            f" = {_fmt(line.get('line_total'))} {ccy}  ({dev_s})"
        )
    out.append(
        f"  total: stated {_fmt(p.get('stated_total'))} {ccy}, computed from lines {_fmt(p.get('computed_total'))} {ccy}"
    )
    out.append(f"  max catalog deviation: {_fmt(p.get('max_deviation_pct'))}%   policy reason: {p.get('decision_reason')}")
    flags = p.get("flags", {})
    out.append(
        "  flags: "
        f"injection_regex_hit={flags.get('injection_regex_hit')} "
        f"conflicting_total={flags.get('conflicting_total')} "
        f"unmatched_items={flags.get('unmatched_items') or []} "
        f"missing_fields={flags.get('missing_fields') or []}"
    )
    out.append("  note: the injection regex misses paraphrased attacks; read the email excerpt below.")
    out.append(f"  idempotency_key: {p.get('idempotency_key')}")
    excerpt = (p.get("raw_email_excerpt") or "").rstrip()
    suffix = "\n    [... truncated]" if p.get("raw_email_truncated") else ""
    out.append("  raw email excerpt:\n    " + excerpt.replace("\n", "\n    ") + suffix)
    return "\n".join(out)


def cmd_pending(args: argparse.Namespace) -> None:
    store = Store(args.db)
    rows = store.pending_approvals()
    if not rows:
        print("no pending approvals")
    for row in rows:
        print(format_approval(row))
        print()
    store.close()


def cmd_decide(args: argparse.Namespace, approve: bool) -> None:
    store = Store(args.db)
    approval = store.get_approval_for_task(args.task_id, "place_order")
    if approval is None:
        print(f"no pending place_order approval for task {args.task_id}", file=sys.stderr)
        sys.exit(1)
    store.decide_approval(approval["id"], approve=approve, decided_by=args.decided_by)
    result = resume_after_approval(args.task_id, store)
    print(f"{args.task_id}: {result.state}")
    store.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="procurement_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-all", help="run every *.txt in a data dir through the agent")
    p_run.add_argument("--data-dir", default="data/emails")
    p_run.add_argument("--db", default="runs.db")
    p_run.add_argument("--provider", default="claude-cli")
    p_run.add_argument("--model", default="haiku")
    p_run.add_argument("--cassette", default="evals/cassettes/default.jsonl")
    p_run.add_argument("--trace", default="traces/run.jsonl")
    p_run.add_argument("--limit", type=int, default=0)
    p_run.add_argument("--ids", nargs="*", default=None)
    p_run.set_defaults(func=cmd_run_all)

    p_pending = sub.add_parser("pending", help="list approvals awaiting a human decision")
    p_pending.add_argument("--db", default="runs.db")
    p_pending.set_defaults(func=cmd_pending)

    p_approve = sub.add_parser("approve", help="approve a pending place_order and let it write")
    p_approve.add_argument("task_id")
    p_approve.add_argument("--db", default="runs.db")
    p_approve.add_argument("--decided-by", default="human-sim")
    p_approve.set_defaults(func=lambda a: cmd_decide(a, approve=True))

    p_reject = sub.add_parser("reject", help="reject a pending place_order (no write happens)")
    p_reject.add_argument("task_id")
    p_reject.add_argument("--db", default="runs.db")
    p_reject.add_argument("--decided-by", default="human-sim")
    p_reject.set_defaults(func=lambda a: cmd_decide(a, approve=False))

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
