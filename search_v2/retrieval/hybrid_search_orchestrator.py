"""
search_v2/retrieval/hybrid_search_orchestrator.py
─────────────────────────────────────────────────────
The single entry point that ties lexical retrieval, semantic retrieval, and
fusion together into one call. Strategy-selectable
(SETTINGS.FUSION_STRATEGY: "rrf" | "weighted" | "native_hybrid") and
fallback-safe at every step — semantic/embedding unavailability degrades to
lexical-only rather than failing the request, the same resilience contract
used throughout this project (and in Search V1's hybrid_search.py before it).

This is also where per-component scores get attached to each result
(fused_score / lexical_score / semantic_score / ranks) — exactly what the
brief's playground milestone needs to display ("lexical score, semantic
score, hybrid score, final score"). Business ranking (next milestone) is
deliberately NOT done here — see HybridSearchResult.items, which carries
`fused_score` as the relevance-only signal business ranking will apply its
own bounded multiplier on top of, same separation-of-concerns the brief asks
for ("keep retrieval and business ranking independent").

Candidate pool vs. page size: hybrid_search() and its strategy branches
return the FULL retrieved candidate pool (sized via _pool_size(), driven by
settings.RETRIEVAL_K) — NOT a `size`-sliced page. This is deliberate:
business ranking (e.g. Flean score) needs a real pool of comparably-relevant
candidates to reorder within; if this function pre-sliced down to the
caller's requested page size (as it used to), business ranking would only
ever see whichever `size` items pure relevance-fusion already ranked first,
with no other candidates to promote in. Pagination (offset/size slicing) is
therefore the CALLER's responsibility, applied AFTER business ranking — see
search_v2/extension/search/core.py.

Product Intent Identification integration (see
query_processing/product_intent_extractor.py): when the caller's
SearchFilters carries a high-confidence product_type filter
(filters.product_type_mode == "filter"), this module does two things
differently, both gated purely on that one field so unfiltered/ambiguous
queries are completely unaffected:
  1. Pool size uses settings.PRODUCT_INTENT_MAX_POOL_SIZE instead of the
     ordinary settings.RETRIEVAL_K floor — "20 genuinely relevant products"
     or "100 genuinely relevant products" shouldn't get truncated at the
     everyday 75-candidate floor (see _pool_size()).
  2. Cascading relaxation: if the gated query comes back with ZERO results
     (a lexicon miss on an otherwise-valid query — the risk of any
     statistically-derived filter), hybrid_search() automatically retries
     once with the product-type filter stripped, so a bad Product Intent
     Identification guess degrades to "search behaves like it did before
     this feature existed" rather than "empty results page". This mirrors
     the exact fallback pattern already used elsewhere in this file for
     embedding-model unavailability.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from search_v2.config.settings import SearchV2Settings, SETTINGS
from search_v2.embedding.embedding_service import EmbeddingService
from search_v2.extension.shop_by_goal.constants import DERIVED_FIELD_SCRIPTS
from search_v2.query_processing.query_pipeline import ProcessedQuery
from search_v2.retrieval import fusion, hybrid_query_builder, lexical_query_builder, semantic_query_builder
from search_v2.retrieval.opensearch_client import OpenSearchClient, extract_hits


@dataclass
class ResultItem:
    doc_id: str
    source: Dict[str, Any]
    fused_score: float
    lexical_rank: Optional[int] = None
    lexical_score: Optional[float] = None
    semantic_rank: Optional[int] = None
    semantic_score: Optional[float] = None


@dataclass
class HybridSearchResult:
    items: List[ResultItem] = field(default_factory=list)
    strategy_used: str = "lexical_only"
    lexical_ran: bool = False
    semantic_ran: bool = False
    fallback_reason: Optional[str] = None
    # True when a high-confidence Product Intent Identification filter was
    # dropped because it produced zero results — see module docstring and
    # hybrid_search() below. Surfaced for observability (logging/playground),
    # not required by any caller.
    product_intent_relaxed: bool = False


def _retrieval_filters(filters):
    """Strip sort_by/offset off a SearchFilters before using it for a POOL
    retrieval call. lexical_query_builder.build_query() independently reads
    filters.sort_by/filters.offset and, if set, applies them AT THE ES QUERY
    LEVEL (an ES `sort` clause instead of relevance order, and `from` for
    pagination) — exactly the two things orchestration must own itself so
    sort_by/offset are applied exactly ONCE, in-memory, over the full fused
    pool (see module docstring). Without this, a request with an explicit
    sort_by/offset would have the retrieval step ALSO silently reorder/shift
    its window, retrieving the wrong slice of the catalog to fuse/rank over.
    All other filter fields (price, dietary, macros, ...) are left untouched.
    """
    from search_v2.retrieval.filters import SearchFilters
    if isinstance(filters, SearchFilters) and (filters.sort_by or filters.sort_order or filters.offset):
        return dataclasses.replace(filters, sort_by=None, sort_order=None, offset=0)
    return filters


def _pool_size(settings: SearchV2Settings, final_size: int, offset: int, expanded: bool = False) -> int:
    """How many candidates to actually retrieve, independent of the caller's
    requested PAGE size. Retrieval/ranking must see a real candidate pool
    (settings.RETRIEVAL_K) — not just the `final_size` the caller happens to
    want back — so a downstream ranking signal (business ranking / Flean
    score) has a genuine pool to reorder within rather than only ever seeing
    (and being powerless to change) whichever `final_size` items pure
    relevance-fusion already put first. Also widened to cover `offset +
    final_size` so a deep page request doesn't fall outside the pool.

    expanded=True (set only when a high-confidence Product Intent
    Identification filter is active — see module docstring) swaps the floor
    for settings.PRODUCT_INTENT_MAX_POOL_SIZE instead of settings.RETRIEVAL_K,
    since a hard product-type filter means every hit in the pool is already
    a genuine candidate — capping at the ordinary 75-candidate floor would
    silently truncate a real "100 relevant products" result down to 75."""
    floor = settings.PRODUCT_INTENT_MAX_POOL_SIZE if expanded else settings.RETRIEVAL_K
    return max(floor, offset + final_size)


def _adaptive_fusion_weights(routing_context, settings: SearchV2Settings) -> List[float]:
    default = settings.FUSION_WEIGHTS
    if routing_context is None:
        return default
    if getattr(routing_context, "health_intent_detected", False) or getattr(routing_context, "has_nutritional_constraint", False):
        return [0.4, 0.6]
    return default


def _wants_expanded_pool(filters) -> bool:
    """True only when filters carries an ACTIVE high-confidence product-type
    hard filter, OR a Fresh Produce id-restriction (see
    SearchFilters.product_ids) — both are gated retrieval that benefit from
    a larger candidate pool. Medium-confidence (should-boost) and no-intent
    queries are completely unaffected — pool sizing stays exactly as it was
    before this feature existed."""
    from search_v2.retrieval.filters import SearchFilters
    if not isinstance(filters, SearchFilters):
        return False
    if filters.product_ids:
        return True
    return filters.product_type_mode == "filter" and bool(filters.product_type)


def _is_too_many_cached_tokens_error(exc: Exception) -> bool:
    """True for the specific OpenSearch 400 this module works around — see
    _search_lexical() below and lexical_query_builder.build_query()'s
    include_bool_prefix docstring. Matched on message content rather than
    exception type alone (opensearch-py's RequestError is used for every
    kind of 400) so this can never accidentally swallow an unrelated
    failure — anything else always re-raises."""
    return "too many cached tokens" in str(exc).lower()


def _search_lexical(
    client: OpenSearchClient,
    query: ProcessedQuery,
    filters,
    pool_size: int,
    settings: SearchV2Settings,
    routing_context=None,
) -> Dict[str, Any]:
    """Build + execute the lexical query, with exactly one fallback retry:
    if OpenSearch rejects the query with "Too many cached tokens" (a
    cluster-side synonym-graph/shingle interaction — see
    lexical_query_builder.build_query()'s include_bool_prefix docstring for
    the full mechanism), retry once with the bool_prefix/_2gram/_3gram
    clause omitted. Generic by construction: triggers on the error itself,
    not on any particular query text, so it protects every query whose
    merged synonym expansion happens to be large enough to hit this,
    present or future, not just the one that surfaced it."""
    matched_phrases = getattr(routing_context, "health_intent_matched_phrases", ()) or ()
    body = lexical_query_builder.build_query(
        query, filters, pool_size, settings, health_intent_matched_phrases=matched_phrases
    )
    try:
        return client.search(body)
    except Exception as exc:
        if not _is_too_many_cached_tokens_error(exc):
            raise
        fallback_body = lexical_query_builder.build_query(
            query, filters, pool_size, settings, include_bool_prefix=False,
            health_intent_matched_phrases=matched_phrases,
        )
        return client.search(fallback_body)


def _lexical_only(
    client: OpenSearchClient,
    query: ProcessedQuery,
    filters,
    size: int,
    settings: SearchV2Settings,
    fallback_reason: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[List[Dict[str, str]]] = None,
    offset: int = 0,
    routing_context=None,
) -> HybridSearchResult:
    """Returns the FULL candidate pool (see _pool_size) — NOT sliced to a
    page. Pagination is the caller's responsibility, applied AFTER business
    ranking (see module docstring and hybrid_search())."""
    pool_size = _pool_size(settings, size, offset, expanded=_wants_expanded_pool(filters))
    response = _search_lexical(
        client, query, _retrieval_filters(filters), pool_size, settings, routing_context=routing_context
    )
    hits = extract_hits(response)
    items = [
        ResultItem(doc_id=doc_id, source=source, fused_score=score, lexical_rank=rank, lexical_score=score)
        for rank, (doc_id, score, source) in enumerate(hits, start=1)
    ]
    if sort_by and sort_by != "relevance":
        items = _apply_post_fusion_sort(items, sort_by)
    elif sort_order:
        items = _apply_post_fusion_sort_order(items, sort_order)
    return HybridSearchResult(items=items, strategy_used="lexical_only", lexical_ran=True, fallback_reason=fallback_reason)


def _hybrid_search_once(
    client: OpenSearchClient,
    query: ProcessedQuery,
    filters=None,
    size: Optional[int] = None,
    settings: Optional[SearchV2Settings] = None,
    embedding_service: Optional[EmbeddingService] = None,
    sort_by: Optional[str] = None,
    offset: int = 0,
    routing_context=None,
) -> HybridSearchResult:
    """
    One retrieval attempt — everything hybrid_search() used to do directly.
    Split out so hybrid_search() can call this a second time with the
    product-type filter relaxed if the first attempt comes back empty (see
    module docstring and hybrid_search() below).

    `filters` accepts a legacy Dict (backward compatible) or a SearchFilters
    object (full filter support including must_not, PC signals, macros, etc.).
    When SearchFilters is passed, sort_by and offset are also read from it
    if not supplied as explicit kwargs.
    """
    settings = settings or SETTINGS
    final_size = size if size is not None else settings.DEFAULT_RESULT_SIZE

    # Extract sort/offset from SearchFilters if not overridden by kwargs
    _sort_by = sort_by
    _sort_order: Optional[List[Dict[str, str]]] = None
    _offset = offset
    _is_search_filters = False
    if filters is not None:
        from search_v2.retrieval.filters import SearchFilters
        _is_search_filters = isinstance(filters, SearchFilters)
        if _is_search_filters:
            if _sort_by is None:
                _sort_by = filters.sort_by
            _sort_order = filters.sort_order
            if _offset == 0:
                _offset = filters.offset

    if not settings.ENABLE_HYBRID or not settings.ENABLE_SEMANTIC or not settings.ENABLE_VECTOR_SEARCH:
        return _lexical_only(
            client, query, filters, final_size, settings,
            fallback_reason="hybrid/semantic disabled via settings",
            sort_by=_sort_by, sort_order=_sort_order, offset=_offset, routing_context=routing_context,
        )

    # A filters/category-only request (no query text at all — e.g.
    # unified_search.py's filters-only branch) has nothing meaningful to
    # embed; skip semantic retrieval and let lexical_query_builder's
    # filter-only bool query (see build_query()) do the real retrieval.
    if not query.primary_text().strip():
        return _lexical_only(
            client, query, filters, final_size, settings,
            fallback_reason="empty query text — filters-only retrieval",
            sort_by=_sort_by, sort_order=_sort_order, offset=_offset, routing_context=routing_context,
        )

    if getattr(settings, "ENABLE_QUERY_ROUTER", True) and routing_context is not None:
        from search_v2.query_processing.query_router import route, LEXICAL_ONLY
        if route(routing_context, settings) == LEXICAL_ONLY:
            return _lexical_only(
                client, query, filters, final_size, settings,
                fallback_reason="query_router: LEXICAL_ONLY",
                sort_by=_sort_by, sort_order=_sort_order, offset=_offset, routing_context=routing_context,
            )

    strategy = settings.FUSION_STRATEGY
    expanded_pool = _wants_expanded_pool(filters)

    if strategy == "native_hybrid":
        pool_size = _pool_size(settings, final_size, _offset, expanded=expanded_pool)
        result = hybrid_query_builder.build_native_hybrid_request(
            query, filters, pool_size, settings, embedding_service
        )
        if result is None:
            return _lexical_only(
                client, query, filters, final_size, settings,
                fallback_reason="embedding model unavailable",
                sort_by=_sort_by, sort_order=_sort_order, offset=_offset, routing_context=routing_context,
            )
        body, query_params = result
        response = client.search(body, query_params)
        hits = extract_hits(response)
        items = [
            ResultItem(doc_id=doc_id, source=source, fused_score=score)
            for doc_id, score, source in hits
        ]
        if _sort_by and _sort_by != "relevance":
            items = _apply_post_fusion_sort(items, _sort_by)
        elif _sort_order:
            items = _apply_post_fusion_sort_order(items, _sort_order)
        return HybridSearchResult(items=items, strategy_used="native_hybrid", lexical_ran=True, semantic_ran=True)

    if strategy in ("rrf", "weighted"):
        retrieval_k = _pool_size(settings, final_size, _offset, expanded=expanded_pool)

        semantic_body = semantic_query_builder.build_query(
            query, filters, retrieval_k, settings, embedding_service, routing_context=routing_context
        )
        if semantic_body is None:
            return _lexical_only(
                client, query, filters, final_size, settings,
                fallback_reason="embedding model unavailable",
                sort_by=_sort_by, sort_order=_sort_order, offset=_offset, routing_context=routing_context,
            )

        # Retrieval phase uses full retrieval_k, always in relevance order
        # (sort_by/offset are stripped via _retrieval_filters() and applied
        # exactly once, in-memory, after fusion — see module docstring).
        lexical_response = _search_lexical(
            client, query, _retrieval_filters(filters), retrieval_k, settings, routing_context=routing_context
        )
        lexical_hits = extract_hits(lexical_response)

        semantic_response = client.search(semantic_body)
        semantic_hits = extract_hits(semantic_response)
        if settings.SEMANTIC_MIN_SCORE > 0:
            semantic_hits = [h for h in semantic_hits if h[1] >= settings.SEMANTIC_MIN_SCORE]

        source_by_id: Dict[str, Dict[str, Any]] = {}
        for doc_id, _, source in lexical_hits + semantic_hits:
            source_by_id.setdefault(doc_id, source)

        lexical_ids = [h[0] for h in lexical_hits]
        lexical_scores = [h[1] for h in lexical_hits]
        semantic_ids = [h[0] for h in semantic_hits]
        semantic_scores = [h[1] for h in semantic_hits]

        fusion_weights = _adaptive_fusion_weights(routing_context, settings)

        if strategy == "rrf":
            fused = fusion.reciprocal_rank_fusion(
                [lexical_ids, semantic_ids],
                rank_constant=settings.RRF_RANK_CONSTANT,
                weights=fusion_weights,
                raw_scores=[lexical_scores, semantic_scores],
            )
        else:
            fused = fusion.weighted_score_fusion(
                [list(zip(lexical_ids, lexical_scores)), list(zip(semantic_ids, semantic_scores))],
                weights=fusion_weights,
            )

        # Apply sort over fused results when requested (post-fusion in-memory sort)
        all_fused = [
            ResultItem(
                doc_id=r.doc_id, source=source_by_id.get(r.doc_id, {}), fused_score=r.fused_score,
                lexical_rank=r.lexical_rank, lexical_score=r.lexical_score,
                semantic_rank=r.semantic_rank, semantic_score=r.semantic_score,
            )
            for r in fused
        ]

        if _sort_by and _sort_by != "relevance":
            all_fused = _apply_post_fusion_sort(all_fused, _sort_by)
        elif _sort_order:
            all_fused = _apply_post_fusion_sort_order(all_fused, _sort_order)

        # NOT sliced to a page here — see _pool_size()/module docstring.
        # Pagination is applied by the caller AFTER business ranking.
        return HybridSearchResult(items=all_fused, strategy_used=strategy, lexical_ran=True, semantic_ran=True)

    raise ValueError(f"Unknown FUSION_STRATEGY: {strategy!r} (expected 'rrf', 'weighted', or 'native_hybrid')")


def _relax_product_type_filter(filters):
    """Return a copy of `filters` with the Product Intent Identification
    filter fields cleared — used ONLY as a fallback when a high-confidence
    product-type filter produced zero results (see hybrid_search() below).
    Mirrors _retrieval_filters()'s dataclasses.replace() pattern above."""
    return dataclasses.replace(filters, product_type=None, product_type_mode=None, product_type_category=None)


