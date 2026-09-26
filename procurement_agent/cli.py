"""Small CLI for driving the agent by hand: run tasks, list what's pending
human approval, and approve/reject/resume them.

    python -m procurement_agent.cli run-all --data-dir data/emails --db runs.db
    python -m procurement_agent.cli pending --db runs.db
    python -m procurement_agent.cli approve TASK_ID --db runs.db
    python -m procurement_agent.cli reject TASK_ID --db runs.db
"""
from __future__ import annotations

import argparse
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


def cmd_pending(args: argparse.Namespace) -> None:
    store = Store(args.db)
    for row in store.pending_approvals():
        print(f"{row['id']}\ttask={row['task_id']}\tkind={row['kind']}\tcreated={row['created_at']}")
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
