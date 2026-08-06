from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.extension.shop_by_goal import (
    GoalConfigError,
    resolve_diet_selection,
    resolve_goal_selection,
)
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses


def test_goal_overlays_are_appended_to_filter_and_must_not_clauses():
    sf = SearchFilters.from_dict(
        {
            "goal": "high_protein",
            "goal_filter_clauses": [{"term": {"category_data.tags.protein_tags.positive": "high_protein_density"}}],
            "goal_must_not_clauses": [{"range": {"flean_score.adjusted_score": {"lt": 50}}}],
        }
    )
    clauses = build_filter_clauses(sf)
    assert sf.goal_ids == ["high_protein"]
    assert any("category_data.tags.protein_tags.positive" in str(c) for c in clauses.filter_clauses)
    assert any("flean_score.adjusted_score" in str(c) for c in clauses.must_not_clauses)


def test_diet_overlays_are_appended_to_filter_and_must_not_clauses():
    sf = SearchFilters.from_dict(
        {
            "diet": "keto",
            "diet_filter_clauses": [{"term": {"category_data.tags.highlight_tags.keto_tags.positive": "keto_friendly"}}],
            "diet_must_not_clauses": [{"range": {"category_data.nutritional.nutri_breakdown.carbohydrate g": {"gt": 20}}}],
        }
    )
    clauses = build_filter_clauses(sf)
    assert sf.diet_ids == ["keto"]
    assert any("keto_tags" in str(c) for c in clauses.filter_clauses)
    assert any("carbohydrate g" in str(c) for c in clauses.must_not_clauses)


def test_goal_sort_is_used_only_when_explicit_sort_missing():
    from_goal = SearchFilters.from_dict({"goal": ["high_protein"], "goal_sort_by": "protein_desc"})
    assert from_goal.sort_by == "protein_desc"

    explicit = SearchFilters.from_dict(
        {"goal": ["high_protein"], "goal_sort_by": "protein_desc", "sort_by": "price_asc"}
    )
    assert explicit.sort_by == "price_asc"


def test_diet_sort_order_is_used_when_present():
    sf = SearchFilters.from_dict(
        {
            "diet": ["keto"],
            "diet_sort_order": [
                {"field": "category_data.nutritional.nutri_breakdown.carbohydrate g", "order": "asc"},
                {"field": "derived.net_carbs", "order": "asc"},
            ],
        }
    )
    assert sf.sort_order == [
        {"field": "category_data.nutritional.nutri_breakdown.carbohydrate g", "order": "asc"},
        {"field": "derived.net_carbs", "order": "asc"},
    ]


def test_goal_sort_order_is_used_when_present():
    sf = SearchFilters.from_dict(
        {
            "goal": ["high_protein"],
            "goal_sort_order": [
                {"field": "category_data.nutritional.nutri_breakdown.protein g", "order": "desc"},
                {"field": "derived.net_carbs", "order": "asc"},
            ],
        }
    )
    assert sf.sort_order == [
        {"field": "category_data.nutritional.nutri_breakdown.protein g", "order": "desc"},
        {"field": "derived.net_carbs", "order": "asc"},
    ]


def test_resolver_uses_raw_es_field_paths_without_mapping():
    payload = {
        "schema_version": "1.0",
        "goals": [
            {
                "id": "high_protein",
                "label": "High Protein",
                "enabled": True,
                "include_all": [
                    {
                        "field": "category_data.nutritional.nutri_breakdown.protein_g",
                        "op": "gte",
                        "value": 10,
                    }
                ],
                "exclude_any": [
                    {
                        "field": "category_data.nutritional.nutri_breakdown.added_sugar_g",
                        "op": "gt",
                        "value": 12,
                    }
                ],
                "sort_by": "protein_desc",
                "sort_order": [
                    {"field": "stats.protein_percentiles.subcategory_percentile", "order": "desc"}
                ],
            }
        ],
    }
    resolved = resolve_goal_selection("high_protein", config_payload=payload)
    assert resolved["goal_sort_by"] == "protein_desc"
    assert resolved["goal_filter_clauses"] == [
        {
            "range": {
                "category_data.nutritional.nutri_breakdown.protein_g": {
                    "gte": 10
                }
            }
        }
    ]
    assert resolved["goal_must_not_clauses"] == [
        {
            "range": {
                "category_data.nutritional.nutri_breakdown.added_sugar_g": {
                    "gt": 12
                }
            }
        }
    ]
    assert resolved["goal_sort_order"] == [
        {"field": "stats.protein_percentiles.subcategory_percentile", "order": "desc"}
    ]


