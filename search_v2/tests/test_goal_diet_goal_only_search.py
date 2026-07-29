from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.config.settings import SETTINGS
from search_v2.goal_diet.goal_only_retrieval import (
    filter_only_processed_query,
    is_goal_only_retrieval,
)
from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.query_processing.health_intent_classifier import classify_health_intent
from search_v2.query_processing.query_pipeline import ProcessedQuery, QueryVariant, process_search_request
from search_v2.query_processing.typo_correction import VocabularyCorrector
from search_v2.query_processing.vocabulary_builder import load_vocabulary, seed_vocabulary, VOCABULARY_PATH
from search_v2.query_processing.product_intent_extractor import (
    ProductIntentExtractor,
    load_product_type_lexicon,
    PRODUCT_TYPE_LEXICON_PATH,
)
from search_v2.query_processing.canonical_produce import load_produce_alias_map, PRODUCE_SYNONYMS_PATH
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
from search_v2.retrieval.hybrid_search_orchestrator import hybrid_search


@pytest.fixture(autouse=True)
def fresh_registry():
    get_compiled_registry(force_reload=True)
    yield


def _pipeline(query: str):
    vocab = load_vocabulary(VOCABULARY_PATH) or seed_vocabulary()
    corrector = VocabularyCorrector(vocab)
    lexicon = load_product_type_lexicon(PRODUCT_TYPE_LEXICON_PATH)
    extractor = ProductIntentExtractor(
        lexicon, settings=SETTINGS, produce_aliases=load_produce_alias_map(PRODUCE_SYNONYMS_PATH)
    )
    return process_search_request(
        query,
        corrector=corrector,
        product_intent_extractor=extractor,
        settings=SETTINGS,
    )


def _processed(text: str) -> ProcessedQuery:
    return ProcessedQuery(
        raw_query=text,
        normalized_query=text,
        variants=[QueryVariant(text=text, is_correction=False, confidence=1.0)],
    )


@pytest.mark.parametrize(
    "query,expected",
    [
        ("keto", True),
        ("vegan", True),
        ("muscle gain", True),
        ("heart healthy", True),
        ("gluten free", True),
        ("keto bread", False),
        ("low sugar biscuits", False),
        ("heart healthy foods", False),
        ("potato chips", False),
    ],
)
def test_is_goal_only_retrieval_detection(query, expected):
    req = _pipeline(query)
    assert is_goal_only_retrieval(req.processed_query, req.filters, req.routing_context) is expected


def test_filter_only_processed_query_clears_variants():
    pq = _processed("keto")
    empty = filter_only_processed_query(pq)
    assert empty.primary_text().strip() == ""
    assert all(not v.text.strip() for v in empty.variants)


def _sample_hit(doc_id: str = "doc1"):
    return {
        "_id": doc_id,
        "_score": 1.0,
        "_source": {"id": doc_id, "name": "Sample Product", "category_group": "f_and_b"},
    }


class _FilterOnlyMockClient:
    """Returns hits for filter-only bodies, empty for text-match bodies."""

    def __init__(self):
        self.bodies = []

    def search(self, body, query_params=None):
        self.bodies.append(body)
        query = body.get("query") or {}
        if _body_is_filter_only(query):
            return {"hits": {"hits": [_sample_hit(f"hit-{len(self.bodies)}")]}}
        return {"hits": {"hits": []}}


def _body_is_filter_only(query: dict) -> bool:
    if not query:
        return False
    if "match_all" in query:
        return True
    if "boosting" in query:
        return _body_is_filter_only(query["boosting"].get("positive") or {})
    bool_q = query.get("bool") or {}
    if bool_q.get("filter") and not bool_q.get("minimum_should_match") and not bool_q.get("should"):
        return True
    return False


@pytest.mark.parametrize("query", ["keto", "vegan", "muscle gain", "heart healthy", "gluten free"])
def test_pipeline_filters_match_goal_only_registry(query):
    """NL dietary_labels must not over-constrain when goal_diet_ids already active."""
    req = _pipeline(query)
    assert req.filters.goal_diet_ids
    registry_only = build_filter_clauses(SearchFilters(goal_diet_ids=list(req.filters.goal_diet_ids)))
    pipeline_clauses = build_filter_clauses(req.filters)
    assert pipeline_clauses.filter_clauses == registry_only.filter_clauses
    assert pipeline_clauses.must_not_clauses == registry_only.must_not_clauses


@pytest.mark.parametrize("query", ["keto", "vegan", "muscle gain", "heart healthy", "gluten free"])
def test_goal_only_search_never_empty_when_filters_match(query):
    req = _pipeline(query)
    assert req.filters.goal_diet_ids
    assert is_goal_only_retrieval(req.processed_query, req.filters, req.routing_context)

    client = _FilterOnlyMockClient()
    emb = MagicMock()
    result = hybrid_search(
        client,
        req.processed_query,
        req.filters,
        size=10,
        settings=SETTINGS,
        embedding_service=emb,
        routing_context=req.routing_context,
    )

    assert result.items, f"{query!r} should not return an empty pool for goal-only retrieval"
    assert result.fallback_reason == "goal_diet: filter-only retrieval"
    assert result.router_decision == "HYBRID"
    assert _body_is_filter_only(client.bodies[0].get("query", {}))


def test_goal_plus_product_not_filter_only():
    req = _pipeline("keto bread")
    assert not is_goal_only_retrieval(req.processed_query, req.filters, req.routing_context)


def test_router_decision_preserved_on_product_intent_relaxation():
    req = _pipeline("keto bread")
    client = MagicMock()
    client.search.side_effect = [
        {"hits": {"hits": []}},
        {"hits": {"hits": [_sample_hit("relaxed")]}},
    ]
    emb = MagicMock()
    emb.embed_query.return_value = [0.1] * 512
    emb.embed_batch.return_value = [[0.1] * 512]

    result = hybrid_search(
        client,
        req.processed_query,
        req.filters,
        size=10,
        settings=SETTINGS,
        embedding_service=emb,
        routing_context=req.routing_context,
    )

    assert result.router_decision == "LEXICAL_ONLY"
    assert result.product_intent_relaxed is True


def test_health_intake_still_sets_goal_diet_ids_for_goal_only_queries():
    for query in ("keto", "vegan", "muscle gain", "heart healthy", "gluten free"):
        health = classify_health_intent(query)
        assert health.detected, query
        assert health.goal_diet_ids
