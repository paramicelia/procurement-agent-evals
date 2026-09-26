.PHONY: venv install data test lint eval report trace gate demo clean

VENV := .venv/bin

venv:
	python3 -m venv .venv

install: venv
	$(VENV)/pip install --quiet --disable-pip-version-check -r requirements.txt

data:
	$(VENV)/python data_gen/generate_data.py --out-dir data --seed 1337

test:
	$(VENV)/python -m pytest tests/ -q

lint:
	$(VENV)/python -m ruff check procurement_agent evals tests data_gen scripts

eval:
	$(VENV)/python evals/run_eval.py --provider claude-cli --model haiku --effort low --concurrency 5
	$(VENV)/python evals/render_report.py evals/results/latest.json EVALS.md

eval-cassette:
	$(VENV)/python evals/run_eval.py --provider cassette --cassette evals/cassettes/ci_subset.jsonl \
	  --ids q000_auto_accept q001_auto_accept q020_clarify_missing_field q021_clarify_missing_field \
	        q030_clarify_price_band q031_clarify_price_band q035_escalate_price q036_escalate_price \
	        q043_escalate_conflict q044_escalate_conflict q049_escalate_unknown_item q050_escalate_unknown_item \
	        q055_escalate_injection q056_escalate_injection \
	  --out evals/ci --tag ci_subset

trace:
	$(VENV)/python trace_viewer/render_trace.py traces/eval_run.jsonl trace_viewer/report.html

gate:
	bash scripts/run_gate_locally.sh

demo:
	# NOTE: only auto_accept/escalate_* ids here — the cassette only recorded
	# extract_quote calls, and a clarify_* fixture would also call
	# draft_followup (a different prompt) that isn't in the cassette. See
	# scripts/record_cassette.py to record more if you extend this demo.
	rm -f /tmp/procurement_demo.db
	$(VENV)/python -m procurement_agent.cli run-all --data-dir data/emails --db /tmp/procurement_demo.db \
	  --provider cassette --cassette evals/cassettes/ci_subset.jsonl \
	  --ids q000_auto_accept q001_auto_accept q043_escalate_conflict \
	        q049_escalate_unknown_item q055_escalate_injection
	$(VENV)/python -m procurement_agent.cli pending --db /tmp/procurement_demo.db

clean:
	rm -f /tmp/procurement_demo.db
