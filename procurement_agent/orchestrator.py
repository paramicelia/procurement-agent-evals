"""The explicit state machine that drives one task through the tools.

See docs/adr/0001 for why this is a small hand-rolled state machine instead
of LangGraph. Every step persists its result to SQLite (Store) BEFORE the
function returns or the next step starts, so run_task() is safe to call
again after a crash: it resumes from the last completed step instead of
re-running everything (in particular, extract_quote — the paid LLM call —
is never re-run once its result is checkpointed).

Caveat, found by code review and now covered by a test either way (see
tests/test_invariants.py): the AUTO_ACCEPT branch below calls
store.create_approval() and *then* checkpoints the state change in a
separate commit. A crash between those two commits used to create a
duplicate approval row on resume; create_approval() is now idempotent per
(task_id, kind), so that boundary is safe. The REQUEST_CLARIFICATION branch
has the same two-commits shape around draft_followup() (a paid LLM call)
and is NOT fixed the same way: a crash there will re-run draft_followup() on
resume. That does not corrupt any state or duplicate a row — it only pays
for the LLM call again — so it is documented as a known limitation (see
LIMITATIONS.md) rather than fixed here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import Action, ComparisonResult, ExtractedQuote
from .providers.base import LLMProvider
from .store import Store
from .tools import (
    compare_to_catalog,
    decide,
    draft_followup,
    extract_quote,
    injection_detected,
    place_order,
)
from .tracing import Tracer

STATE_INIT = "init"
STATE_EXTRACTED = "extracted"
STATE_COMPARED = "compared"
STATE_DECIDED = "decided"
STATE_AWAITING_APPROVAL = "awaiting_approval"
STATE_CLARIFICATION_DRAFTED = "clarification_drafted"
STATE_ESCALATED = "escalated"
STATE_ORDER_PLACED = "order_placed"
STATE_REJECTED = "rejected"

TERMINAL_STATES = {
    STATE_CLARIFICATION_DRAFTED,
    STATE_ESCALATED,
    STATE_ORDER_PLACED,
    STATE_REJECTED,
}


def idempotency_key(task_id: str, extracted: ExtractedQuote) -> str:
    fingerprint = extracted.model_dump_json()
    return hashlib.sha256(f"{task_id}:{fingerprint}".encode()).hexdigest()[:24]


RAW_EXCERPT_CHARS = 2000


class ApprovalPayloadMismatchError(RuntimeError):
    """The approved payload no longer matches the run's extraction, so the
    human approved something other than what would be written."""


def build_approval_payload(
    task_id: str,
    raw_text: str,
    extracted: ExtractedQuote,
    comparison: ComparisonResult,
    decision_reason: str | None,
) -> dict:
    """What the human approver sees before a place_order write is allowed.

    Everything the order would contain (supplier, each line's item, qty,
    unit price, line total, the total), how it compares to the catalog,
    the flags the policy looked at, and an excerpt of the raw email. The
    injection flag is the regex detector only, which misses paraphrased
    attacks (see EVALS_INJECTION_HOLDOUT.md), so the raw excerpt is
    included for the human to read rather than trusting that flag.
    """
    lines = []
    computable = bool(extracted.items)
    computed_total = 0.0
    for idx, item in enumerate(extracted.items):
        match = next((m for m in comparison.matches if m.line_index == idx), None)
        line_total = None
        if item.qty is not None and item.unit_price is not None:
            line_total = round(item.qty * item.unit_price, 4)
            computed_total += line_total
        else:
            computable = False
        lines.append(
            {
                "description": item.description,
                "qty": item.qty,
                "unit_price": item.unit_price,
                "line_total": line_total,
                "matched_sku": match.matched_sku if match else None,
                "catalog_unit_price_eur": match.catalog_unit_price_eur if match else None,
                "quoted_unit_price_eur": match.quoted_unit_price_eur if match else None,
                "deviation_pct": round(match.deviation_pct, 2) if match and match.deviation_pct is not None else None,
            }
        )
    raw = raw_text or ""
    return {
        "idempotency_key": idempotency_key(task_id, extracted),
        "task_id": task_id,
        "supplier": extracted.supplier,
        "currency": extracted.currency,
        "lines": lines,
        "stated_total": extracted.total,
        "computed_total": round(computed_total, 4) if computable else None,
        "max_deviation_pct": comparison.max_deviation_pct,
        "decision_reason": decision_reason,
        "flags": {
            "injection_regex_hit": injection_detected(raw),
            "conflicting_total": comparison.conflicting,
            "unmatched_items": list(comparison.unmatched_items),
            "missing_fields": list(extracted.missing_fields),
        },
        "raw_email_excerpt": raw[:RAW_EXCERPT_CHARS],
        "raw_email_truncated": len(raw) > RAW_EXCERPT_CHARS,
    }


@dataclass
class StepResult:
    task_id: str
    state: str
    halted_for_approval: bool = False


def run_task(
    task_id: str,
    raw_text: str,
    store: Store,
    provider: LLMProvider,
    tracer: Tracer | None = None,
) -> StepResult:
    """Advance one task as far as it can go without a human decision."""
    tracer = tracer or Tracer.null()
    row = store.get_run(task_id)
    if row is None:
        store.upsert_run(task_id, raw_text=raw_text, state=STATE_INIT)
        row = store.get_run(task_id)

    state = row["state"]
    if state in TERMINAL_STATES or state == STATE_AWAITING_APPROVAL:
        return StepResult(task_id, state, halted_for_approval=(state == STATE_AWAITING_APPROVAL))

    if state == STATE_INIT:
        with tracer.span(task_id, "extract_quote") as span:
            extracted, resp = extract_quote(raw_text, provider)
            span.set_io(input={"raw_text_chars": len(raw_text)}, output=extracted.model_dump())
            span.set_usage(resp)
        store.upsert_run(task_id, state=STATE_EXTRACTED, extracted_json=extracted.model_dump_json())
        row = store.get_run(task_id)
        state = STATE_EXTRACTED

    if state == STATE_EXTRACTED:
        extracted = ExtractedQuote.model_validate_json(row["extracted_json"])
        with tracer.span(task_id, "compare_to_catalog") as span:
            comparison = compare_to_catalog(extracted)
            span.set_io(input=extracted.model_dump(), output=comparison.model_dump())
        store.upsert_run(task_id, state=STATE_COMPARED, comparison_json=comparison.model_dump_json())
        row = store.get_run(task_id)
        state = STATE_COMPARED

    if state == STATE_COMPARED:
        extracted = ExtractedQuote.model_validate_json(row["extracted_json"])
        comparison = ComparisonResult.model_validate_json(row["comparison_json"])
        with tracer.span(task_id, "decide") as span:
            decision = decide(extracted, comparison, raw_text)
            span.set_io(input={"raw_text_chars": len(raw_text)}, output=decision.model_dump())
        store.upsert_run(task_id, state=STATE_DECIDED, action=decision.action.value, reason=decision.reason)
        row = store.get_run(task_id)
        state = STATE_DECIDED

    if state == STATE_DECIDED:
        action = row["action"]
        extracted = ExtractedQuote.model_validate_json(row["extracted_json"])
        if action == Action.AUTO_ACCEPT.value:
            comparison = ComparisonResult.model_validate_json(row["comparison_json"])
            payload = build_approval_payload(task_id, raw_text, extracted, comparison, row["reason"])
            store.create_approval(task_id, "place_order", payload)
            store.upsert_run(task_id, state=STATE_AWAITING_APPROVAL)
            return StepResult(task_id, STATE_AWAITING_APPROVAL, halted_for_approval=True)
        if action == Action.REQUEST_CLARIFICATION.value:
            with tracer.span(task_id, "draft_followup") as span:
                text, resp = draft_followup(extracted, row["reason"], provider)
                span.set_io(input={"reason": row["reason"]}, output={"draft_chars": len(text)})
                span.set_usage(resp)
            store.upsert_run(task_id, state=STATE_CLARIFICATION_DRAFTED, followup_text=text)
            return StepResult(task_id, STATE_CLARIFICATION_DRAFTED)
        # escalate_human: no further tool calls, and in particular no write.
        store.upsert_run(task_id, state=STATE_ESCALATED)
        return StepResult(task_id, STATE_ESCALATED)

    return StepResult(task_id, state)


def resume_after_approval(task_id: str, store: Store, tracer: Tracer | None = None) -> StepResult:
    """Call once a human has approved or rejected the pending place_order
    request for this task. Idempotent: calling it again after an order was
    already placed just returns the same state without a second write."""
    tracer = tracer or Tracer.null()
    row = store.get_run(task_id)
    if row is None:
        raise RuntimeError(f"unknown task {task_id}")
    if row["state"] == STATE_ORDER_PLACED or row["state"] == STATE_REJECTED:
        return StepResult(task_id, row["state"])
    if row["state"] != STATE_AWAITING_APPROVAL:
        raise RuntimeError(f"task {task_id} is not awaiting approval (state={row['state']})")

    approval = store.get_approval_for_task(task_id, "place_order")
    if approval is None or approval["status"] == "pending":
        return StepResult(task_id, STATE_AWAITING_APPROVAL, halted_for_approval=True)

    extracted = ExtractedQuote.model_validate_json(row["extracted_json"])
    payload = json.loads(approval["payload_json"])

    if approval["status"] == "approved":
        # The key is a hash of the extraction the human saw. If the run's
        # extraction changed since then, refuse instead of writing an order
        # nobody approved.
        if payload["idempotency_key"] != idempotency_key(task_id, extracted):
            raise ApprovalPayloadMismatchError(
                f"task {task_id}: approved payload does not match the current extraction; reject this approval and re-run the task"
            )
        with tracer.span(task_id, "place_order") as span:
            order_id = place_order(
                task_id=task_id, extracted=extracted, idempotency_key=payload["idempotency_key"], store=store
            )
            span.set_io(input={"idempotency_key": payload["idempotency_key"]}, output={"order_id": order_id})
        store.upsert_run(task_id, state=STATE_ORDER_PLACED, order_id=order_id)
        return StepResult(task_id, STATE_ORDER_PLACED)

    store.upsert_run(task_id, state=STATE_REJECTED)
    return StepResult(task_id, STATE_REJECTED)
