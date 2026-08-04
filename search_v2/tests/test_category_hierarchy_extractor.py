from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.extension.taxonomy import (
    build_category_terms_aggregation,
    build_subcategory_terms_aggregation,
    fetch_app_config_categories,
    parse_categories_from_aggregations,
    parse_subcategories_from_aggregations,
    sync_category_metadata_with_app_config,
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
    assert scripted_metric["params"]["categories"] == ["light_bites"]


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


def _hierarchy_nested_clauses(clauses):
    return [
        c for c in clauses
        if isinstance(c, dict)
        and isinstance(c.get("nested"), dict)
        and c["nested"].get("path") == "category_hierarchies"
        and isinstance(c["nested"].get("query"), dict)
        and isinstance(c["nested"]["query"].get("bool"), dict)
        and isinstance(c["nested"]["query"]["bool"].get("filter"), list)
    ]


def test_build_filter_clauses_applies_nested_department_segment_filter():
    sf = SearchFilters.from_dict({"department": "food"})
    nested = _hierarchy_nested_clauses(build_filter_clauses(sf).filter_clauses)
    assert nested, "Expected nested category_hierarchies clause for department selector"
    assert nested[0]["nested"]["query"]["bool"]["filter"] == [
        {"term": {"category_hierarchies.segments": "food"}}
    ]


def test_build_filter_clauses_applies_nested_category_segment_filter():
    sf = SearchFilters.from_dict({"category": "light_bites"})
    nested = _hierarchy_nested_clauses(build_filter_clauses(sf).filter_clauses)
    assert nested, "Expected nested category_hierarchies clause for category selector"
    assert nested[0]["nested"]["query"]["bool"]["filter"] == [
        {"term": {"category_hierarchies.segments": "light_bites"}}
    ]


def test_build_filter_clauses_applies_nested_subcategory_segment_filter():
    sf = SearchFilters.from_dict({"subcategory": "chips_and_crisps"})
    nested = _hierarchy_nested_clauses(build_filter_clauses(sf).filter_clauses)
    assert nested, "Expected nested category_hierarchies clause for subcategory selector"
    assert nested[0]["nested"]["query"]["bool"]["filter"] == [
        {"term": {"category_hierarchies.segments": "chips_and_crisps"}}
    ]


def test_build_filter_clauses_combines_hierarchy_levels_in_one_nested_bool_filter():
    sf = SearchFilters.from_dict({
        "department": "food",
        "category": "biscuits_and_crackers",
        "subcategory": "cookies",
    })
    nested = _hierarchy_nested_clauses(build_filter_clauses(sf).filter_clauses)
    assert len(nested) == 1
    assert nested[0]["nested"]["query"]["bool"]["filter"] == [
        {"term": {"category_hierarchies.segments": "food"}},
        {"term": {"category_hierarchies.segments": "biscuits_and_crackers"}},
        {"term": {"category_hierarchies.segments": "cookies"}},
    ]


def test_build_filter_clauses_ors_multi_value_hierarchy_level():
    sf = SearchFilters.from_dict({"department": ["food", "beverages"]})
    nested = _hierarchy_nested_clauses(build_filter_clauses(sf).filter_clauses)
    assert len(nested) == 1
    assert nested[0]["nested"]["query"]["bool"]["filter"] == [
        {
            "bool": {
                "should": [
                    {"term": {"category_hierarchies.segments": "food"}},
                    {"term": {"category_hierarchies.segments": "beverages"}},
                ],
                "minimum_should_match": 1,
            }
        }
    ]


def test_from_dict_accepts_comma_separated_hierarchy_string():
    sf = SearchFilters.from_dict({"category": "a, b, a"})
    assert sf.category_segment_l2 == ["a", "b"]


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


def test_build_category_terms_aggregation_has_nested_department_scope():
    aggs = build_category_terms_aggregation("food")
    nested = aggs["department_hierarchy_nested"]
    assert nested["nested"]["path"] == "category_hierarchies"
    scoped = nested["aggs"]["department_scope"]["filter"]["term"]
    assert scoped["category_hierarchies.segments"] == "food"
    scripted_metric = (
        nested["aggs"]["department_scope"]["aggs"]["category_candidates"]["aggs"][
            "segment2_values"
        ]["scripted_metric"]
    )
    assert scripted_metric["params"]["departments"] == ["food"]


def test_build_department_terms_aggregation_extracts_segment_one():
    from search_v2.extension.taxonomy import build_department_terms_aggregation

    aggs = build_department_terms_aggregation()
    nested = aggs["departments_hierarchy_nested"]
    assert nested["nested"]["path"] == "category_hierarchies"
    assert "segment1_values" in nested["aggs"]["department_candidates"]["aggs"]


def test_build_hierarchy_filter_group_marks_selected_and_skips_zero_counts():
    from shopping_bot.data_fetchers.dynamic_search_filters import (
        FILTER_DEPARTMENT_ID,
        build_hierarchy_filter_group,
    )

    group = build_hierarchy_filter_group(
        group_id=FILTER_DEPARTMENT_ID,
        title="Department",
        title_key="department",
        counts={"food": 12, "beverages": 0, "personal_care": 3},
        selected_values=["food"],
    )
    assert group is not None
    assert group["id"] == FILTER_DEPARTMENT_ID
    values = {item["value"]: item for item in group["items"]}
    assert set(values) == {"food", "personal_care"}
    assert values["food"]["isPreSelected"] is True
    assert values["personal_care"]["isPreSelected"] is False


def test_parse_categories_from_aggregations_returns_segment_two_candidates():
    aggregations = {
        "department_hierarchy_nested": {
            "department_scope": {
                "category_candidates": {
                    "segment2_values": {
                        "value": ["biscuits_and_crackers", "light_bites", "biscuits_and_crackers"]
                    }
                }
            }
        }
    }
    out = parse_categories_from_aggregations(aggregations, "food")
    assert out == ["biscuits_and_crackers", "light_bites"]


def test_sync_category_metadata_returns_intersection_in_app_config_order():
    categories_payload = [
        {"id": "light_bites", "image": "i1", "name": "Light Bites"},
        {"id": "biscuits_and_crackers", "image": "i2", "name": "Biscuits"},
        {"id": "dairy_and_bakery", "image": "i3", "name": "Dairy"},
    ]
    out = sync_category_metadata_with_app_config(
        department="food",
        es_category_ids=["dairy_and_bakery", "biscuits_and_crackers"],
        categories_payload=categories_payload,
    )
    assert out == [
        {"id": "biscuits_and_crackers", "image": "i2", "name": "Biscuits"},
        {"id": "dairy_and_bakery", "image": "i3", "name": "Dairy"},
    ]


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
