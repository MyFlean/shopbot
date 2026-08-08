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
        # hide_score is a listing field (defaults false); not a PDP-only metadata leak
        assert card["hide_score"] is False


def test_listing_cards_include_hide_score_from_flean_score():
    src = _src()
    src["flean_score"] = {"adjusted_score": 85.0, "hide_score": True}
    v1_card = transform_to_product_card(src)
    v2_card = to_product_card(src)
    assert v1_card["hide_score"] is True
    assert v2_card["hide_score"] is True

    src_off = _src()
    src_off["flean_score"] = {"adjusted_score": 85.0, "hide_score": False}
    assert transform_to_product_card(src_off)["hide_score"] is False
    assert to_product_card(src_off)["hide_score"] is False


def test_transform_to_product_card_passthrough_hide_score_pretransformed():
    """Pre-transformed listing shape (no category_data) passes hide_score through."""
    card = transform_to_product_card(
        {
            "id": "pre-1",
            "name": "Pretransformed",
            "visibility": "visible",
            "flean_score": 8.5,
            "hide_score": True,
            "protein_g": 10,
        }
    )
    assert card is not None
    assert card["hide_score"] is True
