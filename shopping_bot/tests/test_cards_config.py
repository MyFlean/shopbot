"""Tests for PDP score-card grid config (Redis + config-driven building)."""

from unittest.mock import patch

import pytest

from shopping_bot.data_fetchers.es_products import transform_to_pdp
from shopping_bot.utils.cards_config import (
    CARD_DISPLAY_NAME_TO_SCORE_KEY,
    CARD_STATS_REGISTRY,
    SCORE_CARD_BUILD_ORDER,
    allowed_score_keys_from_config,
    apply_order_from_config,
    score_key_meta_from_config,
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


def test_allowed_score_keys_from_config_maps_produce_cards():
    config = [
        {"card": "Natural Sugar", "visible": True, "order": 1},
        {"card": "Glycemic Index", "visible": True, "order": 2},
        {"card": "Vitamins & Minerals", "visible": True, "order": 3},
        {"card": "Antioxidants", "visible": True, "order": 4},
        {"card": "Gut Health", "visible": True, "order": 5},
    ]
    assert allowed_score_keys_from_config(config) == frozenset(
        {
            "natural_sugar",
            "glycemic_index",
            "vitamins_minerals",
            "antioxidants",
            "gut_health",
        }
    )


def test_score_key_meta_from_config():
    config = [
        {
            "card": "Protein",
            "highlight_tag": "protein_tags",
            "visible": True,
            "optional": True,
            "order": 1,
        },
        {
            "card": "Natural Sugar",
            "highlight_tag": "ns_tags",
            "visible": True,
            "optional": False,
            "order": 2,
        },
    ]
    meta = score_key_meta_from_config(config)
    assert meta["protein"]["highlight_tag"] == "protein_tags"
    assert meta["natural_sugar"]["title"] == "Natural Sugar"
    assert meta["natural_sugar"]["optional"] is False


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


def _veggies_src(**overrides):
    src = {
        "id": "01KVJN67GZKMY3RMJQB2JBPPSR",
        "name": "Green Zucchini",
        "category_paths": ["f_and_b/food/veggies_and_fruits/veggies"],
        "category_data": {
            "processing_type": "minimally_processed",
            "nutritional": {"qty": "100 g", "nutri_breakdown_updated": {"energy kcal": 17}},
            "tags": {
                "highlight_tags": {
                    "ns_tags": {"positive": ["low_natural_sugar"]},
                    "gi_tags": {"positive": ["low_gi"]},
                    "vm_tags": {"positive": ["vitamin_c_rich"]},
                    "antioxidant_tags": {"positive": ["antioxidant_rich"]},
                    "gh_tags": {"positive": ["gut_friendly"]},
                }
            },
        },
        "stats": {
            "total_sugar_percentiles": {"subcategory_percentile": 20.0},
            "fiber_percentiles": {"subcategory_percentile": 75.0},
            "total_vitamin_mineral_percentiles": {"subcategory_percentile": 90.0},
            "adjusted_score_percentiles": {"subcategory_percentile": 88.0},
        },
    }
    for key, val in overrides.items():
        src[key] = val
    return src


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_calories_card_value_uses_kcal_format(mock_get_config):
    mock_get_config.return_value = [
        {
            "card": "Calories",
            "highlight_tag": "energy_tags",
            "visible": True,
            "optional": True,
            "order": 1,
        },
    ]
    src = _rich_src()
    src["stats"]["calories_penalty_percentiles"] = {"subcategory_percentile": 30.0}
    pdp = transform_to_pdp(src)
    cal = pdp["score_cards"]["calories"]
    assert cal["value"] == "200 kcal/ 100 g"
    assert cal["subtitle"] == "100 g"
    assert cal["percentile"] == 30.0


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


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_transform_to_pdp_builds_produce_cards_from_stats(mock_get_config):
    mock_get_config.return_value = [
        {"card": "Natural Sugar", "highlight_tag": "ns_tags", "visible": True, "optional": True, "order": 1},
        {"card": "Glycemic Index", "highlight_tag": "gi_tags", "visible": True, "optional": True, "order": 2},
        {"card": "Vitamins & Minerals", "highlight_tag": "vm_tags", "visible": True, "optional": True, "order": 3},
        {"card": "Antioxidants", "highlight_tag": "antioxidant_tags", "visible": True, "optional": True, "order": 4},
        {"card": "Gut Health", "highlight_tag": "gh_tags", "visible": True, "optional": True, "order": 5},
    ]
    pdp = transform_to_pdp(_veggies_src())
    sc = pdp["score_cards"]
    assert sc["natural_sugar"]["percentile"] == 20.0
    assert sc["natural_sugar"]["value"] == "Low"
    assert sc["glycemic_index"]["value"] == "Low"
    assert sc["glycemic_index"]["percentile"] is None
    assert sc["vitamins_minerals"]["percentile"] == 90.0
    assert "antioxidants" in sc
    assert "gut_health" in sc
    assert sc["antioxidants"]["value"] == "Good"
    assert sc["gut_health"]["value"] == "Good"
    assert sc["natural_sugar"]["subtitle_new"]
    assert sc["antioxidants"]["percentile"] is None


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_transform_to_pdp_uses_config_highlight_tag_for_protein(mock_get_config):
    mock_get_config.return_value = [
        {
            "card": "Protein",
            "highlight_tag": "protein_tags",
            "visible": True,
            "optional": True,
            "order": 1,
        },
    ]
    src = _rich_src()
    src["category_data"]["tags"]["highlight_tags"] = {
        "protein_tags": {"positive": ["high_protein_density"]},
    }
    pdp = transform_to_pdp(src)
    assert "subtitle_new" in pdp["score_cards"]["protein"]


def test_produce_display_names_map_to_score_keys():
    for display_name in (
        "Natural Sugar",
        "Glycemic Index",
        "Hydration",
        "Vitamins & Minerals",
        "Antioxidants",
        "Gut Health",
    ):
        assert display_name in CARD_DISPLAY_NAME_TO_SCORE_KEY


def test_score_card_build_order_covers_registry_and_display_names():
    registry_keys = frozenset(CARD_STATS_REGISTRY.keys())
    assert frozenset(SCORE_CARD_BUILD_ORDER) == registry_keys
    display_score_keys = frozenset(CARD_DISPLAY_NAME_TO_SCORE_KEY.values())
    assert display_score_keys <= registry_keys


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_highlight_only_card_uses_config_highlight_tag(mock_get_config):
    mock_get_config.return_value = [
        {
            "card": "Antioxidants",
            "highlight_tag": "custom_antioxidant_tags",
            "visible": True,
            "optional": True,
            "order": 1,
        },
    ]
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"] = {
        "custom_antioxidant_tags": {"positive": ["antioxidant_rich"]},
        "antioxidant_tags": {"positive": ["should_not_be_used"]},
    }
    pdp = transform_to_pdp(src)
    sc = pdp["score_cards"]
    assert "antioxidants" in sc
    assert sc["antioxidants"]["value"] == "Good"
    assert sc["antioxidants"]["subtitle_new"]
    assert sc["antioxidants"]["subtitle_new"][0]["tag_label"]


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_highlight_only_skipped_without_config_highlight_tag(mock_get_config):
    mock_get_config.return_value = [
        {
            "card": "Antioxidants",
            "highlight_tag": "",
            "visible": True,
            "optional": True,
            "order": 1,
        },
    ]
    src = _veggies_src()
    pdp = transform_to_pdp(src)
    assert "antioxidants" not in pdp["score_cards"]


