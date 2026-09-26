"""The explicit state machine that drives one task through the tools.

See docs/adr/0001 for why this is a small hand-rolled state machine instead
of LangGraph. Every step persists its result to SQLite (Store) BEFORE the
function returns or the next step starts, so run_task() is safe to call
again after a crash: it resumes from the last completed step instead of
re-running everything (in particular, extract_quote — the paid LLM call —
is never re-run once its result is checkpointed).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import Action, ComparisonResult, ExtractedQuote
from .providers.base import LLMProvider
from .store import Store
from .tools import compare_to_catalog, decide, draft_followup, extract_quote, place_order
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
            key = idempotency_key(task_id, extracted)
            store.create_approval(task_id, "place_order", {"idempotency_key": key})
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
        with tracer.span(task_id, "place_order") as span:
            order_id = place_order(
                task_id=task_id, extracted=extracted, idempotency_key=payload["idempotency_key"], store=store
            )
            span.set_io(input={"idempotency_key": payload["idempotency_key"]}, output={"order_id": order_id})
        store.upsert_run(task_id, state=STATE_ORDER_PLACED, order_id=order_id)
        return StepResult(task_id, STATE_ORDER_PLACED)

    store.upsert_run(task_id, state=STATE_REJECTED)
    return StepResult(task_id, STATE_REJECTED)