def _relax_product_ids_filter(filters):
    return dataclasses.replace(
        filters, product_ids=None, product_ids_exact=False,
        product_type=None, product_type_mode=None, product_type_category=None,
    )


def hybrid_search(
    client: OpenSearchClient,
    query: ProcessedQuery,
    filters=None,
    size: Optional[int] = None,
    settings: Optional[SearchV2Settings] = None,
    embedding_service: Optional[EmbeddingService] = None,
    sort_by: Optional[str] = None,
    offset: int = 0,
    routing_context=None,
) -> HybridSearchResult:
    """
    Main hybrid-search entry point — thin wrapper around _hybrid_search_once()
    that adds cascading relaxation for Product Intent Identification (see
    module docstring): if a high-confidence product-type hard filter comes
    back with zero results, retry once with that filter removed rather than
    returning an empty page. Every other call shape/behavior is identical to
    _hybrid_search_once() and thus unchanged from before this feature existed.

    settings.STRICT_ZERO_RESULTS (default False, preserves the behavior above
    exactly) is a business-policy override: when True, a genuinely
    zero-result gated query returns zero products instead of retrying without
    the filter — for a strict, unambiguous product query ("granola bar under
    5 calories") an empty page is the CORRECT answer; falling back to
    unrelated/unfiltered results is worse than returning nothing. This does
    not touch confidence, product_type, filter construction, or how/why the
    gated attempt came back empty — it only decides whether the existing
    retry happens.

    A Fresh Produce id-restriction (filters.product_ids — see
    SearchFilters.product_ids) is NEVER relaxed, regardless of
    STRICT_ZERO_RESULTS: "no fresh onion currently in stock" must stay an
    empty result, not silently fall back to cream & onion chips just because
    they contain the same word — that's the entire point of the id
    allowlist. _relax_product_type_filter() only ever clears product_type*
    fields, so even if this condition's other checks passed, a retry would
    still carry product_ids and still return zero — this check just skips
    that wasted round-trip explicitly.
    """
    settings = settings or SETTINGS
    result = _hybrid_search_once(
        client, query, filters, size, settings, embedding_service, sort_by, offset,
        routing_context=routing_context,
    )

    from search_v2.retrieval.filters import SearchFilters
    has_product_ids = isinstance(filters, SearchFilters) and bool(filters.product_ids)
    has_exact_product_ids = has_product_ids and filters.product_ids_exact
    has_fuzzy_product_ids = has_product_ids and not filters.product_ids_exact

    if (
        getattr(settings, "ENABLE_PRODUCT_INTENT_RELAXATION", True)
        and not has_exact_product_ids
        and (has_fuzzy_product_ids or not getattr(settings, "STRICT_ZERO_RESULTS", False))
        and _wants_expanded_pool(filters)
        and not result.items
    ):
        relaxed_filters = _relax_product_ids_filter(filters) if has_product_ids else _relax_product_type_filter(filters)
        result = _hybrid_search_once(client, query, relaxed_filters, size, settings, embedding_service, sort_by, offset)
        result.product_intent_relaxed = True

    return result


