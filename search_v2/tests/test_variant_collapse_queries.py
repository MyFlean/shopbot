from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.query_processing.query_pipeline import ProcessedQuery, QueryVariant
from search_v2.retrieval.listing import LISTING_COLLAPSE
from search_v2.retrieval.hybrid_query_builder import build_native_hybrid_request
from search_v2.retrieval.lexical_query_builder import build_query as build_lexical_query
from search_v2.retrieval.semantic_query_builder import build_query as build_semantic_query


class _FakeEmbeddingService:
    def embed_query(self, text: str):
        return [0.1, 0.2, 0.3]


def _pq(text: str = "chips") -> ProcessedQuery:
    return ProcessedQuery(
        raw_query=text,
        normalized_query=text,
        variants=[QueryVariant(text=text, is_correction=False, confidence=1.0)],
    )


def test_lexical_query_has_parent_collapse():
    body = build_lexical_query(_pq("chips"), filters=None, size=10)
    assert body["collapse"] == LISTING_COLLAPSE


def test_semantic_query_has_parent_collapse():
    body = build_semantic_query(_pq("chips"), filters=None, size=10, embedding_service=_FakeEmbeddingService())
    assert body is not None
    assert body["collapse"] == LISTING_COLLAPSE


def test_native_hybrid_query_has_parent_collapse():
    result = build_native_hybrid_request(_pq("chips"), filters=None, size=10, embedding_service=_FakeEmbeddingService())
    assert result is not None
    body, _params = result
    assert body["collapse"] == LISTING_COLLAPSE
