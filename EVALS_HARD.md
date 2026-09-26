# Harder eval slice — status: data + harness ready, live run pending

Added after code review noted that EVALS.md's clean 100% / 98.6% numbers had
no example anywhere of the model getting something wrong for a real reason.
This file exists to hold that harder, separately-reported slice — it is
**not yet a measured result**, and this section says exactly why, rather
than leaving the gap unexplained or filling it with a plausible-looking
number.

## What's built and committed

- `data_hard/` — 12 fixtures (6 `noisy_ocr`, 6 `ambiguous_currency`),
  generated deterministically by
  `python data_gen/generate_hard_data.py --out-dir data_hard --seed 9001`.
  Both categories are real stress tests of `extract_quote`, not generator
  artifacts like the currency-omission one already disclosed in
  LIMITATIONS.md:
  - `noisy_ocr`: a clean, catalog-matching quote whose rendered text has
    deterministic OCR-style character noise applied (digit/letter
    lookalikes, dropped spaces) — see `data_hard/emails/h*_noisy_ocr.txt`.
  - `ambiguous_currency`: a clean quote where one line item's price is
    labeled with a currency code different from the quote's real one, while
    the printed number is still the real-currency price — a realistic
    supplier mislabel with no way to resolve it from the text alone. Ground
    truth is anchored to the quote's real currency (the total's and every
    other line's). Example: `data_hard/emails/h006_ambiguous_currency.txt`.
- `evals/run_eval.py --data-dir data_hard ...` runs unmodified — this is a
  data-generation extension of the existing harness, not a new one.

## Why there are no numbers yet

The one live attempt so far (`evals/results_hard/2026-09-26_140748_hard_slice.json`,
14:07 CEST) failed on all 12 fixtures with the same error from the default
backend, the local Claude Code CLI on the machine owner's own account:

```
claude CLI error result: "You've hit your session limit · resets 3:40pm (Europe/Madrid)"
```

That reset is in the future relative to this fix round, so this is reported
as an open item rather than blocked on indefinitely or worked around with a
fabricated or partial number. **Do not fill in numbers here by hand** —
run the commands below once the session limit has reset and let
`evals/render_report.py` generate them, the same way EVALS.md's numbers are
generated.

**Pitfall found while building this**: `evals/run_eval.py --out DIR` always
also writes `DIR/latest.json`, regardless of `--tag`. The command below
therefore points `--out` at a *separate* `evals/results_hard/` directory, not
`evals/results/` — pointing it at the same directory as the main n=61 run
would silently overwrite `evals/results/latest.json`, the file EVALS.md's
100%/98.6% headline numbers are generated from. (This is exactly what
happened once while building this file; it was caught by `git diff` showing
EVALS.md's real numbers replaced with `n/a`, and fixed by restoring
`evals/results/latest.json` from the untouched timestamped copy next to it
and re-rendering. No fabricated numbers were left in EVALS.md at any point.)

## Reproduce this (once the CLI session limit has reset)

```bash
source .venv/bin/activate
python data_gen/generate_hard_data.py --out-dir data_hard --seed 9001   # already committed; re-run only if you want to regenerate
python evals/run_eval.py --data-dir data_hard --provider claude-cli --model haiku --effort low \
  --concurrency 4 --out evals/results_hard --trace traces/hard_slice_eval.jsonl --tag hard_slice
python evals/render_report.py evals/results_hard/<the new timestamp>_hard_slice.json EVALS_HARD.md
```

The second command overwrites this file's content below this line with the
same generated structure EVALS.md uses (headline table, by-category
breakdown, failure taxonomy with concrete examples) — at which point this
"status" section should be trimmed down to a one-line note pointing at the
run, not deleted outright, so the honesty trail (what was pending, and when
it got measured) stays visible.

## A caveat for whoever runs this next

`ambiguous_currency` ground truth assumes a careful human reader would
resolve the mislabeled line to the quote's dominant currency (the one the
total and every other line use). That is a judgment call, not a validated
one — read it the same way LIMITATIONS.md already asks you to read the
8%/20% escalation thresholds: an agent's reasonable-seeming policy choice,
not a domain expert's sign-off.
