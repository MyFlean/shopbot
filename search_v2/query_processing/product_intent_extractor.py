"""
search_v2/query_processing/product_intent_extractor.py
─────────────────────────────────────────────────────────
Product Intent Identification — deterministic, catalog-derived, no LLMs,
no NLP library, no manually-maintained product lists.

Humans search "product-first": they pick a PRODUCT (yogurt, chips, butter,
bread, bar) and everything else in the query modifies it (greek, protein,
almond, low carb, gluten free). This module recovers that structure from a
query by looking up the query's own trailing word phrases against a
"product type lexicon" — a catalog-derived artifact built at index time (see
indexing/product_type_lexicon_builder.py, search repo only) that scores every
phrase found in product names by how CONCENTRATED it is in a single category
(purity), how often it sits at the END of a name (head_ratio — English retail
naming is head-final: modifiers precede the head noun), and how much catalog
support it has (doc_freq). The three combine into one continuous confidence
score per phrase; there is no hardcoded per-product/per-phrase table anywhere
in this file.

Confidence is not treated as a hard yes/no. query_pipeline.py maps a resolved
term's confidence onto three tiers (see SearchV2Settings.PRODUCT_INTENT_*):

  high    -> retrieval GATES to this product type (hard filter)
  medium  -> retrieval BOOSTS this product type (strong should-clause, no exclusion)
  low/none-> retrieval is unchanged from today (no signal applied at all)

This graduated behavior is what keeps ambiguous queries ("healthy snack",
"protein snack") from being wrongly gated into one narrow product family,
while unambiguous ones ("greek yogurt", "protein chips", "almond butter")
get the precise, drift-free retrieval the catalog statistics support.

The SAME resolution algorithm (resolve_head_term) is used here at query time
AND, in the separate indexing-pipeline repo's OWN independent copy of this
file, to assign each product's own product_type field at index time — one
algorithm (kept identical by convention across the two repos' independent
copies, not by any runtime import between them), no drift between how a
query and a document each get labelled. This repo has no runtime dependency
on the indexing-pipeline repo or its filesystem — see this repo's copy of
indexing artifacts (product_type_lexicon.json) alongside this file, generated
and deployed here by the build/deploy pipeline, not read from another repo.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Canonical on-disk path for the generated lexicon — mirrors
# query_processing/vocabulary.json's role/placement exactly: the indexer
# (search repo) writes it, the runtime extractor (this module, both repos)
# reads it, so there is exactly one place to change if it ever moves.
PRODUCT_TYPE_LEXICON_PATH: Path = Path(__file__).resolve().parent / "product_type_lexicon.json"

_WORD_RE = re.compile(r"[a-zA-Z]+")

# A query head term is at most this many words — mirrors the same bound used
# when the lexicon itself was built (indexing/product_type_lexicon_builder.py's
# MAX_NGRAM_WORDS). Kept in sync by convention, not by import, since the two
# modules deliberately don't share a runtime dependency across the indexing/
# query_processing boundary (query_processing/ ships to shopbot-main, which
# has no indexing/ package at all).
MAX_NGRAM_WORDS = 3

# ── Broad Category fallback ──────────────────────────────────────────────────
# Fixes broad/generic queries ("cheese", "milk", "vegetables", "fruits") that
# resolve_head_term() correctly refuses to anchor on: a term that names an
# entire catalog category is, by definition, spread across many differently-
# named products rather than concentrated in one narrow family, so its own
# purity-based confidence is (correctly) too low to clear PRODUCT_INTENT_LOW_
# CONFIDENCE. That's the right call for "is this specific enough to filter/
# boost as a distinct PRODUCT?" — but it throws away a different, simpler,
# still-useful signal: "does the catalog have an entire CATEGORY by this
# name?", which doesn't need a purity score at all.
#
# _known_categories (built in ProductIntentExtractor.__init__) is sourced
# from the SAME product_type_lexicon.json every other resolution already
# uses — no new artifact, no new fetch pipeline. It's just the set of every
# dominant_category value the catalog-derived lexicon has ever assigned to
# ANY term (68 real leaf categories at last count: "cheese", "veggies",
# "fruits", "milk", "chips_and_crisps", ...). Because dominant_category only
# ever comes from real product-name statistics (see
# product_type_lexicon_builder.py), every value in this set is, by
# construction, a real, currently-populated catalog category — never
# invented, never hardcoded per query.
#
# This is checked ONLY as a fallback, after resolve_head_term() finds
# nothing (see extract() below) — it never competes with, overrides, or
# re-ranks an existing head-term resolution, so already-correct behavior for
# compound queries ("mozzarella cheese", "whole wheat bread", "tomato
# ketchup") is unaffected: those resolve via resolve_head_term() and never
# reach this code path at all.
CATEGORY_FALLBACK_CONFIDENCE = 0.4  # deliberately mid-"medium" — boost only, see filters.py's product_type_mode="boost"

# A few common category words are colloquial contractions/synonyms that
# don't derive from the catalog's own leaf name via simple pluralization
# ("veggies" is not "vegetable" + "s"). This is a general English-vocabulary
# equivalence, not a per-query special case — it applies to whichever leaf
# category the alias's target resolves to, wherever (if anywhere) that leaf
# actually exists in this catalog.
_CATEGORY_ALIAS_OVERRIDES: Dict[str, str] = {
    "vegetable": "veggies",
    "vegetables": "veggies",
    "veggie": "veggies",
}

# Shopbot Phase-1 ranking override for broad protein supplement queries:
# keep this as a medium-tier steering signal (never a hard gate), so generic
# protein queries can still return non-supplement products when relevant while
# strongly preferring supplement taxonomy.
_PROTEIN_SUPPLEMENT_OVERRIDES: Dict[str, Tuple[str, str]] = {
    # query_text: (resolved_product_type, dominant_category_leaf)
    # Dominant category must remain a category-level segment (lvl-2), not the
    # department-level "supplements" segment.
    "protein": ("whey protein", "protein"),
    "protein powder": ("whey protein", "protein"),
}

_PRE_WORKOUT_OVERRIDES: Dict[str, Tuple[str, str]] = {
    "pre workout": ("pre workout", "pre_workout"),
    "preworkout": ("pre workout", "pre_workout"),
}


def _build_category_alias_map(categories) -> Dict[str, str]:
    """{normalized query phrase -> catalog dominant_category leaf}, derived
    purely from the leaf names already present in the lexicon (see module
    comment above) plus simple singular/plural normalization."""
    alias_map: Dict[str, str] = {}
    for cat in categories:
        if not cat:
            continue
        label = cat.replace("_", " ").strip().lower()
        if not label:
            continue
        forms = {label}
        if label.endswith("s") and len(label) > 1:
            forms.add(label[:-1])
        else:
            forms.add(label + "s")
        for form in forms:
            alias_map.setdefault(form, cat)
    for alias, target_cat in _CATEGORY_ALIAS_OVERRIDES.items():
        if target_cat in categories:
            alias_map.setdefault(alias, target_cat)
    return alias_map


@dataclass
class ProductIntentResult:
    """Output of ProductIntentExtractor.extract().

    primary_product   — the resolved head-noun phrase ("yogurt", "chips",
                         "butter", ...), or None if nothing in the query
                         matched the lexicon at all.
    modifiers         — every query token BEFORE the resolved head phrase
                         (e.g. ["greek"] for "greek yogurt"). Informational —
                         the query text itself is never rewritten; modifiers
                         still flow into lexical/semantic scoring unchanged.
    confidence        — the lexicon's stored confidence for primary_product,
                         in [0, 1]. 0.0 when primary_product is None.
    tier              — "high" | "medium" | "low" | "none", the settings-driven
                         classification of `confidence` (see
                         SearchV2Settings.PRODUCT_INTENT_HIGH_CONFIDENCE /
                         PRODUCT_INTENT_LOW_CONFIDENCE). Only "high"/"medium"
                         ever translate into a retrieval-time signal.
    dominant_category — the category_hierarchies leaf segment the resolved
                         term is most concentrated in, if any — used as a
                         secondary (OR) signal alongside the exact
                         product_type match to catch relevant products whose
                         own name doesn't happen to contain the same phrase.
    fresh_produce_ids  — catalog ids for the matched produce family. Hard
                         retrieval restriction applies only when
                         fresh_produce_exact is True.
    fresh_produce_exact — True for an exact curated alias match, False for a
                         fuzzy_match_produce_alias() edit-distance match.
    source            — which resolution mechanism produced this result:
                         "fresh_produce" | "category_fallback" | "head_term" |
                         "none". Lets downstream consumers (e.g. the query
                         router) key decisions off *how* a result was
                         resolved rather than only its numeric confidence.
    """
    primary_product: Optional[str] = None
    modifiers: List[str] = field(default_factory=list)
    confidence: float = 0.0
    tier: str = "none"
    dominant_category: Optional[str] = None
    fresh_produce_ids: Tuple[str, ...] = ()
    fresh_produce_exact: bool = False
    source: str = "none"


def resolve_head_term(
    tokens: List[str],
    lexicon: Dict[str, Dict[str, Any]],
    min_confidence: float = 0.0,
) -> Optional[Tuple[str, Dict[str, Any], int]]:
    """Find the TRAILING word phrase (up to MAX_NGRAM_WORDS) of `tokens` that
    is the MOST SPECIFIC candidate clearing `min_confidence`.

    English retail product names and shopping queries are head-final:
    modifiers precede the product ("greek yogurt", "protein chips", "low
    carb bread") — so only TRAILING phrases are ever considered candidates,
    which is what correctly separates "greek" (modifier) from "yogurt"
    (head) without any per-word classification table.

    Specificity wins over raw confidence: candidates are tried LONGEST
    first (MAX_NGRAM_WORDS words down to 1), and the first one whose own
    catalog-derived confidence clears `min_confidence` is returned
    immediately. A valid, well-supported specific compound ("greek yogurt",
    "whole wheat bread", "green tea") is therefore never displaced by a
    shorter, higher-confidence generic parent it happens to be nested
    inside ("yogurt", "bread", "tea") — the specific term's OWN confidence
    still governs its tier (see ProductIntentExtractor.extract()), only the
    SELECTION among candidates changes. A candidate that fails to clear
    `min_confidence` is never selected regardless of length, so a longer
    phrase only wins when the catalog genuinely supports it as a real
    signal — Broad Category fallback (see ProductIntentExtractor.
    _resolve_category_fallback()) remains the only path for a generic
    catalog category name to resolve a query, and only fires when nothing
    at any length clears `min_confidence` here.

    Falls back to a light, generic singular/plural fold (mirrors the
    existing rstrip("s") mild singularization already used in
    nl_filter_extractor.py's ingredient-exclusion handling) so "chip" and
    "chips" resolve to whichever surface form the catalog actually uses.

    `min_confidence` — a candidate that doesn't clear this floor is skipped
    in favor of the next-shorter one; if nothing at any length clears it,
    returns None (used by ProductIntentExtractor.extract() to apply
    PRODUCT_INTENT_LOW_CONFIDENCE; index-time per-document assignment in the
    indexing-pipeline repo's own independent copy of this file calls this
    with the default 0.0 so it always stores its best-effort guess,
    deferring the tiering decision to query time).

    Returns (matched_term, lexicon_entry, split_index) where split_index is
    the token index the match starts at (tokens[:split_index] are the
    modifiers) — or None if nothing at any length clears `min_confidence`
    (or nothing in the lexicon matched at all).
    """
    n = len(tokens)
    if n == 0 or not lexicon:
        return None

    max_size = min(MAX_NGRAM_WORDS, n)
    for size in range(max_size, 0, -1):
        split_idx = n - size
        candidate = " ".join(tokens[split_idx:])

        entry = lexicon.get(candidate)
        matched_term = candidate
        if entry is None:
            # Light plural/singular fold — try the other surface form before
            # giving up on this n-gram length entirely.
            if candidate.endswith("s") and len(candidate) > 3:
                alt = candidate[:-1]
            else:
                alt = candidate + "s"
            entry = lexicon.get(alt)
            matched_term = alt

        if entry is None:
            continue

        confidence = float(entry.get("confidence", 0.0))
        if confidence >= min_confidence:
            return matched_term, entry, split_idx

    return None


def load_product_type_lexicon(path: Path = PRODUCT_TYPE_LEXICON_PATH) -> Dict[str, Dict[str, Any]]:
    """Load the generated lexicon from disk. Returns an empty dict when the
    file does not yet exist — callers (gateway.py) should treat that as
    "Product Intent Identification disabled" and fall back to unchanged
    retrieval behavior, exactly like typo correction does for a missing
    vocabulary.json."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


class ProductIntentExtractor:
    """
    Usage:
        extractor = ProductIntentExtractor(lexicon)
        result = extractor.extract("greek yogurt")
        # result.primary_product -> "yogurt"
        # result.modifiers       -> ["greek"]
        # result.tier            -> "high" (assuming the catalog supports it)
    """

    def __init__(
        self,
        lexicon: Optional[Dict[str, Dict[str, Any]]] = None,
        settings=None,
        produce_aliases: Optional[Dict[str, Any]] = None,
    ):
        """
        produce_aliases — {alias(normalized): FreshProduceFamily}, from
        canonical_produce.load_produce_alias_map(). Typed loosely (Any) to
        avoid a hard import dependency on canonical_produce.py from this
        module; callers pass the real FreshProduceFamily instances.
        """
        self._lexicon = lexicon or {}
        self._produce_aliases = produce_aliases or {}
        from search_v2.config.settings import SETTINGS as _SETTINGS
        self._settings = settings or _SETTINGS
        known_categories = {
            entry.get("dominant_category")
            for entry in self._lexicon.values()
            if entry.get("dominant_category")
        }
        self._category_alias_map = _build_category_alias_map(known_categories)

    def _resolve_category_fallback(self, tokens: List[str]) -> Optional[ProductIntentResult]:
        """Broad Category fallback — see module comment above CATEGORY_FALLBACK_
        CONFIDENCE. Scans every contiguous token window (longest first, so a
        multi-word category label like "chips and crisps" wins over any
        shorter accidental overlap) looking for an exact match against a
        known catalog category. Unmatched tokens on either side (e.g. "for
        kids" in "vegetables for kids", "fresh" in "fresh vegetables") are
        simply carried as modifiers — they don't block the match, since a
        category fallback is intentionally a coarser, position-independent
        signal than the head-final resolve_head_term() above it."""
        if not self._category_alias_map:
            return None
        n = len(tokens)
        for size in range(min(MAX_NGRAM_WORDS, n), 0, -1):
            for start in range(0, n - size + 1):
                phrase = " ".join(tokens[start:start + size])
                cat = self._category_alias_map.get(phrase)
                if cat is None:
                    continue
                modifiers = tokens[:start] + tokens[start + size:]
                return ProductIntentResult(
                    primary_product=cat,
                    modifiers=modifiers,
                    confidence=CATEGORY_FALLBACK_CONFIDENCE,
                    tier="medium",
                    dominant_category=cat,
                    source="category_fallback",
                )
        return None

    def extract(self, clean_query: str) -> ProductIntentResult:
        text = (clean_query or "").strip().lower()
        if not text:
            return ProductIntentResult()

        tokens = _WORD_RE.findall(text)
        if not tokens:
            return ProductIntentResult()

        # Curated vernacular produce resolution — checked BEFORE the normal
        # catalog-statistics lexicon lookup below. Only fires when the
        # ENTIRE cleaned query (not a substring/prefix) exactly matches a
        # curated canonical produce name or one of its aliases (see
        # canonical_produce.py). A curated exact match is definitionally
        # unambiguous, so it resolves at maximum confidence/"high" tier
        # directly rather than through the catalog-derived confidence
        # formula below. If there's no match, this is a complete no-op and
        # the existing lexicon-based resolution runs exactly as before.
        if self._produce_aliases:
            normalized = " ".join(tokens)
            family = self._produce_aliases.get(normalized)
            exact_match = family is not None
            if family is None and normalized not in self._lexicon:
                from search_v2.query_processing.canonical_produce import fuzzy_match_produce_alias
                family = fuzzy_match_produce_alias(normalized, self._produce_aliases)
            if family is not None:
                return ProductIntentResult(
                    primary_product=family.canonical_name,
                    modifiers=[],
                    confidence=1.0,
                    tier="high",
                    dominant_category=None,
                    fresh_produce_ids=family.member_ids,
                    fresh_produce_exact=exact_match,
                    source="fresh_produce",
                )

        normalized = " ".join(tokens)
        override = _PROTEIN_SUPPLEMENT_OVERRIDES.get(normalized)
        if override is not None:
            product_type, dominant_category = override
            return ProductIntentResult(
                primary_product=product_type,
                modifiers=[],
                confidence=CATEGORY_FALLBACK_CONFIDENCE,
                tier="medium",
                dominant_category=dominant_category,
                source="category_fallback",
            )

        pre_workout_override = _PRE_WORKOUT_OVERRIDES.get(normalized)
        if pre_workout_override is not None:
            product_type, dominant_category = pre_workout_override
            return ProductIntentResult(
                primary_product=product_type,
                modifiers=[],
                confidence=CATEGORY_FALLBACK_CONFIDENCE,
                tier="medium",
                dominant_category=dominant_category,
                source="category_fallback",
            )

        if not self._lexicon:
            return ProductIntentResult()

        high = getattr(self._settings, "PRODUCT_INTENT_HIGH_CONFIDENCE", 0.55)
        low = getattr(self._settings, "PRODUCT_INTENT_LOW_CONFIDENCE", 0.25)

        # min_confidence=low: a candidate that can't even clear the LOW bar
        # isn't worth surfacing as a resolved intent at all — retrieval is
        # meant to be completely unaffected in that case (see tier="none").
        # tier is still classified below from the WINNING candidate's own
        # confidence — resolve_head_term() only decides which candidate wins.
        resolved = resolve_head_term(tokens, self._lexicon, min_confidence=low)
        if resolved is None:
            category_result = self._resolve_category_fallback(tokens)
            return category_result if category_result is not None else ProductIntentResult()

        term, entry, split_idx = resolved
        confidence = float(entry.get("confidence", 0.0))
        modifiers = tokens[:split_idx]

        if confidence >= high:
            tier = "high"
        elif confidence >= low:
            tier = "medium"
        else:
            tier = "low"

        return ProductIntentResult(
            primary_product=term,
            modifiers=modifiers,
            confidence=confidence,
            tier=tier,
            dominant_category=entry.get("dominant_category"),
            source="head_term",
        )
