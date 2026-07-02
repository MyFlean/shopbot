"""
search_v2/retrieval/semantic_query_builder.py
─────────────────────────────────────────────────
Builds the OpenSearch kNN query for Search V2's semantic retrieval. Mirrors the
structure of retrieval/lexical_query_builder.py so the two compose cleanly in
the hybrid milestone: same filter-building function, same settings object,
same "return None and let the caller fall back" contract when something
upstream (the embedding model) isn't available.

No LLM. Embeddings come from embedding/embedding_service.py, model selected
via search_v2.config.SETTINGS.EMBEDDING_MODEL_KEY (see
embedding/model_registry.py for the candidate models and the reasoning behind
the current default).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from search_v2.config.settings import SearchV2Settings, SETTINGS
from search_v2.embedding.embedding_service import EmbeddingService, get_embedding_service
from search_v2.query_processing.query_pipeline import ProcessedQuery
from search_v2.retrieval.lexical_query_builder import build_filters


def build_knn_clause(
    query_vector: List[float],
    k: int,
    filter_clauses: Optional[List[Dict[str, Any]]] = None,
    must_not_clauses: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The raw `knn` clause. Filters are pre-filters applied INSIDE the kNN
    search (pruning the HNSW candidate set before distance ranking) rather
    than after — meaningfully different from filtering AFTER retrieval, which
    could return fewer than `k` results post-filter. See OpenSearch's k-NN
    plugin docs for why a kNN-native filter (vs. a wrapping bool/filter) is the
    right tool here."""
    clause: Dict[str, Any] = {"vector": query_vector, "k": k}
    if filter_clauses or must_not_clauses:
        knn_filter: Dict[str, Any] = {"bool": {}}
        if filter_clauses:
            knn_filter["bool"]["filter"] = filter_clauses
        if must_not_clauses:
            knn_filter["bool"]["must_not"] = must_not_clauses
        clause["filter"] = knn_filter
    return {"text_vector": clause}


def build_query(
    query: ProcessedQuery,
    filters=None,
    size: Optional[int] = None,
    settings: Optional[SearchV2Settings] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> Optional[Dict[str, Any]]:
    """
    Returns None (never raises) if the embedding model isn't available or the
    query has no usable text — callers should treat None as "run lexical-only."

    `filters` accepts either a legacy Dict or a SearchFilters object.
    """
    settings = settings or SETTINGS
    if not settings.ENABLE_SEMANTIC or not settings.ENABLE_VECTOR_SEARCH:
        return None

    service = embedding_service or get_embedding_service(settings.EMBEDDING_MODEL_KEY)
    text = query.primary_text()
    if not text:
        return None

    vector = service.embed_query(text)
    if vector is None:
        return None

    # Resolve filter clauses — support both legacy dict and SearchFilters
    filter_clauses: List[Dict[str, Any]] = []
    must_not_clauses: List[Dict[str, Any]] = []
    if filters is not None:
        from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
        if isinstance(filters, SearchFilters):
            fc = build_filter_clauses(filters)
            filter_clauses = fc.filter_clauses
            must_not_clauses = fc.must_not_clauses
        else:
            filter_clauses = build_filters(filters)

    k = settings.RETRIEVAL_K
    knn = build_knn_clause(vector, k=k, filter_clauses=filter_clauses or None, must_not_clauses=must_not_clauses or None)

    return {
        "size": size if size is not None else settings.DEFAULT_RESULT_SIZE,
        "query": {"knn": knn},
        "_source": {"excludes": ["text_vector", "text_vector_source", "vernacular_synonyms"]},
    }


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Plain-Python cosine similarity — used by the playground/benchmark tools
    to display or recompute a semantic similarity score outside of OpenSearch
    (e.g. comparing two candidate embedding models' outputs offline). Assumes
    inputs may not be pre-normalized."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
