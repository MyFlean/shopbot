from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.extension.taxonomy import (
    build_subcategory_terms_aggregation,
    fetch_app_config_categories,
    parse_subcategories_from_aggregations,
    sync_subcategory_metadata_with_app_config,
)
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses


def test_build_subcategory_terms_aggregation_has_nested_category_scope():
    aggs = build_subcategory_terms_aggregation("light_bites")
    nested = aggs["category_hierarchy_nested"]
    assert nested["nested"]["path"] == "category_hierarchies"
    scoped = nested["aggs"]["category_scope"]["filter"]["term"]
    assert scoped["category_hierarchies.segments"] == "light_bites"
    scripted_metric = (
        nested["aggs"]["category_scope"]["aggs"]["subcategory_candidates"]["aggs"]["segment3_values"]["scripted_metric"]
    )
    assert scripted_metric["params"]["category"] == "light_bites"


def test_parse_subcategories_from_aggregations_returns_segment_three_candidates():
    aggregations = {
        "category_hierarchy_nested": {
            "category_scope": {
                "subcategory_candidates": {
                    "buckets": [
                        {"key": "chips_and_crisps", "doc_count": 21},
                        {"key": "nachos", "doc_count": 9},
                        {"key": "chips_and_crisps", "doc_count": 8},
                    ]
                }
            }
        }
    }
    out = parse_subcategories_from_aggregations(aggregations, "light_bites")
    assert out == ["chips_and_crisps", "nachos"]


def test_parse_subcategories_from_aggregations_handles_scripted_metric_values():
    aggregations = {
        "category_hierarchy_nested": {
            "category_scope": {
                "subcategory_candidates": {
                    "segment3_values": {
                        "value": ["ready_to_cook_meals", "pasta_and_soups", "pasta_and_soups"]
                    }
                }
            }
        }
    }
    out = parse_subcategories_from_aggregations(aggregations, "packaged_meals")
    assert out == ["ready_to_cook_meals", "pasta_and_soups"]


def test_build_filter_clauses_applies_nested_category_segment_filter():
    sf = SearchFilters.from_dict({"category": "light_bites"})
    clauses = build_filter_clauses(sf).filter_clauses
    nested = [
        c for c in clauses
        if isinstance(c, dict)
        and isinstance(c.get("nested"), dict)
        and c["nested"].get("path") == "category_hierarchies"
        and isinstance(c["nested"].get("query"), dict)
        and "term" in c["nested"]["query"]
    ]
    assert nested, "Expected nested category_hierarchies clause for category selector"
    assert nested[0]["nested"]["query"]["term"]["category_hierarchies.segments"] == "light_bites"


def test_build_filter_clauses_applies_nested_subcategory_segment_filter():
    sf = SearchFilters.from_dict({"subcategory": "chips_and_crisps"})
    clauses = build_filter_clauses(sf).filter_clauses
    nested = [
        c for c in clauses
        if isinstance(c, dict)
        and isinstance(c.get("nested"), dict)
        and c["nested"].get("path") == "category_hierarchies"
        and isinstance(c["nested"].get("query"), dict)
        and "term" in c["nested"]["query"]
    ]
    assert nested, "Expected nested category_hierarchies clause for subcategory selector"
    assert any(
        c["nested"]["query"]["term"]["category_hierarchies.segments"] == "chips_and_crisps"
        for c in nested
    )


def test_sync_subcategory_metadata_returns_intersection_in_app_config_order():
    categories_payload = [
        {
            "id": "packaged_meals",
            "subcategories": [
                {"id": "baby_food", "image": "i1", "name": "Baby Food"},
                {"id": "baking_mixes_and_ingredients", "image": "i2", "name": "Baking"},
                {"id": "pasta_and_soups", "image": "i3", "name": "Pasta"},
                {"id": "ready_to_cook_meals", "image": "i4", "name": "Ready To Cook"},
            ],
        }
    ]
    out = sync_subcategory_metadata_with_app_config(
        category="packaged_meals",
        es_subcategory_ids=["ready_to_cook_meals", "pasta_and_soups"],
        categories_payload=categories_payload,
    )
    assert out == [
        {"id": "pasta_and_soups", "image": "i3", "name": "Pasta"},
        {"id": "ready_to_cook_meals", "image": "i4", "name": "Ready To Cook"},
    ]


def test_sync_subcategory_metadata_handles_missing_category_or_fields():
    categories_payload = [
        {
            "id": "light_bites",
            "subcategories": [
                {"id": "chips_and_crisps", "name": "Chips"},
            ],
        }
    ]
    out_missing_category = sync_subcategory_metadata_with_app_config(
        category="packaged_meals",
        es_subcategory_ids=["chips_and_crisps"],
        categories_payload=categories_payload,
    )
    assert out_missing_category == []

    out_missing_fields = sync_subcategory_metadata_with_app_config(
        category="light_bites",
        es_subcategory_ids=["chips_and_crisps"],
        categories_payload=categories_payload,
    )
    assert out_missing_fields == [{"id": "chips_and_crisps", "image": "", "name": "Chips"}]


def test_fetch_app_config_categories_returns_empty_on_non_list_payload(monkeypatch):
    class _DummyResponse:
        def read(self):
            return b"{\"bad\": true}"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "search_v2.extension.taxonomy.urllib_request.urlopen",
        lambda *_args, **_kwargs: _DummyResponse(),
    )
    assert fetch_app_config_categories() == []