def test_resolver_resolves_diet_selection():
    payload = {
        "schema_version": "1.0",
        "diets": [
            {
                "id": "keto",
                "label": "Keto",
                "enabled": True,
                "include_all": [
                    {
                        "field": "category_data.nutritional.nutri_breakdown.carbohydrate g",
                        "op": "lte",
                        "value": 12,
                    }
                ],
                "exclude_any": [
                    {
                        "field": "category_data.nutritional.nutri_breakdown.added sugar g",
                        "op": "gt",
                        "value": 4,
                    }
                ],
                "sort_order": [
                    {"field": "category_data.nutritional.nutri_breakdown.carbohydrate g", "order": "asc"}
                ],
            }
        ],
    }
    resolved = resolve_diet_selection("keto", config_payload=payload)
    assert resolved["diet_ids"] == ["keto"]
    assert resolved["diet_filter_clauses"] == [
        {
            "range": {
                "category_data.nutritional.nutri_breakdown.carbohydrate g": {
                    "lte": 12
                }
            }
        }
    ]
    assert resolved["diet_must_not_clauses"] == [
        {
            "range": {
                "category_data.nutritional.nutri_breakdown.added sugar g": {
                    "gt": 4
                }
            }
        }
    ]
    assert resolved["diet_sort_order"] == [
        {"field": "category_data.nutritional.nutri_breakdown.carbohydrate g", "order": "asc"}
    ]


@patch("search_v2.extension.shop_by_goal.resolver.fetch_goal_config")
@patch("search_v2.extension.shop_by_goal.resolver.fetch_diet_config")
def test_diet_resolver_uses_diet_loader_only(mock_fetch_diet_config, mock_fetch_goal_config):
    mock_fetch_diet_config.return_value = {
        "schema_version": "1.0",
        "diets": [
            {
                "id": "keto",
                "label": "Keto",
                "enabled": True,
                "include_all": [],
                "exclude_any": [],
            }
        ],
    }
    resolve_diet_selection("keto")
    mock_fetch_diet_config.assert_called_once()
    mock_fetch_goal_config.assert_not_called()


@patch("search_v2.extension.shop_by_goal.resolver.fetch_diet_config")
@patch("search_v2.extension.shop_by_goal.resolver.fetch_goal_config")
def test_goal_resolver_uses_goal_loader_only(mock_fetch_goal_config, mock_fetch_diet_config):
    mock_fetch_goal_config.return_value = {
        "schema_version": "1.0",
        "goals": [
            {
                "id": "high_protein",
                "label": "High Protein",
                "enabled": True,
                "include_all": [],
                "exclude_any": [],
            }
        ],
    }
    resolve_goal_selection("high_protein")
    mock_fetch_goal_config.assert_called_once()
    mock_fetch_diet_config.assert_not_called()


def test_diet_resolver_rejects_payload_missing_diets_array():
    payload = {"schema_version": "1.0", "goals": []}
    with pytest.raises(GoalConfigError) as exc_info:
        resolve_diet_selection("keto", config_payload=payload)
    assert exc_info.value.code == "GOAL_CONFIG_SCHEMA_INVALID"
    assert "diets" in exc_info.value.message.lower()


def test_resolver_rejects_non_string_field():
    payload = {
        "schema_version": "1.0",
        "goals": [
            {
                "id": "high_protein",
                "label": "High Protein",
                "enabled": True,
                "include_all": [
                    {
                        "field": 123,
                        "op": "gte",
                        "value": 10,
                    }
                ],
                "exclude_any": [],
            }
        ],
    }
    with pytest.raises(GoalConfigError) as exc_info:
        resolve_goal_selection("high_protein", config_payload=payload)
    assert exc_info.value.code == "GOAL_CONFIG_SCHEMA_INVALID"
    assert "field" in exc_info.value.message.lower()


