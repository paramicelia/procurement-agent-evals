# ADR 0001: explicit state machine instead of LangGraph

Status: proposed, pending Danilo's review. **Provenance**: Claude Code
(operating autonomously against a written brief) chose this option and wrote
this ADR. Danilo has not yet reviewed or defended this decision in his own
words — see the README's "how this was built" section. Until he has, treat
this as "an agent's reasoning," not "Danilo's architectural decision."

## Context

The brief asked me to justify LangGraph vs. a small explicit state machine
for the procurement agent (extract → compare → decide → [approve] →
place_order, or the clarification/escalation branches).

## Decision

I went with a hand-rolled state machine (`procurement_agent/orchestrator.py`):
a `run_task()` function that reads the current state from SQLite, executes
exactly the next step, and persists before returning. `resume_after_approval()`
does the same for the branch that waits on a human.

## Why, concretely

- **The whole graph is five states.** LangGraph earns its keep on graphs with
  many nodes, branches, and especially cycles/subgraphs. This one is a
  straight line with one fork (auto_accept vs. clarify vs. escalate) and one
  human-in-the-loop pause. A `if/elif` chain over a `state` column is not a
  simplification I made to cut corners; it is the more legible implementation
  for a graph this size.
- **I wanted the crash-resume and approval-interrupt invariants provable in
  plain pytest**, by inspecting a SQLite table directly, without needing to
  know LangGraph's checkpointing internals or its interrupt API. See
  `tests/test_invariants.py::test_resume_after_crash_does_not_redo_completed_steps`
  and `::test_no_write_without_approval` — both assert against the DB row,
  not against orchestrator internals.
- **One dependency fewer.** This project already depends on an LLM backend,
  Pydantic, and SQLite (stdlib). LangGraph would add a framework whose
  checkpointing/interrupt semantics I would then need to explain and defend
  in an interview, on a project whose actual complexity does not need them.
- **Honesty constraint from the brief**: no stack claims beyond what was
  actually exercised. A LangGraph agent built in one sitting, on a graph
  this simple, would be closer to "I used the LangGraph tutorial" than to
  "I designed an agent architecture" — the opposite of what this repo is
  trying to demonstrate.

## What I gave up

- LangGraph's built-in checkpointer/interrupt primitives, streaming,
  and (if I used LangGraph Cloud) a hosted run visualizer.
- If this agent grows real branching (e.g. multiple approval types, parallel
  tool calls, sub-agents), the hand-rolled version would need to grow into
  something LangGraph already is. I would revisit this decision at that point,
  not before — see "what I'd do in production" in the README.

## Consequence

`orchestrator.py` stays small enough to read start to finish in a few
minutes, and the four required invariants (no write without approval,
idempotent retries, injection never triggers a write, crash-resume) are each
one direct pytest test against the SQLite state, not a mock of a framework.
