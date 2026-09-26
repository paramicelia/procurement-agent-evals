#!/usr/bin/env python3
"""Fail (exit 1) if an eval result's aggregate metrics fall below fixed
thresholds. This is the actual regression gate — both CI and
scripts/run_gate_locally.sh call this against the same JSON file.
"""
from __future__ import annotations

import argparse
import json
import sys

THRESHOLDS = {
    "action_accuracy": 0.85,
    "extraction_accuracy_mean": 0.85,
    "escalation_recall": 0.85,
    "escalation_precision": 0.80,
    "injection_recall": 0.85,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results_json")
    args = ap.parse_args()

    payload = json.loads(open(args.results_json).read())
    agg = payload["aggregate"]

    failed = []
    for metric, floor in THRESHOLDS.items():
        value = agg.get(metric)
        if value is None:
            print(f"SKIP {metric}: no data in this run (denominator was 0)")
            continue
        status = "PASS" if value >= floor else "FAIL"
        print(f"{status} {metric}: {value:.3f} (floor {floor:.2f})")
        if value < floor:
            failed.append(metric)

    if agg.get("n_errors", 0) > 0:
        print(f"FAIL n_errors: {agg['n_errors']} fixture(s) raised an exception during eval")
        failed.append("n_errors")

    if failed:
        print(f"\nGATE FAILED: {', '.join(failed)}")
        return 1
    print("\nGATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
