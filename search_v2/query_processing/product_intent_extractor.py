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
    fresh_produce_ids  — set ONLY when the query exactly matched a curated
                         vernacular produce alias/name (see
                         canonical_produce.py). The authoritative catalog
                         product ids belonging to that produce family —
                         retrieval hard-restricts to exactly these ids
                         (SearchFilters.product_ids) instead of any text
                         signal. Empty tuple (not None) when no such match.
    """
    primary_product: Optional[str] = None
    modifiers: List[str] = field(default_factory=list)
    confidence: float = 0.0
    tier: str = "none"
    dominant_category: Optional[str] = None
    fresh_produce_ids: Tuple[str, ...] = ()


def resolve_head_term(
    tokens: List[str],
    lexicon: Dict[str, Dict[str, Any]],
    min_confidence: float = 0.0,
    high_confidence: Optional[float] = None,
) -> Optional[Tuple[str, Dict[str, Any], int]]:
    """Find the TRAILING word phrase (up to MAX_NGRAM_WORDS) of `tokens` with
    the HIGHEST catalog-derived confidence among every trailing 1/2/3-word
    candidate found in `lexicon`.

    English retail product names and shopping queries are head-final:
    modifiers precede the product ("greek yogurt", "protein chips", "low
    carb bread") — so only TRAILING phrases are ever considered candidates,
    which is what correctly separates "greek" (modifier) from "yogurt"
    (head) without any per-word classification table.

    Deliberately NOT "longest trailing phrase present in the lexicon" —
    a longer phrase that happens to appear in the lexicon isn't necessarily
    a BETTER product-type signal than a shorter one nested inside it (e.g. a
    rare, coincidentally-catalogued "greek yogurt" bigram should lose to a
    catalog-wide well-established "yogurt" unigram if the unigram's
    confidence is actually higher). Comparing confidence directly is what
    lets a genuinely distinct, well-supported longer phrase (e.g. "protein
    bar" as its own product family, if the catalog's own statistics support
    that) win over a shorter one — an emergent, data-driven distinction, not
    a hardcoded preference for one length over another.

    Falls back to a light, generic singular/plural fold (mirrors the
    existing rstrip("s") mild singularization already used in
    nl_filter_extractor.py's ingredient-exclusion handling) so "chip" and
    "chips" resolve to whichever surface form the catalog actually uses.

    `min_confidence` — if the single best candidate found doesn't clear this
    floor, returns None instead (used by ProductIntentExtractor.extract() to
    apply PRODUCT_INTENT_LOW_CONFIDENCE; index-time per-document assignment
    in indexing/document_transformer.py calls this with the default 0.0 so it
    always stores its best-effort guess, deferring the tiering decision to
    query time).

    `high_confidence` — when provided (query time only; index-time labeling
    in document_transformer.py deliberately omits it, unaffected), resolves
    a real over-narrowing failure mode: comparing raw confidence alone lets a
    longer, coincidentally-high-purity compound (e.g. "whole wheat bread" at
    0.87, because that exact string is a tightly-defined SKU family in the
    catalog) beat a shorter candidate that is ALSO comfortably high-tier on
    its own (e.g. "bread" at 0.69) — even though the query's actual intent
    ("whole wheat" as a modifier on "bread") is better served by anchoring
    retrieval on the broad term and letting the modifier influence ranking,
    not hard-gate admission (see SearchV2Settings.PRODUCT_INTENT_HIGH_CONFIDENCE
    and retrieval/filters.py's product_type "filter" mode, which excludes any
    product whose own name/product_type doesn't contain the FULL resolved
    phrase — "whole wheat bread" would wrongly exclude a plain "Multigrain
    Bread" that's a perfectly good bread result). Once a candidate is already
    confidently at the SAME tier (both >= high_confidence, or both in
    [min_confidence, high_confidence)), a longer phrase's extra confidence
    is not buying a meaningfully more reliable signal — it's just a tighter
    substring match. So: among candidates reaching the same best tier as the
    single highest-confidence match, prefer the SHORTEST (most general);
    ties within that tier broken by highest confidence, deterministically.
    This never changes which TIER wins (a genuinely medium-only best match
    still loses to nothing, and a candidate that fails min_confidence is
    never promoted) — it only changes which candidate is picked once a tier
    is already decided, so it cannot make retrieval more permissive than
    today, only less spuriously narrow.

    Returns (matched_term, lexicon_entry, split_index) where split_index is
    the token index the match starts at (tokens[:split_index] are the
    modifiers) — or None if nothing cleared `min_confidence` (or nothing in
    the lexicon matched at all).
    """
    n = len(tokens)
    if n == 0 or not lexicon:
        return None

    candidates: List[Tuple[float, str, Dict[str, Any], int, int]] = []  # (confidence, term, entry, split_idx, n_words)
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
        candidates.append((confidence, matched_term, entry, split_idx, size))

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c[0])
    if best[0] < min_confidence:
        return None

    if high_confidence is not None:
        best_tier_floor = high_confidence if best[0] >= high_confidence else min_confidence
        same_tier = [c for c in candidates if c[0] >= best_tier_floor]
        # Shortest n_words wins; ties broken by highest confidence, then by
        # `candidates`' own (deterministic, size-descending) iteration order.
        best = min(same_tier, key=lambda c: (c[4], -c[0]))

    return best[1], best[2], best[3]


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
            # Only ever attempt the fuzzy fallback when `normalized` is NOT
            # already a real, catalog-attested term in its own right (i.e.
            # not already a key in the product-type lexicon). Without this
            # guard, an ordinary, correctly-spelled word can land within the
            # length-scaled edit-distance budget of an unrelated curated
            # alias purely by coincidence — e.g. "cheese" is 2 edits from the
            # curated Hindi alias "cheeku" (sapota/chikoo), which used to
            # hard-restrict a "cheese" search to sapota products (0 relevant
            # results). A genuine typo of a produce alias (the feature this
            # fallback exists for — see fuzzy_match_produce_alias()'s
            # docstring) is, by definition, NOT itself a term the catalog's
            # own product-name statistics already recognize, so this guard
            # only ever blocks false-positive collisions, never the intended
            # double-typo recovery case.
            if family is None and normalized not in self._lexicon:
                from search_v2.query_processing.canonical_produce import fuzzy_match_produce_alias
                family = fuzzy_match_produce_alias(normalized, self._produce_aliases)
            if family is not None:
                # Fresh Produce intent: retrieval hard-restricts to exactly
                # this family's real catalog ids (fresh_produce_ids) rather
                # than any text-based product_type signal — see
                # canonical_produce.py's module docstring for why a text
                # match (even an exact one) can't distinguish "Onion (Pyaz)"
                # from "Cream & Onion Chips", but an authoritative id
                # allowlist can. dominant_category stays None for the same
                # reason it did before this redesign: it isn't used when
                # fresh_produce_ids is set (see build_filter_clauses()).
                return ProductIntentResult(
                    primary_product=family.canonical_name,
                    modifiers=[],
                    confidence=1.0,
                    tier="high",
                    dominant_category=None,
                    fresh_produce_ids=family.member_ids,
                )

        if not self._lexicon:
            return ProductIntentResult()

        high = getattr(self._settings, "PRODUCT_INTENT_HIGH_CONFIDENCE", 0.55)
        low = getattr(self._settings, "PRODUCT_INTENT_LOW_CONFIDENCE", 0.25)

        # min_confidence=low: a candidate that can't even clear the LOW bar
        # isn't worth surfacing as a resolved intent at all — retrieval is
        # meant to be completely unaffected in that case (see tier="none").
        # high_confidence=high: query-time only (see resolve_head_term()'s
        # docstring) — prevents anchoring on an unnecessarily narrow compound
        # ("whole wheat bread") when the base term ("bread") is ALSO
        # comfortably in the same confidence tier on its own.
        resolved = resolve_head_term(tokens, self._lexicon, min_confidence=low, high_confidence=high)
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
        )
