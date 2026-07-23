"""Tests for purchase-categories home and drilldown endpoints."""

from unittest.mock import patch

import pytest
from flask import Flask

from shopping_bot.routes.home_page import _extract_category_hierarchy_2
from shopping_bot.routes.home_page import bp as home_page_bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(home_page_bp, url_prefix="/rs")
    app.config["TESTING"] = True
    return app.test_client()


@patch("shopping_bot.routes.home_page._fetch_purchased_product_ids")
@patch("shopping_bot.routes.home_page._build_category_product_pairs")
@patch("shopping_bot.routes.home_page._get_purchase_category_name_map")
@patch("shopping_bot.routes.home_page.transform_to_product_card")
def test_purchase_categories_groups_by_category_level_two(
    mock_transform_to_product_card,
    mock_category_name_map,
    mock_pairs,
    mock_fetch_ids,
    client,
):
    mock_category_name_map.return_value = {}
    mock_fetch_ids.return_value = (
        {
            "user_id": "u_123",
            "product_ids": ["p1", "p2", "p3", "p4"],
            "total_count": 6,
            "meta": {},
        },
        None,
    )
    mock_pairs.return_value = [
        ("f_and_b/food/light_bites", {"id": "p1", "images": ["https://img/p1.png"]}),
        ("f_and_b/food/light_bites", {"id": "p2", "images": ["https://img/p2.png"]}),
        ("f_and_b/food/breakfast_essentials", {"id": "p3", "images": ["https://img/p3.png"]}),
        ("f_and_b/food/light_bites", {"id": "p2", "images": ["https://img/p2.png"]}),  # duplicate raw id must be de-duped in count
    ]
    mock_transform_to_product_card.side_effect = lambda raw: {
        "id": raw["id"],
        "name": raw["id"],
        "flean_score": {"p1": 9, "p2": 10, "p3": 8}[raw["id"]],
        "flean_percentile": {"p1": 90, "p2": 95, "p3": 80}[raw["id"]],
    }

    response = client.get(
        "/rs/api/v1/home/purchase-categories",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    categories = payload["data"]["categories"]
    assert categories == [
        {
            "name": "Light Bites",
            "category": "f_and_b/food/light_bites",
            "product_count": 2,
            "image_urls": ["https://img/p2.png", "https://img/p1.png"],
        },
        {
            "name": "Breakfast Essentials",
            "category": "f_and_b/food/breakfast_essentials",
            "product_count": 1,
            "image_urls": ["https://img/p3.png"],
        },
    ]
    assert payload["meta"]["total_count"] == 4


@patch("shopping_bot.routes.home_page._fetch_purchased_product_ids")
@patch("shopping_bot.routes.home_page._build_category_product_pairs")
@patch("shopping_bot.routes.home_page._get_purchase_category_name_map")
@patch("shopping_bot.routes.home_page.transform_to_product_card")
def test_purchase_categories_uses_config_name_for_category_id(
    mock_transform_to_product_card,
    mock_category_name_map,
    mock_pairs,
    mock_fetch_ids,
    client,
):
    mock_category_name_map.return_value = {"veggies_and_fruits": "Veggies & Fruits"}
    mock_transform_to_product_card.return_value = {
        "id": "p1",
        "name": "Alpha",
        "flean_score": 10,
        "flean_percentile": 99,
    }
    mock_fetch_ids.return_value = (
        {"user_id": "u_123", "product_ids": ["p1"], "total_count": 1, "meta": {}},
        None,
    )
    mock_pairs.return_value = [("veggies_and_fruits", {"id": "p1", "images": ["https://img/p1.png"]})]

    response = client.get("/rs/api/v1/home/purchase-categories")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["categories"][0]["category"] == "veggies_and_fruits"
    assert payload["data"]["categories"][0]["name"] == "Veggies & Fruits"
    assert payload["data"]["categories"][0]["image_urls"] == ["https://img/p1.png"]


@patch("shopping_bot.routes.home_page._fetch_purchased_product_ids")
@patch("shopping_bot.routes.home_page._build_category_product_pairs")
@patch("shopping_bot.routes.home_page._get_purchase_category_name_map")
@patch("shopping_bot.routes.home_page.transform_to_product_card")
def test_purchase_categories_image_urls_are_capped_to_two_ranked_products(
    mock_transform_to_product_card,
    mock_category_name_map,
    mock_pairs,
    mock_fetch_ids,
    client,
):
    mock_category_name_map.return_value = {}
    mock_fetch_ids.return_value = (
        {"user_id": "u_123", "product_ids": ["p1", "p2", "p3"], "total_count": 3, "meta": {}},
        None,
    )
    mock_pairs.return_value = [
        ("light_bites", {"id": "p1", "images": ["https://img/p1.png"]}),
        ("light_bites", {"id": "p2", "images": ["https://img/p2.png"]}),
        ("light_bites", {"id": "p3", "images": ["https://img/p3.png"]}),
    ]
    mock_transform_to_product_card.side_effect = lambda raw: {
        "id": raw["id"],
        "name": raw["id"],
        "flean_score": {"p1": 8, "p2": 10, "p3": 9}[raw["id"]],
        "flean_percentile": {"p1": 80, "p2": 96, "p3": 92}[raw["id"]],
    }

    response = client.get("/rs/api/v1/home/purchase-categories")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["categories"][0]["image_urls"] == [
        "https://img/p2.png",
        "https://img/p3.png",
    ]


@patch("shopping_bot.routes.home_page._resolve_effective_search_pincode", return_value="201303")
@patch("shopping_bot.routes.home_page._build_search_identical_card")
@patch("shopping_bot.routes.home_page.transform_to_product_card")
@patch("shopping_bot.routes.home_page._get_purchase_category_name_map")
@patch("shopping_bot.routes.home_page._build_category_product_pairs")
@patch("shopping_bot.routes.home_page._fetch_purchased_product_ids")
def test_purchase_categories_grouped_mode_returns_grouped_products(
    mock_fetch_ids,
    mock_pairs,
    mock_category_name_map,
    mock_transform_to_product_card,
    mock_build_card,
    _mock_pincode,
    client,
):
    mock_category_name_map.return_value = {
        "light_bites": "Smart Snacks",
        "breakfast_essentials": "Power Breakfast",
    }
    mock_fetch_ids.return_value = (
        {"user_id": "u_123", "product_ids": ["p1", "p2", "p3", "p4"], "total_count": 4, "meta": {}},
        None,
    )
    mock_pairs.return_value = [
        ("light_bites", {"id": "p1", "images": ["https://img/p1.png"]}),
        ("light_bites", {"id": "p2", "images": ["https://img/p2.png"]}),
        ("breakfast_essentials", {"id": "p3", "images": ["https://img/p3.png"]}),
        ("light_bites", {"id": "p2", "images": ["https://img/p2.png"]}),  # duplicate id should be de-duped
        ("light_bites", {"id": "p4", "images": ["https://img/p4.png"]}),
    ]

    ranking = {
        "p1": {"flean_score": 8, "flean_percentile": 80},
        "p2": {"flean_score": 10, "flean_percentile": 96},
        "p3": {"flean_score": 7, "flean_percentile": 70},
        "p4": {"flean_score": 9, "flean_percentile": 92},
    }
    mock_transform_to_product_card.side_effect = lambda raw: {
        "id": raw["id"],
        "name": raw["id"],
        **ranking[raw["id"]],
    }
    mock_build_card.side_effect = lambda raw, _pincode: {
        "id": raw["id"],
        "name": raw["id"],
        **ranking[raw["id"]],
    }

    response = client.get("/rs/api/v1/home/purchase-categories?mode=grouped")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    categories = payload["data"]["categories"]
    assert [item["category"] for item in categories] == ["light_bites", "breakfast_essentials"]

    assert categories[0]["name"] == "Smart Snacks"
    assert categories[0]["product_count"] == 3
    assert categories[0]["image_urls"] == ["https://img/p2.png", "https://img/p4.png"]
    assert [item["id"] for item in categories[0]["products"]] == ["p2", "p4", "p1"]

    assert categories[1]["name"] == "Power Breakfast"
    assert categories[1]["product_count"] == 1
    assert categories[1]["image_urls"] == ["https://img/p3.png"]
    assert [item["id"] for item in categories[1]["products"]] == ["p3"]

    assert payload["meta"]["total_count"] == 4
    assert payload["meta"]["input_product_ids"] == 4
    assert payload["meta"]["matched_products"] == 4
    assert payload["meta"]["category_count"] == 2
    assert payload["meta"]["pincode"] == "201303"


@patch("shopping_bot.routes.home_page._resolve_effective_search_pincode", return_value="201303")
@patch("shopping_bot.routes.home_page._build_search_identical_card")
@patch("shopping_bot.routes.home_page._build_category_product_pairs")
@patch("shopping_bot.routes.home_page._fetch_purchased_product_ids")
def test_purchase_category_products_returns_ranked_search_style_cards(
    mock_fetch_ids,
    mock_pairs,
    mock_build_card,
    _mock_pincode,
    client,
):
    mock_fetch_ids.return_value = (
        {"user_id": "u_123", "product_ids": ["p1", "p2", "p3"], "total_count": 4, "meta": {}},
        None,
    )
    mock_pairs.return_value = [
        ("f_and_b/food/light_bites", {"id": "p1"}),
        ("f_and_b/food/light_bites", {"id": "p2"}),
        ("f_and_b/food/other", {"id": "p3"}),
    ]

    def _card_for(raw, _pincode):
        pid = raw["id"]
        if pid == "p1":
            return {"id": "p1", "name": "Beta", "flean_score": 9, "flean_percentile": 92}
        if pid == "p2":
            return {"id": "p2", "name": "Alpha", "flean_score": 9, "flean_percentile": 92}
        return {"id": "p3", "name": "Gamma", "flean_score": 10, "flean_percentile": 99}

    mock_build_card.side_effect = _card_for

    response = client.get(
        "/rs/api/v1/home/purchase-categories?category=f_and_b/food/light_bites",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    products = payload["data"]["products"]
    assert [item["id"] for item in products] == ["p2", "p1"]
    assert payload["data"]["category"] == "f_and_b/food/light_bites"
    assert payload["meta"]["matched_products"] == 2


@patch("shopping_bot.routes.home_page._fetch_purchased_product_ids")
def test_purchase_categories_upstream_failure_returns_502(mock_fetch_ids, client):
    mock_fetch_ids.return_value = (None, "upstream failed")
    response = client.get("/rs/api/v1/home/purchase-categories")
    assert response.status_code == 502
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "PURCHASE_HISTORY_UNAVAILABLE"


def test_extract_category_uses_first_hierarchy_index_two():
    raw = {
        "category_hierarchies": [
            ["f_and_b", "f_and_b/food", "f_and_b/food/light_bites"],
            ["alternate", "alternate/food", "alternate/food/snacks"],
        ]
    }
    assert _extract_category_hierarchy_2(raw) == "f_and_b/food/light_bites"


def test_extract_category_supports_segments_shape():
    raw = {
        "category_hierarchies": [
            {
                "segments": ["f_and_b", "food", "veggies_and_fruits", "veggies"],
            }
        ]
    }
    assert _extract_category_hierarchy_2(raw) == "veggies_and_fruits"


def test_extract_category_rejects_flattened_hierarchy():
    raw = {
        "category_hierarchies": [
            "f_and_b",
            "f_and_b/food",
            "f_and_b/food/spreads_and_condiments",
        ]
    }
    assert _extract_category_hierarchy_2(raw) is None


def test_extract_category_rejects_category_paths_without_nested_hierarchy():
    raw = {
        "category_paths": [
            "f_and_b",
            "f_and_b/food",
            "f_and_b/food/breakfast_essentials",
        ]
    }
    assert _extract_category_hierarchy_2(raw) is None
