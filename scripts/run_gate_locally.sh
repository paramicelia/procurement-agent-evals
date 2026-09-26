#!/usr/bin/env bash
# Runs exactly what .github/workflows/gate.yml runs, so the gate can be
# proven locally without needing GitHub Actions or `act`.
#
#   scripts/run_gate_locally.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

# Use $PYTHON if set, else the repo's .venv, else python3 on PATH, so the
# gate works without activating the venv first.
if [ -z "${PYTHON:-}" ]; then
  if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi
echo "using $PYTHON"

echo "== ruff =="
"$PYTHON" -m ruff check procurement_agent evals tests data_gen scripts

echo
echo "== pytest (unit + invariant tests; no network) =="
"$PYTHON" -m pytest tests/ -q

echo
echo "== recorded-response ('cassette') eval subset — no network/CLI needed =="
IDS="q000_auto_accept q001_auto_accept q020_clarify_missing_field q021_clarify_missing_field \
q030_clarify_price_band q031_clarify_price_band q035_escalate_price q036_escalate_price \
q043_escalate_conflict q044_escalate_conflict q049_escalate_unknown_item q050_escalate_unknown_item \
q055_escalate_injection q056_escalate_injection"

# Writes to traces/ci_gate_run.jsonl (gitignored, rewritten every run), not
# the committed traces/ci_gate.jsonl example — see that file's own note.
rm -f traces/ci_gate_run.jsonl
"$PYTHON" evals/run_eval.py \
  --provider cassette \
  --cassette evals/cassettes/ci_subset.jsonl \
  --ids $IDS \
  --out evals/ci \
  --trace traces/ci_gate_run.jsonl \
  --tag ci_subset

echo
echo "== threshold gate =="
"$PYTHON" scripts/check_thresholds.py evals/ci/latest.json

echo
echo "GATE OK"
