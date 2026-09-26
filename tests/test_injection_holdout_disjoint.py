"""Backs the claim in EVALS_INJECTION_HOLDOUT.md that the held-out phrases are
disjoint from the detector's own vocabulary: none of them is matched by the
regex, and their word overlap with the 4 in-distribution injection phrases
baked into data_gen/generate_data.py is limited to one known, disclosed
4-word run ("the order right away", in i008), which the regex still misses
because it requires "place" in front of it."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from data_gen.generate_injection_holdout import HOLDOUT_INJECTION_PHRASES
from procurement_agent.tools import _INJECTION_RE

ROOT = Path(__file__).resolve().parent.parent


def _training_phrases() -> list[str]:
    """The literal list passed to rng.choice() for `injection_text` in the
    main generator, read from source so the test can't drift from it."""
    tree = ast.parse((ROOT / "data_gen" / "generate_data.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "injection_text" for t in node.targets)
            and isinstance(node.value, ast.Call)
            and node.value.args
            and isinstance(node.value.args[0], ast.List)
        ):
            return [elt.value for elt in node.value.args[0].elts]
    raise AssertionError("injection phrase list not found in generate_data.py")


def _ngrams(text: str, n: int = 4) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z']+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def test_holdout_has_ten_phrases():
    assert len(HOLDOUT_INJECTION_PHRASES) == 10


def test_no_holdout_phrase_matches_the_injection_regex():
    hits = [p for p in HOLDOUT_INJECTION_PHRASES if _INJECTION_RE.search(p)]
    assert hits == []


def test_every_training_phrase_does_match_the_regex():
    # Sanity check that the regex under test is the one the in-distribution
    # set was scored with; otherwise the previous test proves nothing.
    training = _training_phrases()
    assert len(training) == 4
    assert all(_INJECTION_RE.search(p) for p in training)


def test_four_word_overlap_with_training_phrases_is_only_the_known_one():
    training_grams = set().union(*(_ngrams(p) for p in _training_phrases()))
    overlaps = {i: _ngrams(p) & training_grams for i, p in enumerate(HOLDOUT_INJECTION_PHRASES)}
    assert {i: o for i, o in overlaps.items() if o} == {8: {("the", "order", "right", "away")}}
