"""Tests unified search lab-report annotations and ordering behavior."""

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


@patch("shopping_bot.routes.unified_search.transform_to_product_card")
@patch("shopping_bot.routes.unified_search.get_es_fetcher")
def test_unified_search_sets_has_lab_report_per_product(
    mock_get_fetcher,
    mock_transform_to_product_card,
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
            "name": raw["id"],
            "visibility": "visible",
            "flean_score": 8,
        }

    mock_transform_to_product_card.side_effect = _card_for

    resp = unified_search_client.get("/rs/v1/search?query=chips")
    assert resp.status_code == 200
    payload = resp.get_json()
    products = payload["data"]["products"]
    assert len(products) == 2

    by_id = {item["id"]: item for item in products}
    assert by_id["prod-with-report"]["has_lab_report"] is True
    assert by_id["prod-without-report"]["has_lab_report"] is False


@patch("shopping_bot.routes.unified_search.transform_to_product_card")
@patch("shopping_bot.routes.unified_search.get_es_fetcher")
def test_unified_search_preserves_fetcher_rank_order(
    mock_get_fetcher,
    mock_transform_to_product_card,
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
