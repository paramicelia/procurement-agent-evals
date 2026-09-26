from __future__ import annotations

from evals.metrics import classify_failure, score_extraction
from procurement_agent.models import ExtractedQuote, QuoteLineItem


def make(**kw):
    defaults = dict(supplier="Acme", currency="EUR", total=11.0,
                     items=[QuoteLineItem(description="M6 Hex Bolt", qty=100, unit_price=0.11)])
    defaults.update(kw)
    return ExtractedQuote(**defaults)


def test_identical_quotes_score_perfect():
    q = make()
    s = score_extraction(q, q)
    assert s.accuracy == 1.0


def test_currency_typo_is_wrong_field():
    a = make(currency="EUR")
    b = make(currency="USD")
    s = score_extraction(a, b)
    assert s.accuracy < 1.0
    assert any(f.field == "currency" and not f.correct for f in s.fields)


def test_small_rounding_within_tolerance():
    a = make(total=11.00)
    b = make(total=11.05)  # <2% off
    s = score_extraction(a, b)
    assert s.accuracy == 1.0


def test_large_price_diff_outside_tolerance():
    a = make(items=[QuoteLineItem(description="M6 Hex Bolt", qty=100, unit_price=0.11)])
    b = make(items=[QuoteLineItem(description="M6 Hex Bolt", qty=100, unit_price=0.20)])
    s = score_extraction(a, b)
    assert s.accuracy < 1.0


def test_classify_failure_none_when_everything_matches():
    q = make()
    s = score_extraction(q, q)
    assert classify_failure(q, q, "auto_accept", "auto_accept", s, False) is None


def test_classify_failure_injection_not_escalated():
    q = make()
    s = score_extraction(q, q)
    cat = classify_failure(q, q, "auto_accept", "escalate_human", s, has_injection=True)
    assert cat == "injection_not_escalated"


def test_classify_failure_hallucinated_missing_field():
    expected = make(items=[QuoteLineItem(description="M6 Hex Bolt", qty=100, unit_price=None)], total=None)
    predicted = make(items=[QuoteLineItem(description="M6 Hex Bolt", qty=100, unit_price=0.11)], total=11.0)
    s = score_extraction(predicted, expected)
    cat = classify_failure(predicted, expected, "auto_accept", "request_clarification", s, False)
    assert cat == "hallucinated_value_for_missing_field"