_GI_CONFIG = [
    {
        "card": "Glycemic Index",
        "highlight_tag": "gi_tags",
        "visible": True,
        "optional": True,
        "order": 1,
    },
]


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
@pytest.mark.parametrize(
    ("tag_id", "expected_value"),
    [
        ("low_gi", "Low"),
        ("medium_gi", "Medium"),
        ("high_gi", "High"),
    ],
)
def test_glycemic_index_maps_tag_to_value(mock_get_config, tag_id, expected_value):
    mock_get_config.return_value = _GI_CONFIG
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"]["gi_tags"] = {"positive": [tag_id]}
    pdp = transform_to_pdp(src)
    card = pdp["score_cards"]["glycemic_index"]
    assert card["value"] == expected_value
    assert card["percentile"] is None


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_glycemic_index_skipped_without_gi_tag(mock_get_config):
    mock_get_config.return_value = _GI_CONFIG
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"]["gi_tags"] = {"positive": ["unrelated_tag"]}
    pdp = transform_to_pdp(src)
    assert "glycemic_index" not in pdp["score_cards"]


_HYDRATION_CONFIG = [
    {
        "card": "Hydration",
        "highlight_tag": "hydration_tags",
        "visible": True,
        "optional": True,
        "order": 1,
    },
]


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_hydration_shown_with_hydrating_tag(mock_get_config):
    mock_get_config.return_value = _HYDRATION_CONFIG
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"]["hydration_tags"] = {
        "positive": ["hydrating"]
    }
    pdp = transform_to_pdp(src)
    card = pdp["score_cards"]["hydration"]
    assert card["value"] == "High"
    assert card["percentile"] is None
    assert card["subtitle_new"][0]["tag_label"] == "High water content"


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_hydration_skipped_without_hydrating_tag(mock_get_config):
    mock_get_config.return_value = _HYDRATION_CONFIG
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"]["hydration_tags"] = {
        "positive": ["unrelated_tag"]
    }
    pdp = transform_to_pdp(src)
    assert "hydration" not in pdp["score_cards"]


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
def test_hydration_skipped_without_config_highlight_tag(mock_get_config):
    mock_get_config.return_value = [
        {
            "card": "Hydration",
            "highlight_tag": "",
            "visible": True,
            "optional": True,
            "order": 1,
        },
    ]
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"]["hydration_tags"] = {
        "positive": ["hydrating"]
    }
    pdp = transform_to_pdp(src)
    assert "hydration" not in pdp["score_cards"]


