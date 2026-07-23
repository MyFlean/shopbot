"""Tests unified search lab-report annotations, ordering, and dynamic filters."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from shopping_bot.routes.unified_search import bp as unified_search_bp


@pytest.fixture
def unified_search_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(unified_search_bp, url_prefix="/rs")
    return app.test_client()


@patch("shopping_bot.routes.unified_search._search_engine", return_value="v1")
@patch("shopping_bot.routes.unified_search.transform_to_product_card")
@patch("shopping_bot.routes.unified_search.get_es_fetcher")
def test_unified_search_sets_has_lab_report_per_product(
    mock_get_fetcher,
    mock_transform_to_product_card,
    _mock_search_engine,
    unified_search_client,
):
    raw_products = [
        {
            "id": "prod-with-report",
            "visibility": "visible",
            "category_data": {
                "tags": {"ingredient_tags": ["no_palm_oil"]},
                "lab_reports": {"url": "https://cdn.example.com/lab-report-a.pdf"},
            },
        },
        {
            "id": "prod-without-report",
            "visibility": "visible",
            "category_data": {"tags": {"ingredient_tags": ["no_palm_oil"]}},
        },
    ]
    mock_get_fetcher.return_value = SimpleNamespace(
        search_products_unified=lambda **_kwargs: {
            "products": raw_products,
            "meta": {"total": 2},
        }
    )

    def _card_for(raw):
        return {
            "id": raw["id"],
            "parent_id": "parent-1",
            "name": raw["id"],
            "visibility": "visible",
            "flean_score": 8,
            "variants": [{"id": "variant-1", "price": 99.0, "mrp": 120.0, "size": "500 g", "image": "img"}],
        }

    mock_transform_to_product_card.side_effect = _card_for

    resp = unified_search_client.get("/rs/v1/search?query=chips")
    assert resp.status_code == 200
    assert mock_get_fetcher.called
    payload = resp.get_json()
    products = payload["data"]["products"]
    assert len(products) == 2

    by_id = {item["id"]: item for item in products}
    assert by_id["prod-with-report"]["has_lab_report"] is True
    assert by_id["prod-without-report"]["has_lab_report"] is False
    assert by_id["prod-with-report"]["parent_id"] == "parent-1"
    assert by_id["prod-with-report"]["variants"][0]["id"] == "variant-1"
    assert payload["data"]["filters"] == []


@patch("shopping_bot.routes.unified_search._search_engine", return_value="v1")
@patch("shopping_bot.routes.unified_search.transform_to_product_card")
@patch("shopping_bot.routes.unified_search.get_es_fetcher")
def test_unified_search_returns_v1_dynamic_filters(
    mock_get_fetcher,
    mock_transform_to_product_card,
    _mock_search_engine,
    unified_search_client,
):
    raw_products = [{"id": "prod-1", "visibility": "visible", "category_data": {}}]
    mock_get_fetcher.return_value = SimpleNamespace(
        search_products_unified=lambda **_kwargs: {
            "products": raw_products,
            "filters": [
                {
                    "id": "filter_price",
                    "title": "Price",
                    "titleKey": "price_range",
                    "items": [
                        {
                            "id": "price_0_100",
                            "labelKey": "0_99",
                            "label": "Below Rs 100",
                            "value": "0-100",
                            "count": 5,
                            "isPreSelected": False,
                        }
                    ],
                }
            ],
            "meta": {"total": 1},
        }
    )
    mock_transform_to_product_card.return_value = {
        "id": "prod-1",
        "parent_id": "parent-1",
        "name": "prod-1",
        "visibility": "visible",
        "flean_score": 8,
        "variants": [{"id": "variant-1", "price": 99.0, "mrp": 120.0, "size": "500 g", "image": "img"}],
    }

    resp = unified_search_client.get("/rs/v1/search?query=chips")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["data"]["filters"][0]["id"] == "filter_price"


@patch("shopping_bot.routes.unified_search._search_engine", return_value="v2")
@patch("shopping_bot.routes.unified_search.transform_to_product_card")
@patch("shopping_bot.routes.unified_search.get_search_gateway")
def test_unified_search_returns_v2_dynamic_filters(
    mock_get_gateway,
    mock_transform_to_product_card,
    _mock_search_engine,
    unified_search_client,
):
    gateway = SimpleNamespace(
        search=lambda _params: {
            "products": [{"id": "prod-1", "visibility": "visible", "category_data": {}}],
            "filters": [
                {
                    "id": "filter_flean_score",
                    "title": "Flean Score",
                    "titleKey": "flean_score",
                    "items": [
                        {
                            "id": "9_plus",
                            "labelKey": "9_plus",
                            "label": "9+ (Excellent)",
                            "value": 9,
                            "count": 2,
                            "isPreSelected": False,
                        }
                    ],
                }
            ],
            "meta": {"total_hits": 1, "took_ms": 10},
        }
    )
    mock_get_gateway.return_value = gateway
    mock_transform_to_product_card.return_value = {
        "id": "prod-1",
        "parent_id": "parent-1",
        "name": "prod-1",
        "visibility": "visible",
        "flean_score": 9,
        "variants": [{"id": "variant-1", "price": 99.0, "mrp": 120.0, "size": "500 g", "image": "img"}],
    }

    resp = unified_search_client.get("/rs/v1/search?query=chips")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["data"]["filters"][0]["id"] == "filter_flean_score"


@patch("shopping_bot.routes.unified_search._search_engine", return_value="v1")
@patch("shopping_bot.routes.unified_search.transform_to_product_card")
@patch("shopping_bot.routes.unified_search.get_es_fetcher")
def test_unified_search_preserves_fetcher_rank_order(
    mock_get_fetcher,
    mock_transform_to_product_card,
    _mock_search_engine,
    unified_search_client,
):
    raw_products = [
        {
            "id": "higher-rank-non-report",
            "visibility": "visible",
            "_score": 99.0,
            "category_data": {"tags": {"ingredient_tags": ["no_palm_oil"]}},
        },
        {
            "id": "lower-rank-with-report",
            "visibility": "visible",
            "_score": 80.0,
            "category_data": {
                "tags": {"ingredient_tags": ["no_palm_oil"]},
                "lab_reports": {"url": "https://cdn.example.com/lab-report-a.pdf"},
            },
        },
    ]
    mock_get_fetcher.return_value = SimpleNamespace(
        search_products_unified=lambda **_kwargs: {
            "products": raw_products,
            "meta": {"total": 2},
        }
    )

    mock_transform_to_product_card.side_effect = lambda raw: {
        "id": raw["id"],
        "name": raw["id"],
        "visibility": "visible",
        "flean_score": 8,
    }

    resp = unified_search_client.get("/rs/v1/search?query=paneer&sort_by=relevance")
    assert resp.status_code == 200
    payload = resp.get_json()
    products = payload["data"]["products"]
    assert [item["id"] for item in products] == [
        "higher-rank-non-report",
        "lower-rank-with-report",
    ]
