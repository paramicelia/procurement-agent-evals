# Limitations — read this before the numbers in EVALS.md

This project is a demo built on synthetic data in a single agent-assisted
session. It is not production software and the numbers in `EVALS.md` are not
a general claim about "how good this agent is." Specifically:

## About the eval

- **n=61, one seed, one model, one run.** No confidence intervals, no
  repeated runs, no held-out set separate from the one the thresholds were
  tuned against. A single run's 100% action accuracy at this n is consistent
  with a genuinely easy, well-specified extraction task (JSON schema,
  templated formats) — it is not evidence the agent "never makes mistakes."
  Treat it as a floor for a clean benchmark, not a ceiling for a hard one.
- **Labels are generator-deterministic, not human-reviewed.** `decide()` and
  `compare_to_catalog()` are shared code between the generator and the live
  agent (see `docs/adr/0002...`), so an eval mismatch can only come from the
  extraction step — but nobody with a procurement background has checked
  whether the *policy itself* (8%/20% deviation thresholds, the specific
  regex injection patterns) is actually sound. It encodes my/the agent's
  judgment call, not a domain expert's.
- **One model family, one provider path.** Every number is Claude Haiku via
  the local CLI (`--effort low`). No comparison against another model, no
  adversarial red-teaming of the injection detector beyond the ~10 patterns
  baked into the synthetic data.
- **A generator artifact, disclosed rather than hidden**: 5 of 61 fixtures
  don't literally state a currency anywhere in the rendered text (when both
  the unit price and the total are missing, the template never has a reason
  to print a currency code). The ground-truth label still names a currency
  (the generator "knows" it), so the model correctly returning `null` there
  is scored as a field mismatch even though refusing to guess is the right
  behavior. See EVALS.md's failure taxonomy — I chose to label and explain
  this rather than quietly exclude those 5 items or patch the labels after
  seeing the model's answers.
- **Minor rendering rough edge**: the `table`/`pdf_extract` renderers
  sometimes print the literal text `None` instead of a placeholder like
  `TBD` for an intentionally-omitted qty/price. Cosmetic — it doesn't change
  any label or metric — but it's a rough edge in `data_gen/generate_data.py`
  I noticed after generating the committed data and did not re-roll for
  (re-rolling would have meant re-running the paid eval a third time; not
  worth it for a display string).

## About the agent

- **The escalation policy is a guess, not a benchmarked threshold.** 8%
  above catalog price → ask; 20% → escalate. Nobody validated these numbers
  against real procurement outcomes.
- **The catalog matcher is `difflib` on 15 SKUs.** It will not scale to a
  real catalog of thousands of items with genuine near-duplicates; a
  production version needs embeddings or a real product-matching service.
- **`draft_followup` is a draft, full stop.** There is no send capability
  anywhere in this repo, by design, not as an oversight.
- **Single-currency-per-quote assumption.** The comparator does not handle
  an email that quotes different line items in different currencies.

## About the write-safety claims specifically

- The **structural** guarantee — no code path reaches `orders` without a
  prior approved approval row — is proven by `tests/test_invariants.py`
  against the real SQLite table, not asserted from documentation.
- The **detection** numbers (injection recall, escalation precision/recall)
  are *measured and imperfect*, from `EVALS.md`, over a benchmark I built
  myself. A real adversary optimizing against my specific regex list would
  likely beat it. The structural guarantee is what should be trusted; the
  detection numbers are a secondary, weaker claim.

## Provenance

This repository, including this file, was built by Claude Code (Anthropic's
coding agent) operating autonomously against a written brief, in one session
on 2026-09-26. **Danilo has not yet reviewed the code line by line, defended
the ADRs in his own words, or independently re-run the eval.** Do not present
any design decision here as "Danilo decided X" until that review has actually
happened — see the provenance note repeated in each ADR and in README.md.
