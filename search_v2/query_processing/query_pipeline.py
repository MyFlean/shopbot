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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from search_v2.query_processing.text_normalization import normalize_text
from search_v2.query_processing.typo_correction import QueryCorrectionResult, VocabularyCorrector

if TYPE_CHECKING:
    from search_v2.retrieval.filters import SearchFilters


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
                      Both lexical and semantic builders consume this one object.
    """
    processed_query: ProcessedQuery
    filters: "SearchFilters"


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

    correction_result: Optional[QueryCorrectionResult] = None
    if enable_typo_correction and corrector is not None and normalized:
        correction_result = corrector.correct_query(normalized)

        if correction_result.corrections:
            corrected_text = " ".join(correction_result.corrected_tokens())
            if corrected_text != normalized:
                max_dist = max(c.distance for c in correction_result.corrections.values())
                confidence = {0: 1.0, 1: 0.85, 2: 0.65}.get(max_dist, 0.5)
                variants.append(QueryVariant(text=corrected_text, is_correction=True, confidence=confidence))

        for repair in correction_result.segmentation_repairs:
            confidence = {0: 1.0, 1: 0.8, 2: 0.6}.get(repair.distance, 0.5)
            variants.append(QueryVariant(text=repair.corrected, is_correction=True, confidence=confidence))

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
    settings=None,
) -> SearchRequest:
    """
    Full query-processing pipeline entry point.

    1. Run NL filter extraction on raw_query (deterministic, no LLMs).
    2. Merge extracted filters with any explicit_filters from the caller.
    3. Process the clean query through normalization + typo correction.
    4. Return SearchRequest(processed_query, merged_filters).

    The gateway and every other client should call this function — it ensures
    all clients benefit from the same query understanding pipeline.

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
    settings         : SearchV2Settings instance; falls back to module SETTINGS.
    """
    from search_v2.retrieval.filters import SearchFilters, merge_filters

    if settings is None:
        from search_v2.config.settings import SETTINGS
        settings = SETTINGS

    # Step 1: NL filter extraction
    clean_query = raw_query
    nl_filters = SearchFilters()
    if enable_nl_filters and getattr(settings, "ENABLE_NL_FILTER_EXTRACTION", True) and raw_query.strip():
        from search_v2.query_processing.nl_filter_extractor import NLFilterExtractor
        nl_result = NLFilterExtractor(settings).extract(raw_query)
        clean_query = nl_result.clean_query
        nl_filters = nl_result.filters

    # Step 2: Merge explicit + NL-extracted filters (explicit takes precedence)
    base = explicit_filters or SearchFilters()
    merged = merge_filters(nl_filters, base)   # base values win on overlap

    # Step 3: Text pipeline on the clean query
    processed = process_query(
        clean_query,
        corrector=corrector,
        enable_typo_correction=enable_typo_correction,
    )

    return SearchRequest(processed_query=processed, filters=merged)
