"""
search_v2/query_processing/query_pipeline.py
─────────────────────────────────────────────────
Full Search V2 query-processing pipeline:

    raw query
        → text normalization
        → typo correction / segmentation repair
        → natural-language filter extraction      (deterministic, no LLMs)
        → ProcessedQuery  +  SearchFilters

The two outputs travel together as a SearchRequest through the rest of the
system. Every client (ShopBot gateway, REST API, Flutter app) calls
process_search_request() and receives both objects — there is no NLP anywhere
in the gateway or ShopBot.

ProcessedQuery carries every "variant" of the query worth searching for —
the original text plus any corrected/repaired variant — so the lexical query
builder can search across all of them via dis_max rather than committing to
a single "best guess" rewrite.

Synonym expansion is NOT done here — it happens inside OpenSearch via the
synonym_graph search-time analyzer already wired into the mapping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from search_v2.query_processing.text_normalization import normalize_text
from search_v2.query_processing.typo_correction import QueryCorrectionResult, VocabularyCorrector

if TYPE_CHECKING:
    from search_v2.retrieval.filters import SearchFilters
    from search_v2.query_processing.product_intent_extractor import ProductIntentExtractor, ProductIntentResult
    from search_v2.query_processing.health_intent_classifier import HealthIntentResult
    from search_v2.query_processing.routing_context import RoutingContext


_MAX_MERGE_WORDS = 3


def _merged_variant_text(normalized: str) -> Optional[str]:
    words = [w for w in re.split(r"[\s\-]+", normalized) if w]
    if not (2 <= len(words) <= _MAX_MERGE_WORDS):
        return None
    merged = "".join(words)
    return merged if merged != normalized else None


@dataclass
class QueryVariant:
    text: str
    is_correction: bool
    confidence: float  # 0-1, used by the lexical query builder to weight this variant's `should` clause


@dataclass
class ProcessedQuery:
    raw_query: str
    normalized_query: str
    variants: List[QueryVariant] = field(default_factory=list)
    correction_result: Optional[QueryCorrectionResult] = None

    def primary_text(self) -> str:
        """The single best-guess text, for fields/contexts that can only take
        one query string (e.g. the embedding model in the semantic milestone)."""
        corrected = [v for v in self.variants if v.is_correction]
        return corrected[0].text if corrected else self.normalized_query

    def has_corrections(self) -> bool:
        return any(v.is_correction for v in self.variants)


@dataclass
class SearchRequest:
    """
    The complete output of the Search V2 query-processing pipeline.

    processed_query — cleaned, typo-corrected text ready for lexical/semantic
                      retrieval.
    filters         — unified SearchFilters combining:
                        • structured filters from the caller (UI, ShopBot, API)
                        • filters extracted deterministically from natural language
                        • the product-type filter/boost resolved by Product
                          Intent Identification (see product_intent below)
                      Both lexical and semantic builders consume this one object.
    product_intent  — the raw ProductIntentResult (primary_product, modifiers,
                      confidence, tier), kept alongside `filters` purely for
                      observability/debugging (logging, playground display) —
                      retrieval itself only ever reads `filters`. None when
                      Product Intent Identification is disabled, its lexicon
                      is unavailable, or nothing in the query resolved.
    health_intent   — HealthIntentResult from health_intent_classifier.py,
                      computed independently of filters/product_intent.
                      Never affects retrieval directly.
    routing_context — RoutingContext, package of routing-only signals for
                      query_router.py. Never affects retrieval; retrieval
                      only ever reads `filters`.
    """
    processed_query: ProcessedQuery
    filters: "SearchFilters"
    product_intent: Optional["ProductIntentResult"] = None
    health_intent: Optional["HealthIntentResult"] = None
    routing_context: Optional["RoutingContext"] = None


def process_query(
    raw_query: str,
    corrector: Optional[VocabularyCorrector] = None,
    enable_typo_correction: bool = True,
) -> ProcessedQuery:
    """Low-level helper: text processing only, no filter extraction.
    Kept for backward compatibility with tests and callers that supply
    filters separately. Prefer process_search_request() for new code."""
    normalized = normalize_text(raw_query)
    variants = [QueryVariant(text=normalized, is_correction=False, confidence=1.0)]

    merged_variant = _merged_variant_text(normalized)
    if merged_variant:
        variants.append(QueryVariant(text=merged_variant, is_correction=False, confidence=1.0))

    correction_result: Optional[QueryCorrectionResult] = None
    if enable_typo_correction and corrector is not None and normalized:
        correction_result = corrector.correct_query(normalized)

        if correction_result.has_any_correction():
            # corrected_tokens() already folds in BOTH per-token corrections
            # AND segmentation repairs (merged-span replacements) into one
            # coherent token list — see typo_correction.py. A single variant
            # built from it preserves every other token in the query (unlike
            # previously emitting a segmentation repair's merged token alone,
            # which silently dropped the rest of the query, e.g. "gluten free
            # chips" -> just "glutenfree").
            corrected_text = " ".join(correction_result.corrected_tokens())
            if corrected_text != normalized:
                distances = [c.distance for c in correction_result.corrections.values()]
                distances += [r.distance for r in correction_result.segmentation_repairs]
                max_dist = max(distances) if distances else 0
                confidence = {0: 1.0, 1: 0.85, 2: 0.65}.get(max_dist, 0.5)
                variants.append(QueryVariant(text=corrected_text, is_correction=True, confidence=confidence))

    return ProcessedQuery(
        raw_query=raw_query,
        normalized_query=normalized,
        variants=variants,
        correction_result=correction_result,
    )


def process_search_request(
    raw_query: str,
    explicit_filters: Optional["SearchFilters"] = None,
    corrector: Optional[VocabularyCorrector] = None,
    enable_typo_correction: bool = True,
    enable_nl_filters: bool = True,
    product_intent_extractor: Optional["ProductIntentExtractor"] = None,
    settings=None,
) -> SearchRequest:
    """
    Full query-processing pipeline entry point.

    1. Run NL filter extraction on raw_query (deterministic, no LLMs).
    2. Process the clean query through normalization + typo correction.
    3. If typo correction changed the query, re-run NL filter extraction on
       the corrected text and merge in anything newly found (see below).
    4. Product Intent Identification: resolve the head product term out of
       the fully cleaned/corrected text and fold it into filters as either a
       hard filter (high confidence) or a should-boost (medium confidence) —
       see below.
    5. Merge extracted filters with any explicit_filters from the caller.
    6. Return SearchRequest(processed_query, merged_filters, product_intent).

    The gateway and every other client should call this function — it ensures
    all clients benefit from the same query understanding pipeline.

    Why step 3 exists: NL filter extraction is regex-based and only matches
    correctly-spelled phrases. A misspelled dietary/macro/price phrase (e.g.
    "glutten free chips", "suger free cookies", "protien bars") fails those
    regexes on the raw text, so step 1 alone would silently miss the filter a
    correctly-spelled version of the same query gets — the modifier would
    survive only as free text instead of becoming a real filter. Re-running
    extraction once more on the corrected text closes that gap generically,
    for every dietary label / macro-nutrient / price phrase the extractor
    already knows how to recognize, not just specific words.

    Why step 4 runs where it does: it needs text that's already typo-corrected
    AND has had dietary/macro/price phrases stripped out by NL filter
    extraction — otherwise a misspelled or filter-phrase word could get
    mistaken for (or crowd out) the actual product head term. See
    query_processing/product_intent_extractor.py for the resolution algorithm
    and indexing/product_type_lexicon_builder.py (search repo) for how the
    lexicon it reads is built.

    Parameters
    ----------
    raw_query        : The user's raw text input.
    explicit_filters : Structured filters from the caller (UI sliders, ShopBot
                       params, REST API body). These are merged with NL-extracted
                       filters; caller values take precedence for scalar fields
                       (price, category) while list fields are combined.
    corrector        : Optional VocabularyCorrector for typo correction.
    enable_typo_correction : Whether to run typo/segmentation correction.
    enable_nl_filters : Whether to run NL filter extraction. Defaults to True
                        (controlled by SETTINGS.ENABLE_NL_FILTER_EXTRACTION).
    product_intent_extractor : Optional ProductIntentExtractor. None (the
                        default) makes step 4 a complete no-op — identical
                        behavior to before this feature existed.
    settings         : SearchV2Settings instance; falls back to module SETTINGS.
    """
    from search_v2.retrieval.filters import SearchFilters, merge_filters

    if settings is None:
        from search_v2.config.settings import SETTINGS
        settings = SETTINGS

    nl_filters_enabled = enable_nl_filters and getattr(settings, "ENABLE_NL_FILTER_EXTRACTION", True)

    # Step 0: Health Intent classification — runs on the RAW query text,
    # independently of NL filter extraction/typo correction/Product Intent.
    # Must run on raw_query specifically: NL filter extraction can strip a
    # recognized macro phrase (e.g. "high protein") out of clean_query
    # entirely, which would make it invisible to a classifier running later.
    health_intent = None
    if getattr(settings, "ENABLE_HEALTH_INTENT", True) and raw_query.strip():
        from search_v2.query_processing.health_intent_classifier import classify_health_intent
        health_intent = classify_health_intent(raw_query)

    # Step 1: NL filter extraction on the raw query
    clean_query = raw_query
    nl_filters = SearchFilters()
    if nl_filters_enabled and raw_query.strip():
        from search_v2.query_processing.nl_filter_extractor import NLFilterExtractor
        nl_result = NLFilterExtractor(settings).extract(raw_query)
        clean_query = nl_result.clean_query
        nl_filters = nl_result.filters

    # Step 2: Text pipeline (normalization + typo correction) on the clean query
    processed = process_query(
        clean_query,
        corrector=corrector,
        enable_typo_correction=enable_typo_correction,
    )

    # Step 3: Re-run NL filter extraction on the corrected text, if typo
    # correction actually changed anything. Merge any newly-found filters in
    # (pass-1 findings win on scalar overlap — they came from the user's
    # literal text, not a guessed correction). If the second pass strips
    # more text out (the newly-recognized modifier), rebuild `processed` on
    # that further-cleaned text so retrieval gets a clean head-term variant.
    if nl_filters_enabled and processed.has_corrections():
        from search_v2.query_processing.nl_filter_extractor import NLFilterExtractor
        corrected_text = processed.primary_text()
        nl_result_2 = NLFilterExtractor(settings).extract(corrected_text)
        nl_filters = merge_filters(nl_result_2.filters, nl_filters)
        if nl_result_2.clean_query != corrected_text:
            processed = process_query(
                nl_result_2.clean_query,
                corrector=corrector,
                enable_typo_correction=enable_typo_correction,
            )

    # Step 4: Product Intent Identification — operates on the final cleaned,
    # typo-corrected head-term text. High confidence becomes a hard retrieval
    # filter; medium confidence becomes a strong should-boost only; low/no
    # match leaves filters untouched (today's behavior).
    product_intent = None
    intent_enabled = getattr(settings, "ENABLE_PRODUCT_INTENT", True)
    if intent_enabled and product_intent_extractor is not None:
        head_text = processed.primary_text()
        if head_text.strip():
            product_intent = product_intent_extractor.extract(head_text)
            if product_intent.primary_product and product_intent.tier in ("high", "medium"):
                intent_filters = SearchFilters(
                    product_type=product_intent.primary_product,
                    product_type_mode="filter" if product_intent.tier == "high" else "boost",
                    product_type_category=product_intent.dominant_category,
                    # Fresh Produce Identification (see canonical_produce.py
                    # / SearchFilters.product_ids) — only ever non-empty
                    # alongside tier=="high", set by an exact curated
                    # vernacular produce match. Hard-restricts retrieval to
                    # this family's real catalog ids, replacing the
                    # text-based product_type clause entirely.
                    product_ids=(
                        list(product_intent.fresh_produce_ids)
                        if product_intent.fresh_produce_ids else None
                    ),
                )
                nl_filters = merge_filters(intent_filters, nl_filters)

    # Step 5: Merge explicit + NL/intent-extracted filters (explicit wins)
    base = explicit_filters or SearchFilters()
    merged = merge_filters(nl_filters, base)   # base values win on overlap

    from search_v2.query_processing.routing_context import build_routing_context
    routing_context = build_routing_context(product_intent, health_intent, merged)

    return SearchRequest(
        processed_query=processed,
        filters=merged,
        product_intent=product_intent,
        health_intent=health_intent,
        routing_context=routing_context,
    )
