"""
Unit tests for resolve_head_term()'s tier-aware shortest-candidate
preference (the `high_confidence` parameter).

Uses a synthetic lexicon (not the real product_type_lexicon.json) so these
tests are deterministic and independent of catalog data drifting over time.
Confidence values mirror the real, observed shape of the actual bug this
fixes (real lexicon lookup done during this migration): a longer compound
("whole wheat bread") can have HIGHER raw confidence than its own base noun
("bread") while both are comfortably in the same tier, which previously made
resolve_head_term() anchor on the needlessly narrow compound.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.query_processing.product_intent_extractor import resolve_head_term

HIGH = 0.55
LOW = 0.25

LEXICON = {
    # "whole wheat bread" case — all three candidates clear HIGH; base noun
    # is not the highest-confidence one, but is the shortest at that tier.
    "bread": {"confidence": 0.6893, "dominant_category": "bread_and_buns"},
    "wheat bread": {"confidence": 0.8571, "dominant_category": "bread_and_buns"},
    "whole wheat bread": {"confidence": 0.8667, "dominant_category": "bread_and_buns"},

    # "greek yogurt" case — base noun clears HIGH, compound is only MEDIUM.
    # Different tiers -> no ambiguity -> base noun already wins even under
    # the OLD (pure-max) algorithm; must keep winning under the new one too.
    "yogurt": {"confidence": 0.622, "dominant_category": "yogurt_and_shrikhand"},
    "greek yogurt": {"confidence": 0.5272, "dominant_category": "yogurt_and_shrikhand"},

    # "protein granola bar" / "protein bar" — distinct, well-supported
    # product families genuinely deserve to win over the bare "bar" (only
    # MEDIUM on its own) — must NOT be flattened to "bar" by this change.
    "granola bar": {"confidence": 0.6364, "dominant_category": "energy_bars"},
    "protein bar": {"confidence": 0.7278, "dominant_category": "energy_bars"},
    "bar": {"confidence": 0.4399, "dominant_category": "energy_bars"},

    # Both candidates below LOW — genuinely ambiguous term (same class as
    # "cheese"), correctly resolves to nothing either way.
    "milk": {"confidence": 0.1682, "dominant_category": "chocolates"},
    "almond milk": {"confidence": 0.0463, "dominant_category": "chocolates"},

    # Medium-tier-only compounds (the "cheese" regression check) — must
    # still resolve to the compound, not collapse to a lower-confidence
    # unigram, since only high_confidence>=HIGH triggers the shortest-pick
    # logic relative to the compound's OWN tier, and there's no shorter
    # same-tier competitor here anyway ("cheese" alone is tier=none).
    "mozzarella cheese": {"confidence": 0.25, "dominant_category": "cheese"},
    "feta cheese": {"confidence": 0.375, "dominant_category": "cheese"},
    "cheese": {"confidence": 0.177, "dominant_category": "cheese"},
}


def _resolve(query: str):
    tokens = query.split()
    result = resolve_head_term(tokens, LEXICON, min_confidence=LOW, high_confidence=HIGH)
    return result[0] if result else None


class TestShortestWithinTier_TheActualFix:
    def test_whole_wheat_bread_anchors_on_base_noun(self):
        assert _resolve("whole wheat bread") == "bread"

    def test_wheat_bread_also_anchors_on_base_noun(self):
        assert _resolve("wheat bread") == "bread"

    def test_bare_bread_is_unaffected(self):
        assert _resolve("bread") == "bread"


class TestNoRegression_DifferentTiersStillWinOnConfidenceAlone:
    def test_greek_yogurt_resolves_to_yogurt(self):
        # yogurt=HIGH, greek yogurt=MEDIUM — different tiers, no ambiguity.
        assert _resolve("greek yogurt") == "yogurt"

    def test_organic_greek_yogurt_resolves_to_yogurt(self):
        assert _resolve("organic greek yogurt") == "yogurt"


class TestNoRegression_GenuineProductFamiliesStillWin:
    def test_protein_granola_bar_resolves_to_granola_bar_not_bar(self):
        # granola bar=HIGH, bar=MEDIUM — different tiers, granola bar wins
        # on its own merits, not flattened to the generic "bar".
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


class TestBackwardCompatibility_NoHighConfidenceArgUnchanged:
    """high_confidence=None (the default, used by index-time
    assign_product_type() in document_transformer.py) must reproduce the
    OLD pure-max-confidence behavior exactly — this is what keeps index-time
    per-product labeling unaffected by this fix."""

    def test_without_high_confidence_whole_wheat_bread_picks_the_max(self):
        tokens = "whole wheat bread".split()
        result = resolve_head_term(tokens, LEXICON, min_confidence=0.0)
        assert result[0] == "whole wheat bread"  # old behavior: pure max confidence

    def test_without_high_confidence_greek_yogurt_still_picks_yogurt(self):
        # yogurt's raw confidence is already the max, so this is unaffected
        # either way — included for completeness.
        tokens = "greek yogurt".split()
        result = resolve_head_term(tokens, LEXICON, min_confidence=0.0)
        assert result[0] == "yogurt"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
