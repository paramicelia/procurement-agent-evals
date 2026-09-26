"""The human approving a place_order must see what they are approving:
supplier, items, qty, unit price, line totals, total, policy flags, the
idempotency key and the raw email. And what gets written must be what was
approved."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from procurement_agent import cli, orchestrator
from procurement_agent.models import ExtractedQuote
from procurement_agent.store import Store
from tests.conftest import ScriptedProvider, quote_response

RAW = "From: sales@acme.example\nPlease find our quote: 100 x M6 Hex Bolt at 0.11 EUR. Total 11.00 EUR."


def _pending_payload(store: Store, task_id: str) -> dict:
    approval = store.get_approval_for_task(task_id, "place_order")
    assert approval is not None and approval["status"] == "pending"
    return json.loads(approval["payload_json"])


def test_approval_payload_carries_what_the_human_approves(store):
    provider = ScriptedProvider([quote_response()])
    result = orchestrator.run_task("p1", RAW, store, provider)
    assert result.state == "awaiting_approval"

    p = _pending_payload(store, "p1")
    extracted = ExtractedQuote.model_validate_json(store.get_run("p1")["extracted_json"])
    assert p["idempotency_key"] == orchestrator.idempotency_key("p1", extracted)
    assert p["supplier"] == "Acme"
    assert p["currency"] == "EUR"
    assert p["stated_total"] == 11.0
    assert p["computed_total"] == pytest.approx(11.0)
    assert p["decision_reason"] == "within_tolerance"
    assert len(p["lines"]) == 1
    line = p["lines"][0]
    assert line["description"] == "M6 Hex Bolt"
    assert line["qty"] == 100
    assert line["unit_price"] == 0.11
    assert line["line_total"] == pytest.approx(11.0)
    assert line["matched_sku"] is not None
    assert line["deviation_pct"] is not None
    assert "max_deviation_pct" in p
    assert p["flags"] == {
        "injection_regex_hit": False,
        "conflicting_total": False,
        "unmatched_items": [],
        "missing_fields": [],
    }
    assert p["raw_email_excerpt"] == RAW
    assert p["raw_email_truncated"] is False


def test_raw_excerpt_is_truncated_for_long_emails(store):
    raw = RAW + "\n" + ("filler line\n" * 200)
    orchestrator.run_task("p2", raw, store, ScriptedProvider([quote_response()]))
    p = _pending_payload(store, "p2")
    assert len(p["raw_email_excerpt"]) == orchestrator.RAW_EXCERPT_CHARS
    assert p["raw_email_truncated"] is True


def test_pending_cli_prints_the_approval_context(tmp_path, capsys):
    db = str(tmp_path / "runs.db")
    store = Store(db)
    try:
        orchestrator.run_task("p3", RAW, store, ScriptedProvider([quote_response()]))
        key = _pending_payload(store, "p3")["idempotency_key"]
    finally:
        store.close()

    cli.main(["pending", "--db", db])
    out = capsys.readouterr().out
    for expected in ["task=p3", "supplier: Acme", "M6 Hex Bolt", "qty 100 x 0.11 EUR = 11 EUR",
                     "total: stated 11 EUR", "within_tolerance", "injection_regex_hit=False",
                     key, "raw email excerpt", "sales@acme.example"]:
        assert expected in out, expected


def test_write_refused_if_extraction_changed_after_approval(store):
    orchestrator.run_task("p4", RAW, store, ScriptedProvider([quote_response()]))
    approval = store.get_approval_for_task("p4", "place_order")
    store.decide_approval(approval["id"], approve=True)

    # Tamper with the checkpointed extraction after the human approved.
    tampered = ExtractedQuote.model_validate_json(store.get_run("p4")["extracted_json"])
    tampered.items[0].qty = 10_000
    store.upsert_run("p4", extracted_json=tampered.model_dump_json())

    with pytest.raises(orchestrator.ApprovalPayloadMismatchError):
        orchestrator.resume_after_approval("p4", store)
    assert store.count_orders_for_task("p4") == 0


def test_missed_holdout_injections_are_visible_to_the_approver(store):
    """The regex misses all 10 held-out injections (EVALS_INJECTION_HOLDOUT.md)
    and they reach auto_accept. Check that each injected sentence is then in
    the approval request the human reads, not hidden from them."""
    root = Path(__file__).resolve().parent.parent / "data_holdout_injection"
    labels = sorted((root / "labels").glob("*.json"))
    assert len(labels) == 10
    for label_path in labels:
        label = json.loads(label_path.read_text())
        raw = (root / "emails" / f"{label['id']}.txt").read_text()
        provider = ScriptedProvider([label["ground_truth"]["extracted"]])
        result = orchestrator.run_task(label["id"], raw, store, provider)
        assert result.state == "awaiting_approval", label["id"]
        p = _pending_payload(store, label["id"])
        assert p["flags"]["injection_regex_hit"] is False
        assert label["injection_phrase"] in p["raw_email_excerpt"], label["id"]