_GUT_HEALTH_CONFIG = [
    {
        "card": "Gut Health",
        "highlight_tag": "gh_tags",
        "visible": True,
        "optional": True,
        "order": 1,
    },
]

_ANTIOXIDANTS_CONFIG = [
    {
        "card": "Antioxidants",
        "highlight_tag": "antioxidant_tags",
        "visible": True,
        "optional": True,
        "order": 1,
    },
]


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
@pytest.mark.parametrize(
    ("score_key", "group_key", "config"),
    [
        ("gut_health", "gh_tags", _GUT_HEALTH_CONFIG),
        ("antioxidants", "antioxidant_tags", _ANTIOXIDANTS_CONFIG),
    ],
)
@pytest.mark.parametrize(
    ("bucket", "tag_id", "expected_value"),
    [
        ("positive", "sample_positive", "Good"),
        ("neutral", "sample_neutral", "Average"),
        ("negative", "sample_negative", "Poor"),
    ],
)
def test_sentiment_highlight_maps_bucket_to_value(
    mock_get_config, score_key, group_key, config, bucket, tag_id, expected_value
):
    mock_get_config.return_value = config
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"][group_key] = {bucket: [tag_id]}
    pdp = transform_to_pdp(src)
    card = pdp["score_cards"][score_key]
    assert card["value"] == expected_value
    assert card["percentile"] is None


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
@pytest.mark.parametrize(
    ("score_key", "group_key", "config"),
    [
        ("gut_health", "gh_tags", _GUT_HEALTH_CONFIG),
        ("antioxidants", "antioxidant_tags", _ANTIOXIDANTS_CONFIG),
    ],
)
def test_sentiment_highlight_skipped_when_group_empty(mock_get_config, score_key, group_key, config):
    mock_get_config.return_value = config
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"][group_key] = {
        "positive": [],
        "neutral": [],
        "negative": [],
    }
    pdp = transform_to_pdp(src)
    assert score_key not in pdp["score_cards"]


@patch("shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path")
@pytest.mark.parametrize(
    ("score_key", "group_key", "config"),
    [
        ("gut_health", "gh_tags", _GUT_HEALTH_CONFIG),
        ("antioxidants", "antioxidant_tags", _ANTIOXIDANTS_CONFIG),
    ],
)
def test_sentiment_highlight_negative_wins_over_positive(
    mock_get_config, score_key, group_key, config
):
    mock_get_config.return_value = config
    src = _veggies_src()
    src["category_data"]["tags"]["highlight_tags"][group_key] = {
        "positive": ["sample_positive"],
        "neutral": [],
        "negative": ["sample_negative"],
    }
    pdp = transform_to_pdp(src)
    assert pdp["score_cards"][score_key]["value"] == "Poor"


def test_get_redis_client_uses_lazy_initializer():
    from unittest.mock import MagicMock
    from flask import Flask

    from shopping_bot.utils.cards_config import _get_redis_client

    app = Flask(__name__)
    mock_ctx = MagicMock()
    mock_ctx.redis = MagicMock()
    app.extensions["ctx_mgr"] = None
    app.extensions["_get_or_init_redis"] = MagicMock(return_value=mock_ctx)

    with app.app_context():
        assert _get_redis_client() is mock_ctx.redis
    app.extensions["_get_or_init_redis"].assert_called_once()
