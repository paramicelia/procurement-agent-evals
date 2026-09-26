#!/usr/bin/env python3
"""A harder eval slice, generated separately from the clean n=61 set in data/.

    python data_gen/generate_hard_data.py --out-dir data_hard --seed 9001

Added after code review flagged that EVALS.md's clean 100%/98.6% numbers had
no example anywhere of the model getting something wrong for a real reason.
Two categories, both real stress tests of extract_quote (the LLM step), not
generator artifacts like the currency one already disclosed in LIMITATIONS.md:

- noisy_ocr: a clean auto_accept quote whose RENDERED TEXT has been run
  through a deterministic OCR-style noise function (digit/letter lookalike
  substitutions, occasional dropped spaces). The underlying true values are
  unchanged — only how hard they are to read.
- ambiguous_currency: a clean auto_accept quote where ONE line item's price
  is labeled with a different currency code than the quote's real currency
  (a realistic supplier copy/paste slip), while the printed number is still
  the real-currency price. This is genuinely unresolvable from the text
  alone. Ground truth is anchored to the quote's real currency (the one the
  total and every other line use) — the same reading a careful human would
  most likely reach — never to the mislabeled one.

Ground truth for both categories reuses generate_data.ground_truth_for() on
the underlying CLEAN fields, never on the noised/mislabeled text, because the
distortion is applied to what the model reads, not to what's true. See
EVALS_HARD.md for the measured results and LIMITATIONS.md for how to read
this category's action-accuracy is (and is not) meaningful.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_gen.generate_data import CONTACTS, Fixture, build_fixture, ground_truth_for, money  # noqa: E402

OCR_SUBS = {"0": "O", "O": "0", "1": "l", "l": "1", "5": "S", "S": "5", "8": "B", "B": "8", "6": "G"}
ALL_CURRENCIES = ["EUR", "USD", "GBP", "JPY"]


def ocr_noise(text: str, rng: random.Random, char_rate: float = 0.05, space_drop_rate: float = 0.02) -> str:
    """Deterministic (given rng), character-level OCR-style corruption:
    digit/letter lookalikes swapped in at `char_rate`, spaces dropped at
    `space_drop_rate` (a common artifact of merged words in scanned text)."""
    out = []
    for ch in text:
        if ch == " " and rng.random() < space_drop_rate:
            continue
        if ch in OCR_SUBS and rng.random() < char_rate:
            out.append(OCR_SUBS[ch])
        else:
            out.append(ch)
    return "".join(out)


def render_ambiguous_currency(f: Fixture, rng: random.Random, contact: str,
                               deceptive_idx: int, wrong_ccy: str) -> str:
    lines = []
    for i, it in enumerate(f.true_items):
        ccy_label = wrong_ccy if i == deceptive_idx else f.currency
        price_s = f"{money(rng, it['unit_price'], f.currency)} {ccy_label} each"
        lines.append(f"- {it['qty']} x {it['description']}, {price_s}")
    total_s = f"{money(rng, f.true_total, f.currency)} {f.currency}"
    return (
        f"Hi,\n\nThanks for reaching out. This is {contact} from {f.supplier}.\n\n"
        "We can supply:\n" + "\n".join(lines) + "\n\n"
        f"Altogether that comes to about {total_s}. Let us know if you'd like to proceed."
        f"\n\nBest,\n{contact}\n{f.supplier}"
    )


def build_noisy_ocr_fixture(rng: random.Random, idx: int) -> dict:
    f = build_fixture(rng, idx, "auto_accept", "pdf_extract")
    noisy_text = ocr_noise(f.raw_text, rng)
    gt = ground_truth_for(f)
    return {
        "id": f"h{idx:03d}_noisy_ocr",
        "category": "noisy_ocr",
        "format": "pdf_extract_ocr_noise",
        "currency": f.currency,
        "has_injection": False,
        "notes": "OCR-style character noise over an otherwise clean auto-accept quote; ground truth is the clean fields.",
        "ground_truth": gt,
        "raw_text": noisy_text,
    }


def build_ambiguous_currency_fixture(rng: random.Random, idx: int) -> dict:
    f = build_fixture(rng, idx, "auto_accept", "free_text")
    if not f.true_items:
        f = build_fixture(rng, idx + 5000, "auto_accept", "free_text")
    contact = rng.choice(CONTACTS)
    other_currencies = [c for c in ALL_CURRENCIES if c != f.currency]
    wrong_ccy = rng.choice(other_currencies)
    deceptive_idx = rng.randrange(len(f.true_items))
    text = render_ambiguous_currency(f, rng, contact, deceptive_idx, wrong_ccy)
    gt = ground_truth_for(f)
    return {
        "id": f"h{idx:03d}_ambiguous_currency",
        "category": "ambiguous_currency",
        "format": "free_text_mixed_currency_label",
        "currency": f.currency,
        "has_injection": False,
        "notes": (f"line index {deceptive_idx} labeled '{wrong_ccy}' but priced in the quote's real "
                  f"currency ({f.currency}) — a realistic supplier mislabel, not resolvable from the text alone."),
        "ground_truth": gt,
        "raw_text": text,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data_hard")
    ap.add_argument("--seed", type=int, default=9001)
    ap.add_argument("--n-per-category", type=int, default=6)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out_dir)
    (out / "emails").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)

    fixtures = []
    idx = 0
    for _ in range(args.n_per_category):
        fixtures.append(build_noisy_ocr_fixture(rng, idx))
        idx += 1
    for _ in range(args.n_per_category):
        fixtures.append(build_ambiguous_currency_fixture(rng, idx))
        idx += 1
    rng.shuffle(fixtures)

    manifest = {"seed": args.seed, "total": 0, "by_category": {}}
    for item in fixtures:
        (out / "emails" / f"{item['id']}.txt").write_text(item["raw_text"])
        label = {k: v for k, v in item.items() if k != "raw_text"}
        (out / "labels" / f"{item['id']}.json").write_text(json.dumps(label, indent=2))
        manifest["by_category"][item["category"]] = manifest["by_category"].get(item["category"], 0) + 1
        manifest["total"] += 1
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {manifest['total']} hard fixtures to {out}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
