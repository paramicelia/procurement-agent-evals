from __future__ import annotations

from procurement_agent import orchestrator
from procurement_agent.store import Store
from tests.conftest import ScriptedProvider, quote_response


def test_request_clarification_path_never_creates_approval(store):
    provider = ScriptedProvider([
        quote_response(items=[{"description": "M6 Hex Bolt", "qty": 100, "unit_price": None}],
                       total=None, missing_fields=["unit_price for 'M6 Hex Bolt'"]),
        "Could you confirm the unit price for the M6 Hex Bolt line?",  # draft_followup's own call
    ])
    result = orchestrator.run_task("c1", "quote with a missing price", store, provider)
    assert result.state == "clarification_drafted"
    row = store.get_run("c1")
    assert row["followup_text"]
    assert store.get_approval_for_task("c1", "place_order") is None
    assert store.count_orders_for_task("c1") == 0


def test_full_happy_path_on_disk_store(tmp_path):
    db_path = str(tmp_path / "runs.db")
    provider = ScriptedProvider([quote_response()])
    store = Store(db_path)
    try:
        r = orchestrator.run_task("d1", "clean quote", store, provider)
        assert r.state == "awaiting_approval"
        approval = store.get_approval_for_task("d1", "place_order")
        store.decide_approval(approval["id"], approve=True)
        r2 = orchestrator.resume_after_approval("d1", store)
        assert r2.state == "order_placed"
    finally:
        store.close()

    # Re-open the same on-disk DB (simulating a fresh process) and confirm
    # the completed run and the order are still there.
    store2 = Store(db_path)
    try:
        row = store2.get_run("d1")
        assert row["state"] == "order_placed"
        assert store2.count_orders_for_task("d1") == 1
    finally:
        store2.close()
