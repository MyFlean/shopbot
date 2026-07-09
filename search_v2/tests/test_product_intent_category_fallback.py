"""
Unit tests for two ProductIntentExtractor fixes made together while
investigating why broad/generic queries ("cheese", "vegetables", "fruits")
weren't returning the expected product coverage:

1. Broad Category fallback (_build_category_alias_map / _resolve_category_
   fallback) — a term whose own purity-based confidence is too low to
   resolve as a specific PRODUCT (e.g. "cheese", spread across many
   differently-named products) can still name a real, populated catalog
   CATEGORY. This fallback only ever fires when resolve_head_term() finds
   nothing, so it must never change behavior for any query that already
   resolves via the normal path.

2. Fresh Produce fuzzy-match false-positive guard — fuzzy_match_produce_
   alias() previously ran on ANY exact-alias miss, which let ordinary,
   correctly-spelled words (e.g. "cheese", 2 edits from the curated Hindi
   alias "cheeku"/sapota) collide with an unrelated curated produce alias
   purely by coincidence and get hard-restricted to that family's ids. The
   fix: skip the fuzzy attempt whenever the query text is already a real
   key in the product-type lexicon (i.e. already catalog-attested in its
   own right, so not itself a typo of anything).

Uses synthetic lexicons/alias maps (not the real catalog artifacts) so these
tests are deterministic and independent of catalog data drifting over time.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.query_processing.product_intent_extractor import ProductIntentExtractor

LOW = 0.25
HIGH = 0.55


class _FakeSettings:
    PRODUCT_INTENT_HIGH_CONFIDENCE = HIGH
    PRODUCT_INTENT_LOW_CONFIDENCE = LOW


# A synthetic lexicon shaped like the real one: "cheese" is a real,
# catalog-attested term but too low-purity to anchor as its own product
# (below LOW) — the same shape as "milk", "vegetable" etc. in the real
# lexicon. "mozzarella cheese" / "bread" resolve normally and must be
# completely unaffected by the category fallback.
LEXICON = {
    "cheese": {"confidence": 0.177, "dominant_category": "cheese"},
    "milk": {"confidence": 0.168, "dominant_category": "chocolates"},
    "mozzarella cheese": {"confidence": 0.25, "dominant_category": "cheese"},
    "bread": {"confidence": 0.6893, "dominant_category": "bread_and_buns"},
    "veggies": {"confidence": 0.30, "dominant_category": "veggies"},
    "fruits": {"confidence": 0.28, "dominant_category": "fruits"},
    "chips_and_crisps_leaf_source": {"confidence": 0.65, "dominant_category": "chips_and_crisps"},
}


@dataclass
class _FakeFamily:
    canonical_name: str
    member_ids: Tuple[str, ...]


def _extractor(produce_aliases=None) -> ProductIntentExtractor:
    return ProductIntentExtractor(LEXICON, settings=_FakeSettings(), produce_aliases=produce_aliases)


# ── Broad Category fallback ──────────────────────────────────────────────────

def test_bare_category_word_resolves_as_boost():
    result = _extractor().extract("cheese")
    assert result.primary_product == "cheese"
    assert result.tier == "medium"
    assert result.dominant_category == "cheese"


def test_category_fallback_does_not_fire_when_head_term_already_resolves():
    result = _extractor().extract("mozzarella cheese")
    assert result.primary_product == "mozzarella cheese"
    assert result.dominant_category == "cheese"
    # Confidence must be the head-term lexicon's own value, not the fallback's
    # fixed CATEGORY_FALLBACK_CONFIDENCE — proves the fallback never ran.
    assert result.confidence == 0.25


def test_existing_high_confidence_resolution_unaffected_by_category_fallback():
    result = _extractor().extract("bread")
    assert result.primary_product == "bread"
    assert result.tier == "high"
    assert result.confidence == 0.6893


def test_category_word_not_in_lexicon_at_all_still_resolves_via_taxonomy():
    """"vegetables" itself is never a lexicon key (fresh produce isn't named
    with the word "vegetable"), but "veggies" is a known dominant_category
    value (from some OTHER term's entry) — the colloquial alias table must
    bridge "vegetables" -> "veggies"."""
    result = _extractor().extract("vegetables")
    assert result.primary_product == "veggies"
    assert result.tier == "medium"
    assert result.dominant_category == "veggies"


def test_category_fallback_handles_leading_and_trailing_modifiers():
    fresh = _extractor().extract("fresh vegetables")
    assert fresh.primary_product == "veggies"
    assert fresh.modifiers == ["fresh"]

    for_kids = _extractor().extract("vegetables for kids")
    assert for_kids.primary_product == "veggies"
    assert for_kids.modifiers == ["for", "kids"]


def test_unrecognized_broad_word_stays_unresolved():
    """A word that isn't a known category and doesn't resolve via the normal
    lexicon must stay tier=none — the fallback must never invent a match."""
    result = _extractor().extract("snacks")
    assert result.primary_product is None
    assert result.tier == "none"


# ── Fresh Produce fuzzy-match false-positive guard ───────────────────────────

def test_fuzzy_produce_match_blocked_for_real_lexicon_term():
    """"cheese" is a real lexicon term (see LEXICON above) that happens to be
    edit-distance-2 from a hypothetical curated alias "cheeku" — the guard
    must prevent this collision, leaving the (separate) category fallback as
    the only thing that resolves it."""
    produce_aliases = {"cheeku": _FakeFamily("sapota", ("sapota-1",))}
    result = _extractor(produce_aliases).extract("cheese")
    assert result.fresh_produce_ids == ()
    assert result.primary_product == "cheese"  # category fallback, not produce


def test_fuzzy_produce_match_still_works_for_genuine_non_lexicon_typo():
    """A genuine near-miss typo of a curated alias, where the typo itself is
    NOT already a real lexicon term, must still resolve via the fuzzy path —
    the guard must only block real-word collisions, not the intended
    double-typo recovery case."""
    produce_aliases = {"onion": _FakeFamily("onion", ("onion-1", "onion-2"))}
    # "onian" is not in LEXICON and is within the length-scaled budget of "onion".
    result = _extractor(produce_aliases).extract("onian")
    assert result.fresh_produce_ids == ("onion-1", "onion-2")
    assert result.tier == "high"


def test_exact_produce_alias_match_unaffected_by_guard():
    produce_aliases = {"onion": _FakeFamily("onion", ("onion-1", "onion-2"))}
    result = _extractor(produce_aliases).extract("onion")
    assert result.fresh_produce_ids == ("onion-1", "onion-2")
    assert result.primary_product == "onion"
    assert result.tier == "high"
