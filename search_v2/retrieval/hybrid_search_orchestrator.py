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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from search_v2.config.settings import SearchV2Settings, SETTINGS
from search_v2.embedding.embedding_service import EmbeddingService
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


def _lexical_only(
    client: OpenSearchClient,
    query: ProcessedQuery,
    filters,
    size: int,
    settings: SearchV2Settings,
    fallback_reason: Optional[str] = None,
    sort_by: Optional[str] = None,
    offset: int = 0,
) -> HybridSearchResult:
    body = lexical_query_builder.build_query(query, filters, size, settings, sort_by=sort_by, offset=offset)
    response = client.search(body)
    hits = extract_hits(response)
    items = [
        ResultItem(doc_id=doc_id, source=source, fused_score=score, lexical_rank=rank, lexical_score=score)
        for rank, (doc_id, score, source) in enumerate(hits, start=1)
    ]
    return HybridSearchResult(items=items, strategy_used="lexical_only", lexical_ran=True, fallback_reason=fallback_reason)


def hybrid_search(
    client: OpenSearchClient,
    query: ProcessedQuery,
    filters=None,
    size: Optional[int] = None,
    settings: Optional[SearchV2Settings] = None,
    embedding_service: Optional[EmbeddingService] = None,
    sort_by: Optional[str] = None,
    offset: int = 0,
) -> HybridSearchResult:
    """
    Main hybrid-search entry point.

    `filters` accepts a legacy Dict (backward compatible) or a SearchFilters
    object (full filter support including must_not, PC signals, macros, etc.).
    When SearchFilters is passed, sort_by and offset are also read from it
    if not supplied as explicit kwargs.
    """
    settings = settings or SETTINGS
    final_size = size if size is not None else settings.DEFAULT_RESULT_SIZE

    # Extract sort/offset from SearchFilters if not overridden by kwargs
    _sort_by = sort_by
    _offset = offset
    if filters is not None:
        from search_v2.retrieval.filters import SearchFilters
        if isinstance(filters, SearchFilters):
            if _sort_by is None:
                _sort_by = filters.sort_by
            if _offset == 0:
                _offset = filters.offset

    if not settings.ENABLE_HYBRID or not settings.ENABLE_SEMANTIC or not settings.ENABLE_VECTOR_SEARCH:
        return _lexical_only(
            client, query, filters, final_size, settings,
            fallback_reason="hybrid/semantic disabled via settings",
            sort_by=_sort_by, offset=_offset,
        )

    strategy = settings.FUSION_STRATEGY

    if strategy == "native_hybrid":
        result = hybrid_query_builder.build_native_hybrid_request(
            query, filters, final_size, settings, embedding_service
        )
        if result is None:
            return _lexical_only(
                client, query, filters, final_size, settings,
                fallback_reason="embedding model unavailable",
                sort_by=_sort_by, offset=_offset,
            )
        body, query_params = result
        response = client.search(body, query_params)
        hits = extract_hits(response)
        items = [
            ResultItem(doc_id=doc_id, source=source, fused_score=score)
            for doc_id, score, source in hits
        ]
        return HybridSearchResult(items=items, strategy_used="native_hybrid", lexical_ran=True, semantic_ran=True)

    if strategy in ("rrf", "weighted"):
        retrieval_k = settings.RETRIEVAL_K

        semantic_body = semantic_query_builder.build_query(query, filters, retrieval_k, settings, embedding_service)
        if semantic_body is None:
            return _lexical_only(
                client, query, filters, final_size, settings,
                fallback_reason="embedding model unavailable",
                sort_by=_sort_by, offset=_offset,
            )

        # Retrieval phase uses full retrieval_k (no sort/offset at this stage)
        lexical_body = lexical_query_builder.build_query(query, filters, retrieval_k, settings)
        lexical_response = client.search(lexical_body)
        lexical_hits = extract_hits(lexical_response)

        semantic_response = client.search(semantic_body)
        semantic_hits = extract_hits(semantic_response)

        source_by_id: Dict[str, Dict[str, Any]] = {}
        for doc_id, _, source in lexical_hits + semantic_hits:
            source_by_id.setdefault(doc_id, source)

        lexical_ids = [h[0] for h in lexical_hits]
        lexical_scores = [h[1] for h in lexical_hits]
        semantic_ids = [h[0] for h in semantic_hits]
        semantic_scores = [h[1] for h in semantic_hits]

        if strategy == "rrf":
            fused = fusion.reciprocal_rank_fusion(
                [lexical_ids, semantic_ids],
                rank_constant=settings.RRF_RANK_CONSTANT,
                weights=settings.FUSION_WEIGHTS,
                raw_scores=[lexical_scores, semantic_scores],
            )
        else:
            fused = fusion.weighted_score_fusion(
                [list(zip(lexical_ids, lexical_scores)), list(zip(semantic_ids, semantic_scores))],
                weights=settings.FUSION_WEIGHTS,
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

        page_start = _offset
        page_end = page_start + final_size
        items = all_fused[page_start:page_end]

        return HybridSearchResult(items=items, strategy_used=strategy, lexical_ran=True, semantic_ran=True)

    raise ValueError(f"Unknown FUSION_STRATEGY: {strategy!r} (expected 'rrf', 'weighted', or 'native_hybrid')")


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
        "low_sugar": ("stats.sugar_penalty_percentiles.subcategory_percentile", False, _SENTINEL),
        "flean_score": ("flean_score.adjusted_score", True, -_SENTINEL),
    }

    _ALIASES: Dict[str, str] = {
        "price": "price_asc",
        "price_low_to_high": "price_asc",
        "price_high_to_low": "price_desc",
        "highest_flean": "quality",
        "highest_protein": "protein",
        "lowest_sugar": "low_sugar",
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
