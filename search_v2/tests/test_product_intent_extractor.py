"""
Unit tests for resolve_head_term()'s specificity-first selection policy.

Uses a synthetic lexicon (not the real product_type_lexicon.json) so these
tests are deterministic and independent of catalog data drifting over time.
Confidence values mirror the real, observed shape of a genuine query-time
bug found during a routing-architecture investigation: a shorter, generic
base noun ("bread", "yogurt", "tea") can have HIGHER raw catalog confidence
than a longer, well-supported specific compound it's nested inside ("whole
wheat bread", "greek yogurt", "green tea"). The previous algorithm compared
raw confidence directly (or, for same-tier candidates, preferred the
shortest), which silently weakened an explicit, valid user intent ("greek
yogurt") down to its generic parent ("yogurt") whenever the parent's own
catalog stats happened to be stronger — contradicting the architecture rule
that a valid specific head-term must never be displaced by a more generic
one. resolve_head_term() now tries candidates longest-first and returns the
first one whose OWN confidence clears `min_confidence`, so specificity wins
whenever the catalog genuinely supports it, and only degrades to a shorter
candidate (or, failing that, Category Fallback) when the longer one isn't
itself a valid signal.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.query_processing.product_intent_extractor import resolve_head_term

HIGH = 0.55
LOW = 0.25

LEXICON = {
    # "whole wheat bread" case — the base noun has lower raw confidence than
    # either compound nested inside it; specificity must still win.
    "bread": {"confidence": 0.6893, "dominant_category": "bread_and_buns"},
    "wheat bread": {"confidence": 0.8571, "dominant_category": "bread_and_buns"},
    "whole wheat bread": {"confidence": 0.8667, "dominant_category": "bread_and_buns"},

    # "greek yogurt" case — base noun ("yogurt") has HIGHER raw confidence
    # than the specific compound, and the two land in different tiers
    # (yogurt=high, greek yogurt=medium) — the compound must still win,
    # using its OWN confidence/tier, not the base noun's.
    "yogurt": {"confidence": 0.622, "dominant_category": "yogurt_and_shrikhand"},
    "greek yogurt": {"confidence": 0.5272, "dominant_category": "yogurt_and_shrikhand"},

    # "protein granola bar" / "protein bar" — distinct, well-supported
    # product families genuinely deserve to win over the bare "bar" (only
    # MEDIUM on its own) — must remain unaffected by this change.
    "granola bar": {"confidence": 0.6364, "dominant_category": "energy_bars"},
    "protein bar": {"confidence": 0.7278, "dominant_category": "energy_bars"},
    "bar": {"confidence": 0.4399, "dominant_category": "energy_bars"},

    # Both candidates below LOW — genuinely ambiguous term (same class as
    # "cheese"), correctly resolves to nothing either way, so Category
    # Fallback (not tested here — see test_product_intent_category_fallback.py)
    # is left as the only path to a result.
    "milk": {"confidence": 0.1682, "dominant_category": "chocolates"},
    "almond milk": {"confidence": 0.0463, "dominant_category": "chocolates"},

    # Medium-tier-only compounds (the "cheese" regression check) — must
    # still resolve to the compound; there's no shorter competitor here
    # anyway ("cheese" alone is below LOW, tier=none).
    "mozzarella cheese": {"confidence": 0.25, "dominant_category": "cheese"},
    "feta cheese": {"confidence": 0.375, "dominant_category": "cheese"},
    "cheese": {"confidence": 0.177, "dominant_category": "cheese"},
}


def _resolve(query: str):
    tokens = query.split()
    result = resolve_head_term(tokens, LEXICON, min_confidence=LOW)
    return result[0] if result else None


class TestSpecificityWins_TheActualFix:
    def test_whole_wheat_bread_stays_specific(self):
        assert _resolve("whole wheat bread") == "whole wheat bread"

    def test_wheat_bread_stays_specific(self):
        assert _resolve("wheat bread") == "wheat bread"

    def test_bare_bread_is_unaffected(self):
        assert _resolve("bread") == "bread"

    def test_greek_yogurt_stays_specific_despite_lower_raw_confidence(self):
        # yogurt=0.622 (high tier), greek yogurt=0.5272 (medium tier) — the
        # base noun has the higher raw confidence, but greek yogurt is a
        # valid signal on its own (clears LOW) and must not be displaced.
        assert _resolve("greek yogurt") == "greek yogurt"

    def test_organic_greek_yogurt_stays_specific(self):
        assert _resolve("organic greek yogurt") == "greek yogurt"


class TestNoRegression_GenuineProductFamiliesStillWin:
    def test_protein_granola_bar_resolves_to_granola_bar_not_bar(self):
        assert _resolve("protein granola bar") == "granola bar"

    def test_protein_bar_resolves_to_protein_bar_not_bar(self):
        assert _resolve("protein bar") == "protein bar"


class TestNoRegression_GenuineAmbiguityStaysUnresolved:
    def test_almond_milk_stays_none_both_below_low(self):
        assert _resolve("almond milk") is None

    def test_milk_alone_stays_none_below_low(self):
        assert _resolve("milk") is None


class TestNoRegression_MediumTierCompoundsUnaffected:
    def test_mozzarella_cheese_still_resolves_to_the_compound(self):
        assert _resolve("mozzarella cheese") == "mozzarella cheese"

    def test_feta_cheese_still_resolves_to_the_compound(self):
        assert _resolve("feta cheese") == "feta cheese"

    def test_bare_cheese_stays_none(self):
        assert _resolve("cheese") is None


class TestDegradesToShorterCandidate_WhenLongerOneIsNotValid:
    def test_falls_back_to_base_noun_when_compound_not_in_lexicon(self):
        # "black tea" is not a lexicon key at all — falls through to "tea".
        lexicon = dict(LEXICON, tea={"confidence": 0.4713, "dominant_category": "tea"})
        tokens = "black tea".split()
        result = resolve_head_term(tokens, lexicon, min_confidence=LOW)
        assert result[0] == "tea"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
