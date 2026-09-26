#!/usr/bin/env python3
"""Render a JSONL trace file (see procurement_agent/tracing.py) as a small,
dependency-free static HTML report: one row per span, grouped by task, with
timing, status, and I/O.

    python trace_viewer/render_trace.py traces/eval_run.jsonl trace_viewer/report.html
"""
from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from pathlib import Path

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Trace report — {run_ids}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; margin: 2rem;
          background: #fafafa; color: #1a1a1a; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #16171a; color: #e8e8e8; }} }}
  h1 {{ font-size: 1.3rem; }}
  .summary {{ margin-bottom: 1.5rem; color: #666; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; font-size: 0.85rem; }}
  th, td {{ border-bottom: 1px solid #ddd; padding: 4px 8px; text-align: left; vertical-align: top; }}
  th {{ background: #eee; position: sticky; top: 0; }}
  @media (prefers-color-scheme: dark) {{
    th, td {{ border-bottom: 1px solid #333; }}
    th {{ background: #222; }}
  }}
  tr.error {{ background: #fee; }}
  @media (prefers-color-scheme: dark) {{ tr.error {{ background: #3a1616; }} }}
  .task-header {{ font-weight: 600; margin-top: 1.5rem; }}
  code {{ font-size: 0.8rem; white-space: pre-wrap; word-break: break-word; }}
  .muted {{ color: #888; }}
</style>
</head>
<body>
<h1>Trace report</h1>
<div class="summary">{summary}</div>
{body}
</body>
</html>
"""


def load_spans(path: Path) -> list[dict]:
    spans = []
    for line in path.read_text().splitlines():
        if line.strip():
            spans.append(json.loads(line))
    return spans


def render(spans: list[dict]) -> str:
    by_task: dict[str, list[dict]] = defaultdict(list)
    for s in spans:
        by_task[s["task_id"]].append(s)

    run_ids = sorted({s["run_id"] for s in spans})
    total_cost = sum((s.get("usage") or {}).get("cost_usd") or 0.0 for s in spans)
    n_errors = sum(1 for s in spans if s.get("status") == "error")
    summary = (
        f"{len(spans)} spans across {len(by_task)} tasks, {n_errors} error(s), "
        f"total reported cost ${total_cost:.4f}"
    )

    parts = []
    for task_id in sorted(by_task):
        task_spans = sorted(by_task[task_id], key=lambda s: s["ts"])
        parts.append(f'<div class="task-header">{html.escape(task_id)}</div>')
        parts.append("<table>")
        parts.append(
            "<tr><th>tool</th><th>status</th><th>duration_ms</th><th>usage</th>"
            "<th>input</th><th>output</th><th>error</th></tr>"
        )
        for s in task_spans:
            row_class = "error" if s.get("status") == "error" else ""
            usage = s.get("usage") or {}
            usage_str = (
                f"{usage.get('model', '')} cost=${usage.get('cost_usd', 0):.4f} "
                f"lat={usage.get('latency_s', 0):.2f}s"
                if usage else ""
            )
            parts.append(
                f'<tr class="{row_class}">'
                f"<td>{html.escape(s['tool'])}</td>"
                f"<td>{html.escape(s.get('status', ''))}</td>"
                f"<td>{s.get('duration_ms', 0):.1f}</td>"
                f"<td>{html.escape(usage_str)}</td>"
                f"<td><code>{html.escape(json.dumps(s.get('input', {}))[:400])}</code></td>"
                f"<td><code>{html.escape(json.dumps(s.get('output', {}))[:400])}</code></td>"
                f"<td>{html.escape(s.get('error') or '')}</td>"
                f"</tr>"
            )
        parts.append("</table>")

    return TEMPLATE.format(run_ids=", ".join(run_ids) or "n/a", summary=summary, body="\n".join(parts))


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <trace.jsonl> <out.html>", file=sys.stderr)
        raise SystemExit(2)
    trace_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    spans = load_spans(trace_path)
    out_path.write_text(render(spans))
    print(f"wrote {out_path} ({len(spans)} spans)")


if __name__ == "__main__":
    main()
