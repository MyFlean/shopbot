"""Tests for PDP category_data metadata and listing isolation."""

from unittest.mock import patch

from search_v2.extension.product.card import to_product_card
from shopping_bot.data_fetchers.es_products import (
    transform_to_pdp,
    transform_to_product_card,
)


AMINO = {
    "l-leucine mg": 2500.0,
    "l-isoleucine mg": 1250.0,
}
ACTIVE = {
    "l-taurine mg": 1000.0,
    "coconut powder mg": 2000.0,
}


def _src(**category_overrides):
    category_data = {
        "tags": {"ingredient_tags": ["no_palm_oil"]},
        "nutritional": {},
    }
    category_data.update(category_overrides)
    return {
        "id": "prod-1",
        "name": "Test Product",
        "visibility": "visible",
        "category_data": category_data,
    }


@patch(
    "shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path",
    return_value=[],
)
def test_pdp_includes_non_null_category_metadata(_mock_cards):
    src = _src(
        servings_per_container=30,
        dietary_label="Vegetarian",
        amino_acid_profile=AMINO,
        active_ingredients=ACTIVE,
    )
    pdp = transform_to_pdp(src)

    assert pdp["product_info"]["servings_per_container"] == 30
    assert pdp["product_info"]["dietary_label"] == "Vegetarian"
    assert pdp["amino_acid_profile"] == AMINO
    assert pdp["active_ingredients"] == ACTIVE


@patch(
    "shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path",
    return_value=[],
)
def test_pdp_omits_null_category_fields_independently(_mock_cards):
    src = _src(
        servings_per_container=None,
        dietary_label="Vegetarian",
        amino_acid_profile=None,
        active_ingredients=ACTIVE,
    )
    pdp = transform_to_pdp(src)

    assert "servings_per_container" not in pdp["product_info"]
    assert pdp["product_info"]["dietary_label"] == "Vegetarian"
    assert "amino_acid_profile" not in pdp
    assert pdp["active_ingredients"] == ACTIVE


@patch(
    "shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path",
    return_value=[],
)
def test_pdp_omits_absent_category_fields(_mock_cards):
    pdp = transform_to_pdp(_src())

    assert "servings_per_container" not in pdp["product_info"]
    assert "dietary_label" not in pdp["product_info"]
    assert "amino_acid_profile" not in pdp
    assert "active_ingredients" not in pdp


@patch(
    "shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path",
    return_value=[],
)
def test_pdp_preserves_nested_category_objects(_mock_cards):
    amino = dict(AMINO)
    active = dict(ACTIVE)
    pdp = transform_to_pdp(
        _src(amino_acid_profile=amino, active_ingredients=active)
    )

    assert pdp["amino_acid_profile"] is amino
    assert pdp["active_ingredients"] is active
    assert pdp["amino_acid_profile"] == AMINO
    assert pdp["active_ingredients"] == ACTIVE


def test_listing_cards_do_not_gain_category_metadata_fields():
    src = _src(
        servings_per_container=30,
        dietary_label="Vegetarian",
        amino_acid_profile=AMINO,
        active_ingredients=ACTIVE,
    )
    v1_card = transform_to_product_card(src)
    v2_card = to_product_card(src)

    for card in (v1_card, v2_card):
        assert "servings_per_container" not in card
        assert "dietary_label" not in card
        assert "amino_acid_profile" not in card
        assert "active_ingredients" not in card
        assert "express_delivery" not in card
