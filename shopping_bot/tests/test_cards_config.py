"""Tests for PDP score-card grid config (Redis + config-driven building)."""

from unittest.mock import patch

from shopping_bot.data_fetchers.es_products import transform_to_pdp
from shopping_bot.utils.cards_config import (
    allowed_score_keys_from_config,
    apply_order_from_config,
    scorecard_redis_key,
)


def test_scorecard_redis_key_uses_prefix_and_path():
    assert scorecard_redis_key("f_and_b/food/biscuits_and_crackers/cookies") == (
        "scorecard/f_and_b/food/biscuits_and_crackers/cookies"
    )
    assert scorecard_redis_key("Default") == "scorecard/Default"


def test_allowed_score_keys_from_config_excludes_visible_false():
    config = [
        {"card": "Protein", "visible": False, "order": 1},
        {"card": "Fiber", "visible": True, "order": 2},
    ]
    assert allowed_score_keys_from_config(config) == frozenset({"fiber"})


def test_allowed_score_keys_from_config_maps_display_names():
    config = [
        {"card": "Protein", "visible": True, "order": 1},
        {"card": "Fats", "visible": True, "order": 2},
    ]
    assert allowed_score_keys_from_config(config) == frozenset({"protein", "oils"})


def test_allowed_score_keys_from_config_empty():
    assert allowed_score_keys_from_config([]) == frozenset()


def test_apply_order_from_config_sets_order_and_visible():
    score_cards = {
        "protein": {"title": "Protein", "value": "Good"},
        "fiber": {"title": "Fiber", "value": "Good"},
    }
    config = [
        {"card": "Protein", "visible": True, "order": 2},
        {"card": "Fiber", "visible": True, "order": 1},
    ]
    updated = apply_order_from_config(score_cards, config)
    assert updated["protein"]["order"] == 2
    assert updated["protein"]["visible"] is True
    assert updated["fiber"]["order"] == 1


def _rich_src(**overrides):
    src = {
        "id": "test-1",
        "name": "Test Product",
        "category_paths": ["f_and_b/food/biscuits_and_crackers/cookies"],
        "category_data": {
            "processing_type": "minimally_processed",
            "nutritional": {"qty": "100 g", "nutri_breakdown_updated": {"energy kcal": 200}},
            "tags": {"highlight_tags": {}},
        },
        "stats": {
            "protein_percentiles": {"subcategory_percentile": 70},
            "fiber_percentiles": {"subcategory_percentile": 65},
            "adjusted_score_percentiles": {"subcategory_percentile": 80},
        },
    }
    for key, val in overrides.items():
        src[key] = val
    return src


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_transform_to_pdp_only_builds_visible_configured_cards(mock_get_config):
    mock_get_config.return_value = [
        {"card": "Protein", "visible": False, "order": 1},
        {"card": "Fiber", "visible": True, "order": 2},
    ]
    pdp = transform_to_pdp(_rich_src())
    assert "protein" not in pdp["score_cards"]
    assert "fiber" in pdp["score_cards"]


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_transform_to_pdp_only_builds_listed_cards(mock_get_config):
    mock_get_config.return_value = [
        {"card": "Fiber", "visible": True, "order": 1},
    ]
    pdp = transform_to_pdp(_rich_src())
    assert "protein" not in pdp["score_cards"]
    assert set(pdp["score_cards"].keys()) == {"fiber"}


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_transform_to_pdp_empty_config_returns_all_built_cards(mock_get_config):
    mock_get_config.return_value = []
    pdp = transform_to_pdp(_rich_src())
    assert "protein" in pdp["score_cards"]
    assert "fiber" in pdp["score_cards"]
