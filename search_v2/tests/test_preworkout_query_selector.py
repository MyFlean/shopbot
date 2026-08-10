from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.query_processing.product_intent_extractor import ProductIntentExtractor
from search_v2.query_processing.query_pipeline import process_query, process_search_request
from search_v2.retrieval.filters import SearchFilters


def test_process_query_adds_spaced_variant_for_preworkout():
    pq = process_query("preworkout", corrector=None, enable_typo_correction=False)
    variant_texts = {v.text for v in pq.variants}
    assert "preworkout" in variant_texts
    assert "pre workout" in variant_texts


def test_process_query_adds_compound_variant_for_pre_workout():
    pq = process_query("pre workout", corrector=None, enable_typo_correction=False)
    variant_texts = {v.text for v in pq.variants}
    assert "pre workout" in variant_texts
    assert "preworkout" in variant_texts


def test_preworkout_query_selector_changes_do_not_mutate_explicit_hierarchy_filters():
    explicit = SearchFilters.from_dict(
        {
            "department": "supplements",
            "category": "pre_post_workout",
            "subcategory": "pre_workout",
        }
    )
    extractor = ProductIntentExtractor({}, settings=None)
    req = process_search_request(
        "preworkout",
        explicit_filters=explicit,
        corrector=None,
        enable_typo_correction=False,
        enable_nl_filters=False,
        product_intent_extractor=extractor,
    )
    assert req.filters.department_segment_l1 == ["supplements"]
    assert req.filters.category_segment_l2 == ["pre_post_workout"]
    assert req.filters.subcategory_segment_l3 == ["pre_workout"]


def test_pre_workout_keeps_head_term_through_nl_filter_extraction():
    extractor = ProductIntentExtractor({}, settings=None)
    req = process_search_request(
        "pre workout",
        explicit_filters=None,
        corrector=None,
        enable_typo_correction=False,
        enable_nl_filters=True,
        product_intent_extractor=extractor,
    )
    assert req.processed_query.primary_text() == "pre workout"
    assert req.product_intent is not None
    assert req.product_intent.primary_product == "pre workout"
