"""
search_v2/retrieval/hybrid_query_builder.py
─────────────────────────────────────────────────
Builds the request for the "native_hybrid" fusion strategy — OpenSearch's
own `hybrid` query type, fusing lexical + semantic server-side via a
`normalization-processor` search pipeline. See retrieval/fusion.py's module
docstring for why this is an ALTERNATIVE strategy rather than the default
(RRF is) — short version: requires cluster-side pipeline setup and is tied to
this specific OpenSearch version's feature set (no native RRF until 2.19).

Reuses the inner clauses from lexical_query_builder.py rather than
duplicating them — the bool/should clauses built there for a single query
variant are exactly what becomes the "lexical" branch of the hybrid query.
Mirrors the same approach Search V1 used (see that project's
hybrid_search.py) for the function_score-vs-hybrid-query incompatibility:
OpenSearch's hybrid query doesn't reliably combine with function_score or
(by extension) the `boosting` query used for derivative demotion in the
lexical builder — so derivative demotion is NOT applied inside the
native_hybrid request itself. It still happens, just one step later, as part
of business ranking (next milestone) applied to the fused candidate list —
which is arguably the architecturally cleaner place for it anyway (the brief
itself frames business ranking as a separate post-retrieval step).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from search_v2.config.settings import SearchV2Settings, SETTINGS
from search_v2.embedding.embedding_service import EmbeddingService, get_embedding_service
from search_v2.query_processing.query_pipeline import ProcessedQuery
from search_v2.retrieval.lexical_query_builder import _field_match_clauses, build_filters
from search_v2.retrieval.semantic_query_builder import build_knn_clause


def build_native_hybrid_request(
    query: ProcessedQuery,
    filters: Optional[Dict[str, Any]] = None,
    size: Optional[int] = None,
    settings: Optional[SearchV2Settings] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> Optional[Dict[str, Any]]:
    """
    Returns (request_body, query_params) where query_params carries the
    `search_pipeline` name to pass to the client's search() call — e.g.
    `client.search(index=idx, body=request_body, **query_params)`.
    Returns None (the whole tuple) if the embedding model isn't available;
    callers should fall back to lexical-only in that case, same contract as
    semantic_query_builder.build_query().

    Only uses the FIRST query variant (the primary/best-guess text) — the
    multi-variant dis_max trick from the lexical builder doesn't combine
    cleanly with the hybrid query's two-sub-query structure (a hybrid query
    supports exactly the sub-queries you give it; nesting a dis_max-of-dis_max
    inside one branch works as a `bool` sub-query, per OpenSearch's own
    examples, but stacking N query variants there starts to fight the
    fusion's own scoring assumptions). RRF (the default strategy) doesn't have
    this limitation since each variant could in principle be issued as its
    own request — though for symmetry and simplicity, the RRF orchestrator
    (hybrid_search_orchestrator.py) also uses just the primary variant. If
    multi-variant fusion turns out to matter in practice, that's a clean,
    isolated follow-up.
    """
    settings = settings or SETTINGS
    service = embedding_service or get_embedding_service(settings.EMBEDDING_MODEL_KEY)
    text = query.primary_text()
    if not text:
        return None

    vector = service.embed_query(text)
    if vector is None:
        return None

    filter_clauses = build_filters(filters)
    lexical_subquery: Dict[str, Any] = {"bool": {"should": _field_match_clauses(text, settings)}}
    if filter_clauses:
        lexical_subquery["bool"]["filter"] = filter_clauses

    knn_clause = build_knn_clause(vector, k=settings.RETRIEVAL_K, filter_clauses=filter_clauses)
    semantic_subquery = {"knn": knn_clause}

    body = {
        "size": size if size is not None else settings.DEFAULT_RESULT_SIZE,
        "query": {"hybrid": {"queries": [lexical_subquery, semantic_subquery]}},
        "_source": {"excludes": ["text_vector", "text_vector_source", "vernacular_synonyms"]},
    }
    query_params = {"search_pipeline": settings.HYBRID_PIPELINE_NAME}
    return body, query_params
