# ADR 0002: the approval gate lives in the store, not in the tool

Status: proposed, pending Danilo's review. **Provenance**: written by Claude
Code; see the note in ADR 0001 — this documents an agent's reasoning, not
yet a decision Danilo has independently defended.

## Context

The brief requires: every write goes through an approval queue/interrupt;
idempotency keys so retries never double-order; and an invariant test proving
prompt-injection text never triggers a write.

## Decision

`Store.place_order()` (`procurement_agent/store.py`) is the only function in
the codebase that inserts into the `orders` table, and the first thing it
does is re-check for an approved `place_order` approval row for that task.
`tools.place_order()` and `orchestrator.resume_after_approval()` call
through to it; neither of them contains the check itself.

Idempotency: the key is `sha256(task_id + extracted_quote_json)[:24]`
(`orchestrator.idempotency_key`). `place_order` looks up that key in `orders`
before inserting; on a re-run with the same key it returns the existing
`order_id` instead of inserting again, and a race between two concurrent
callers is resolved by the table's `PRIMARY KEY` constraint plus a catch on
`sqlite3.IntegrityError` that re-reads the winner's row.

## Why the check lives in the store, not the tool

If `place_order()` (a plain function) held the only copy of the "is this
approved?" check, a future code path that also wants to write an order
could accidentally call `store._conn.execute(...)` directly, or a
prompt-injected instruction that got an LLM to call some *other* function
could bypass a check that only `place_order` enforces. Putting the check
inside `Store`, next to the only `INSERT INTO orders`, means there is
structurally nothing else that can write an order — not "we always
remember to check," but "there is only one door, and it has a lock."

This is also why `tools.injection_detected()` (a regex safety net) is
explicitly documented as a *measured, imperfect* detector, not the safety
guarantee (see `tools.py`'s docstring on it, and EVALS.md's injection-recall
number). The guarantee that matters — no write without a human approval —
does not depend on catching the injection at all. An injection that fools
both the extraction LLM and the regex still cannot place an order, because
nothing has approved it. `tests/test_invariants.py` proves both: the regex
path (`test_injection_never_reaches_a_write`) and the structural path
(`test_no_write_without_approval`, which calls `place_order` directly with
no approval and asserts it raises).

## Consequence

- Every write is one extra SQLite round-trip slower (a `SELECT` before the
  `INSERT`). Irrelevant at this scale.
- A human reviewing pending approvals only ever sees `place_order` requests;
  `request_clarification` and `escalate_human` never create one, so the
  approval queue does not get noisy with things nobody needs to approve.
- If a second write-tool is ever added (e.g. `cancel_order`), it must go
  through the same `Store`-owned gate — that's the pattern to extend, not
  re-derive.

## Addendum (code review, 2026-09-26): the approval-creation checkpoint was not atomic

The original version of this design checked out under every test that
existed at the time, but missed one boundary: `orchestrator.run_task()`
calls `store.create_approval()` and then checkpoints the run to
`awaiting_approval` in a *separate* commit. A crash between those two
commits left the run row at `decided`, so a restart re-entered the same
branch and called `create_approval()` again — creating a second
`place_order` approval row with the same idempotency key, silently, with no
error. `Store.get_approval_for_task` always returns the newest row, so a
human approving the older (now-orphaned) one would never see their approval
take effect. Fix: `create_approval` is now idempotent per `(task_id, kind)` —
it returns the existing row instead of inserting a duplicate. See
`tests/test_invariants.py::test_crash_between_create_approval_and_checkpoint_does_not_duplicate`
and `LIMITATIONS.md`'s "About crash-resume" section for the analogous,
deliberately-not-fixed case around `draft_followup`.
