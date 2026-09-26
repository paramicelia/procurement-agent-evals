"""Scoring functions for the eval harness. Kept separate from run_eval.py so
they can be unit-tested on their own (tests/test_metrics.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from procurement_agent.models import ExtractedQuote

NUMERIC_TOLERANCE_PCT = 2.0  # rounding noise in the synthetic data's own rendering


def _num_close(a: float | None, b: float | None, tol_pct: float = NUMERIC_TOLERANCE_PCT) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if b == 0:
        return abs(a - b) < 1e-6
    return abs(a - b) / abs(b) * 100.0 <= tol_pct


def _text_close(a: str | None, b: str | None, threshold: float = 0.6) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    ratio = SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()
    return ratio >= threshold


@dataclass
class FieldScore:
    field: str
    correct: bool
    expected: object
    got: object


@dataclass
class ExtractionScore:
    fields: list[FieldScore] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.fields)

    @property
    def correct(self) -> int:
        return sum(1 for f in self.fields if f.correct)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0

    def wrong_fields(self) -> list[FieldScore]:
        return [f for f in self.fields if not f.correct]


def score_extraction(predicted: ExtractedQuote, expected: ExtractedQuote) -> ExtractionScore:
    """Per-field comparison. Top-level fields + per-item fields, matched by
    index (the synthetic data's item order is deterministic, so index
    alignment is a fair, simple choice here — not a general solution)."""
    score = ExtractionScore()
    score.fields.append(FieldScore("supplier", _text_close(predicted.supplier, expected.supplier),
                                    expected.supplier, predicted.supplier))
    score.fields.append(FieldScore(
        "currency",
        (predicted.currency or "").strip().upper() == (expected.currency or "").strip().upper(),
        expected.currency, predicted.currency,
    ))
    score.fields.append(FieldScore("total", _num_close(predicted.total, expected.total),
                                    expected.total, predicted.total))

    n = max(len(predicted.items), len(expected.items))
    for i in range(n):
        p = predicted.items[i] if i < len(predicted.items) else None
        e = expected.items[i] if i < len(expected.items) else None
        if p is None or e is None:
            score.fields.append(FieldScore(f"item[{i}].presence", False, e, p))
            continue
        score.fields.append(FieldScore(f"item[{i}].description", _text_close(p.description, e.description),
                                        e.description, p.description))
        score.fields.append(FieldScore(f"item[{i}].qty", _num_close(p.qty, e.qty), e.qty, p.qty))
        score.fields.append(FieldScore(f"item[{i}].unit_price", _num_close(p.unit_price, e.unit_price),
                                        e.unit_price, p.unit_price))
    return score


_CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}


def _currency_literally_absent(expected_currency: str | None, raw_text: str) -> bool:
    """True if neither the currency code nor its symbol appears anywhere in
    the raw text — i.e. a reader (human or model) genuinely cannot recover
    it, as opposed to the model misreading a currency that IS stated."""
    if not expected_currency or not raw_text:
        return False
    code = expected_currency.strip().upper()
    symbol = _CURRENCY_SYMBOLS.get(code, "")
    text = raw_text.upper()
    return code not in text and (not symbol or symbol not in raw_text)


def classify_failure(
    predicted: ExtractedQuote,
    expected: ExtractedQuote,
    predicted_action: str,
    expected_action: str,
    ext_score: ExtractionScore,
    has_injection: bool,
    raw_text: str = "",
) -> str | None:
    """Best-effort bucket for a mismatch, for the failure taxonomy in
    EVALS.md. Order matters: more specific categories are checked first."""
    if predicted_action == expected_action and ext_score.accuracy == 1.0:
        return None
    if has_injection and predicted_action != "escalate_human":
        return "injection_not_escalated"
    for f in ext_score.wrong_fields():
        if f.field == "currency":
            if predicted.currency is None and _currency_literally_absent(expected.currency, raw_text):
                # The label says a currency, but the rendered text never
                # states it (a generator gap, not an extraction mistake):
                # the model correctly flagged it as missing.
                return "generator_currency_not_stated_in_text"
            return "currency_misread"
    for i, p_item in enumerate(predicted.items):
        e_item = expected.items[i] if i < len(expected.items) else None
        if e_item is not None:
            if e_item.unit_price is None and p_item.unit_price is not None:
                return "hallucinated_value_for_missing_field"
            if e_item.qty is None and p_item.qty is not None:
                return "hallucinated_value_for_missing_field"
            if e_item.unit_price is not None and p_item.unit_price is None:
                return "wrongly_flagged_present_field_as_missing"
    if any(f.field.endswith(".description") for f in ext_score.wrong_fields()):
        return "item_description_misread"
    if any(f.field == "total" for f in ext_score.wrong_fields()):
        return "total_misread"
    if predicted_action != expected_action:
        return "action_mismatch_from_correct_extraction"
    return "other"