def test_resolver_rejects_nutri_breakdown_updated_paths():
    payload = {
        "schema_version": "1.0",
        "goals": [
            {
                "id": "high_protein",
                "label": "High Protein",
                "enabled": True,
                "include_all": [
                    {
                        "field": "category_data.nutritional.nutri_breakdown_updated.protein_g",
                        "op": "gte",
                        "value": 10,
                    }
                ],
                "exclude_any": [],
            }
        ],
    }
    with pytest.raises(GoalConfigError) as exc_info:
        resolve_goal_selection("high_protein", config_payload=payload)
    assert exc_info.value.code == "GOAL_CONFIG_SCHEMA_INVALID"
    assert "nutri_breakdown" in exc_info.value.message


def test_resolver_rejects_invalid_sort_order_schema():
    payload = {
        "schema_version": "1.0",
        "goals": [
            {
                "id": "high_protein",
                "label": "High Protein",
                "enabled": True,
                "include_all": [
                    {
                        "field": "category_data.nutritional.nutri_breakdown.protein g",
                        "op": "gte",
                        "value": 10,
                    }
                ],
                "exclude_any": [],
                "sort_order": [{"field": "category_data.nutritional.nutri_breakdown.protein g", "order": "descending"}],
            }
        ],
    }
    with pytest.raises(GoalConfigError) as exc_info:
        resolve_goal_selection("high_protein", config_payload=payload)
    assert exc_info.value.code == "GOAL_CONFIG_SCHEMA_INVALID"
    assert "sort_order" in exc_info.value.message.lower()


def test_resolver_supports_match_any_operator():
    payload = {
        "schema_version": "1.0",
        "diets": [
            {
                "id": "millet_based",
                "label": "Millet Based",
                "enabled": True,
                "include_all": [
                    {
                        "field": "ingredients.raw_text",
                        "op": "match_any",
                        "value": ["millet", "ragi"],
                    }
                ],
                "exclude_any": [],
            }
        ],
    }
    resolved = resolve_diet_selection("millet_based", config_payload=payload)
    assert resolved["diet_filter_clauses"] == [
        {
            "bool": {
                "should": [
                    {"match": {"ingredients.raw_text": "millet"}},
                    {"match": {"ingredients.raw_text": "ragi"}},
                ],
                "minimum_should_match": 1,
            }
        }
    ]


def test_resolver_supports_match_phrase_any_operator():
    payload = {
        "schema_version": "1.0",
        "diets": [
            {
                "id": "nut_free",
                "label": "Nut Free",
                "enabled": True,
                "include_all": [],
                "exclude_any": [
                    {
                        "field": "ingredients.raw_text",
                        "op": "match_phrase_any",
                        "value": ["may contain tree nuts", "may contain peanuts"],
                    }
                ],
            }
        ],
    }
    resolved = resolve_diet_selection("nut_free", config_payload=payload)
    assert resolved["diet_must_not_clauses"] == [
        {
            "bool": {
                "should": [
                    {"match_phrase": {"ingredients.raw_text": "may contain tree nuts"}},
                    {"match_phrase": {"ingredients.raw_text": "may contain peanuts"}},
                ],
                "minimum_should_match": 1,
            }
        }
    ]


def test_resolver_supports_derived_additives_count_exclusion():
    payload = {
        "schema_version": "1.0",
        "goals": [
            {
                "id": "clean_eating",
                "label": "Clean Eating",
                "enabled": True,
                "include_all": [],
                "exclude_any": [
                    {
                        "field": "derived.additives_count",
                        "op": "gt",
                        "value": 2,
                    }
                ],
            }
        ],
    }
    resolved = resolve_goal_selection("clean_eating", config_payload=payload)
    clauses = resolved["goal_must_not_clauses"]
    assert len(clauses) == 1
    source = clauses[0]["script"]["script"]["source"]
    assert "doc.containsKey('ingredients.additives')" in source
    assert "doc['ingredients.additives'].size()" in source
    assert "return metric > params.value;" in source
    assert clauses[0]["script"]["script"]["params"] == {"value": 2}