def _source_value(source: Dict[str, Any], dotted: str) -> Any:
    value: Any = source
    for key in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _derived_sort_value(item: ResultItem, field_name: str) -> Optional[float]:
    if field_name not in DERIVED_FIELD_SCRIPTS:
        return None
    energy = _to_float(_source_value(item.source, "category_data.nutritional.nutri_breakdown.energy kcal")) or 0.0
    protein = _to_float(_source_value(item.source, "category_data.nutritional.nutri_breakdown.protein g")) or 0.0
    fat = _to_float(_source_value(item.source, "category_data.nutritional.nutri_breakdown.total fat g")) or 0.0
    carbs = _to_float(_source_value(item.source, "category_data.nutritional.nutri_breakdown.carbohydrate g")) or 0.0
    fiber = _to_float(_source_value(item.source, "category_data.nutritional.nutri_breakdown.fiber g")) or 0.0

    if field_name == "derived.protein_cal_pct":
        return 0.0 if energy <= 0.0 else ((protein * 4.0) / energy) * 100.0
    if field_name == "derived.fat_cal_pct":
        return 0.0 if energy <= 0.0 else ((fat * 9.0) / energy) * 100.0
    if field_name == "derived.net_carbs":
        return carbs - fiber
    if field_name == "derived.micronutrient_count":
        micronutrient_fields = (
            "category_data.nutritional.nutri_breakdown.vitamin a mcg",
            "category_data.nutritional.nutri_breakdown.vitamin c",
            "category_data.nutritional.nutri_breakdown.zinc",
            "category_data.nutritional.nutri_breakdown.iron mg",
            "category_data.nutritional.nutri_breakdown.folate dfe mcg",
            "category_data.nutritional.nutri_breakdown.vitamin b6 mg",
        )
        count = 0
        for nutrient_path in micronutrient_fields:
            nutrient_value = _to_float(_source_value(item.source, nutrient_path))
            if nutrient_value is not None:
                count += 1
        return float(count)
    return None


def _sort_value_for_field(item: ResultItem, field_name: str) -> Any:
    if field_name == "_score":
        return item.fused_score
    if field_name.startswith("derived."):
        return _derived_sort_value(item, field_name)
    return _source_value(item.source, field_name)


def _comparable_sort_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return value.lower()
    return str(value)


def _apply_post_fusion_sort_order(
    items: List[ResultItem],
    sort_order: Optional[List[Dict[str, str]]],
) -> List[ResultItem]:
    """Apply stable multi-key sort over fused results using goal sort_order."""
    if not sort_order:
        return items
    sorted_items = list(items)
    for entry in reversed(sort_order):
        field_name = str((entry or {}).get("field") or "").strip()
        order = str((entry or {}).get("order") or "").strip().lower()
        if not field_name or order not in {"asc", "desc"}:
            continue
        reverse = order == "desc"

        def _key(it: ResultItem):
            raw = _sort_value_for_field(it, field_name)
            if raw is None:
                return (-1, None) if reverse else (1, None)
            return (0, _comparable_sort_value(raw))

        sorted_items = sorted(sorted_items, key=_key, reverse=reverse)
    return sorted_items


def _apply_post_fusion_sort(items: List[ResultItem], sort_by: str) -> List[ResultItem]:
    """Apply an in-memory sort over fused results by a named field."""
    _SENTINEL = float("inf")

    def _get_field(item: ResultItem, dotted: str):
        val = item.source
        for k in dotted.split("."):
            if not isinstance(val, dict):
                return None
            val = val.get(k)
        return val

    sort_key_map = {
        "price_asc": ("price", False, _SENTINEL),
        "price_desc": ("price", True, -_SENTINEL),
        "quality": ("stats.adjusted_score_percentiles.subcategory_percentile", True, -_SENTINEL),
        "protein": ("stats.protein_percentiles.subcategory_percentile", True, -_SENTINEL),
        "fiber": ("stats.fiber_percentiles.subcategory_percentile", True, -_SENTINEL),
        "fat": ("stats.total_fat_penalty_percentiles.subcategory_percentile", False, _SENTINEL),
        "low_sugar": ("stats.sugar_penalty_percentiles.subcategory_percentile", False, _SENTINEL),
        "flean_score": ("flean_score.adjusted_score", True, -_SENTINEL),
    }

    # NOTE: "protein_desc"/"fiber_desc"/"fat_asc"/"flean_score_desc" are the
    # ACTUAL sort_by values the public API accepts (see
    # shopping_bot/routes/product_api.py's VALID_SORT_OPTIONS) — without
    # these aliases, requesting them silently no-ops (falls through to plain
    # relevance order) because this module's own vocabulary never matched
    # what the API surface actually sends.
    _ALIASES: Dict[str, str] = {
        "price": "price_asc",
        "price_low_to_high": "price_asc",
        "price_high_to_low": "price_desc",
        "highest_flean": "quality",
        "highest_protein": "protein",
        "lowest_sugar": "low_sugar",
        "protein_desc": "protein",
        "fiber_desc": "fiber",
        "fibre_desc": "fiber",
        "fat_asc": "fat",
        "flean_score_desc": "quality",
    }
    key = sort_by.lower().strip()
    key = _ALIASES.get(key, key)
    spec = sort_key_map.get(key)
    if spec is None:
        return items

    field_path, reverse, missing_val = spec
    return sorted(
        items,
        key=lambda it: (
            _get_field(it, field_path)
            if _get_field(it, field_path) is not None
            else missing_val
        ),
        reverse=reverse,
    )
