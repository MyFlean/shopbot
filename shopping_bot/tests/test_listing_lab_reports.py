"""Tests has_lab_report on home listing cards and PDP alternatives."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from shopping_bot.routes.home_page import (
    BEST_SELLING_CATEGORY_PATHS,
    _fetch_products_by_ids,
    _listing_card_from_src,
    bp as home_page_bp,
)
from shopping_bot.routes.product_api import (
    _enrich_listing_card,
    bp as product_api_bp,
)


RAW_WITH_REPORT = {
    "id": "prod-with-report",
    "name": "With Report",
    "visibility": "visible",
    "brand": "BrandA",
    "price": 99,
    "category_data": {
        "nutritional": {"qty": "100 g"},
        "lab_reports": {"url": "https://cdn.example.com/lab.pdf"},
    },
    "flean_score": {"adjusted_score": 90},
    "stats": {},
}

RAW_WITHOUT_REPORT = {
    "id": "prod-without-report",
    "name": "Without Report",
    "visibility": "visible",
    "brand": "BrandB",
    "price": 79,
    "category_data": {"nutritional": {"qty": "100 g"}},
    "flean_score": {"adjusted_score": 80},
    "stats": {},
}


@pytest.fixture
def home_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(home_page_bp, url_prefix="/rs")
    return app.test_client()


@pytest.fixture
def product_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(product_api_bp, url_prefix="/rs")
    return app.test_client()


def test_enrich_listing_card_sets_has_lab_report():
    card = {"id": "prod-with-report", "name": "With Report"}
    enriched = _enrich_listing_card(card, RAW_WITH_REPORT)
    assert enriched["has_lab_report"] is True

    card2 = {"id": "prod-without-report", "name": "Without Report"}
    enriched2 = _enrich_listing_card(card2, RAW_WITHOUT_REPORT)
    assert enriched2["has_lab_report"] is False


def test_listing_card_from_src_sets_has_lab_report():
    card = _listing_card_from_src(RAW_WITH_REPORT)
    assert card is not None
    assert card["has_lab_report"] is True

    card2 = _listing_card_from_src(RAW_WITHOUT_REPORT)
    assert card2 is not None
    assert card2["has_lab_report"] is False


@patch("shopping_bot.routes.home_page.get_es_fetcher")
def test_fetch_products_by_ids_sets_has_lab_report(mock_get_fetcher):
    mock_get_fetcher.return_value = SimpleNamespace(
        search_by_ids=lambda _ids: [RAW_WITH_REPORT, RAW_WITHOUT_REPORT]
    )

    cards = _fetch_products_by_ids(["prod-with-report", "prod-without-report"])
    by_id = {card["id"]: card for card in cards}

    assert by_id["prod-with-report"]["has_lab_report"] is True
    assert by_id["prod-without-report"]["has_lab_report"] is False


@patch(
    "shopping_bot.routes.home_page._filter_cards_with_validation_cache",
    side_effect=lambda cards, *args, **kwargs: cards,
)
@patch("shopping_bot.routes.home_page._resolve_canonical_request_pincode", return_value=None)
@patch("shopping_bot.routes.home_page.get_es_fetcher")
def test_best_selling_sets_has_lab_report(
    mock_get_fetcher,
    _mock_pincode,
    _mock_validation,
    home_client,
):
    mock_get_fetcher.return_value = SimpleNamespace(
        best_selling_by_category_paths_agg=lambda **_kwargs: {
            BEST_SELLING_CATEGORY_PATHS[0]: [RAW_WITH_REPORT],
            BEST_SELLING_CATEGORY_PATHS[1]: [RAW_WITHOUT_REPORT],
            BEST_SELLING_CATEGORY_PATHS[2]: [],
        },
        search_by_category_paths=lambda **_kwargs: [],
        search_by_ids=lambda _ids: [],
    )

    resp = home_client.get("/rs/api/v1/home/best-selling")
    assert resp.status_code == 200
    payload = resp.get_json()
    products = payload["data"]["products"]
    by_id = {item["id"]: item for item in products}

    assert by_id["prod-with-report"]["has_lab_report"] is True
    assert by_id["prod-without-report"]["has_lab_report"] is False


@patch(
    "shopping_bot.routes.home_page._filter_cards_with_validation_cache",
    side_effect=lambda cards, *args, **kwargs: cards,
)
@patch("shopping_bot.routes.home_page._resolve_canonical_request_pincode", return_value=None)
@patch("shopping_bot.routes.home_page.get_es_fetcher")
def test_curated_home_sets_has_lab_report(
    mock_get_fetcher,
    _mock_pincode,
    _mock_validation,
    home_client,
    monkeypatch,
):
    monkeypatch.setenv("FLEAN_PICKS_FORCE_LEGACY", "true")

    def _search_by_category_paths(paths, **_kwargs):
        path_set = set(paths or [])
        if "f_and_b/food/light_bites/energy_bars" in path_set:
            return [RAW_WITH_REPORT]
        if "f_and_b/food/spreads_and_condiments/peanut_butter" in path_set:
            return [RAW_WITHOUT_REPORT]
        return []

    mock_get_fetcher.return_value = SimpleNamespace(
        search_by_category_paths=_search_by_category_paths,
        flean_picks_by_subcategories_agg=lambda **_kwargs: None,
    )

    resp = home_client.get("/rs/api/v1/home/curated")
    assert resp.status_code == 200
    payload = resp.get_json()
    products = payload["data"]["products"]
    by_id = {item["id"]: item for item in products}

    assert by_id["prod-with-report"]["has_lab_report"] is True
    assert by_id["prod-without-report"]["has_lab_report"] is False


@patch("shopping_bot.routes.product_api.get_es_fetcher")
def test_alternatives_sets_has_lab_report(mock_get_fetcher, product_client, monkeypatch):
    monkeypatch.setenv("SEARCH_ENGINE", "v1")
    mock_get_fetcher.return_value = SimpleNamespace(
        search_healthier_alternatives=lambda _pid, limit=5: {
            "source_product": RAW_WITHOUT_REPORT,
            "alternatives": [RAW_WITH_REPORT],
        }
    )

    resp = product_client.get("/rs/api/v1/product/source-prod/alternatives")
    assert resp.status_code == 200
    payload = resp.get_json()
    data = payload["data"]

    assert data["source_product"]["has_lab_report"] is False
    assert len(data["alternatives"]) == 1
    assert data["alternatives"][0]["has_lab_report"] is True


@patch("shopping_bot.routes.product_api.get_es_fetcher")
def test_alternatives_filters_to_add_to_cart_only(mock_get_fetcher, product_client, monkeypatch):
    monkeypatch.setenv("SEARCH_ENGINE", "v1")
    keep_alt = {
        "id": "alt-keep",
        "name": "Keep Me",
        "visibility": "visible",
        "brand": "BrandK",
        "price": 120,
        "category_data": {
            "nutritional": {"qty": "100 g"},
            "tags": {"ingredient_tags": ["no_palm_oil"]},
        },
        "flean_score": {"adjusted_score": 90},
        "stats": {},
    }
    drop_soft = {
        "id": "alt-soft",
        "name": "Soft Visibility",
        "visibility": "soft",
        "brand": "BrandS",
        "price": 130,
        "category_data": {
            "nutritional": {"qty": "100 g"},
            "tags": {"ingredient_tags": ["no_palm_oil"]},
        },
        "flean_score": {"adjusted_score": 90},
        "stats": {},
    }
    drop_low_score = {
        "id": "alt-low",
        "name": "Low Score",
        "visibility": "visible",
        "brand": "BrandL",
        "price": 110,
        "category_data": {
            "nutritional": {"qty": "100 g"},
            "tags": {"ingredient_tags": ["no_palm_oil"]},
        },
        "flean_score": {"adjusted_score": 40},
        "stats": {},
    }

    mock_get_fetcher.return_value = SimpleNamespace(
        search_healthier_alternatives=lambda _pid, limit=5: {
            "source_product": RAW_WITHOUT_REPORT,
            "alternatives": [keep_alt, drop_soft, drop_low_score],
        }
    )

    resp = product_client.get("/rs/api/v1/product/source-prod/alternatives")
    assert resp.status_code == 200
    payload = resp.get_json()
    data = payload["data"]
    alt_ids = [item["id"] for item in data["alternatives"]]
    assert alt_ids == ["alt-keep"]


@patch("shopping_bot.routes.product_api.get_es_fetcher")
def test_alternatives_returns_empty_when_all_non_add_to_cart(
    mock_get_fetcher, product_client, monkeypatch
):
    monkeypatch.setenv("SEARCH_ENGINE", "v1")
    drop_palm = {
        "id": "alt-palm",
        "name": "Palm Oil",
        "visibility": "visible",
        "brand": "BrandP",
        "price": 140,
        "category_data": {
            "nutritional": {"qty": "100 g"},
            "tags": {"ingredient_tags": ["contains_palm_oil"]},
        },
        "flean_score": {"adjusted_score": 90},
        "stats": {},
    }
    drop_soft = {
        "id": "alt-soft",
        "name": "Soft Visibility",
        "visibility": "soft",
        "brand": "BrandS",
        "price": 130,
        "category_data": {
            "nutritional": {"qty": "100 g"},
            "tags": {"ingredient_tags": ["no_palm_oil"]},
        },
        "flean_score": {"adjusted_score": 90},
        "stats": {},
    }

    mock_get_fetcher.return_value = SimpleNamespace(
        search_healthier_alternatives=lambda _pid, limit=5: {
            "source_product": RAW_WITHOUT_REPORT,
            "alternatives": [drop_palm, drop_soft],
        }
    )

    resp = product_client.get("/rs/api/v1/product/source-prod/alternatives")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["data"]["alternatives"] == []


@patch("shopping_bot.routes.product_api.get_es_fetcher")
def test_alternatives_returns_404_when_source_missing(mock_get_fetcher, product_client, monkeypatch):
    monkeypatch.setenv("SEARCH_ENGINE", "v1")
    mock_get_fetcher.return_value = SimpleNamespace(
        search_healthier_alternatives=lambda _pid, limit=5: {
            "source_product": None,
            "alternatives": [],
        }
    )

    resp = product_client.get("/rs/api/v1/product/source-prod/alternatives")
    assert resp.status_code == 404
    payload = resp.get_json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "PRODUCT_NOT_FOUND"


@patch("search_v2.extension.recommendations.similar_products")
def test_alternatives_v2_uses_single_fetch_cta_metadata(
    mock_similar_products, product_client, monkeypatch
):
    monkeypatch.setenv("SEARCH_ENGINE", "v2")
    mock_similar_products.return_value = {
        "source_product": {"id": "source-prod", "name": "Source"},
        "alternatives": [
            {"id": "alt-keep", "name": "Keep", "visibility": "visible", "flean_score": 9},
            {"id": "alt-drop", "name": "Drop", "visibility": "visible", "flean_score": 9},
        ],
        "alt_cta_meta_by_id": {
            "alt-keep": {"visibility": "visible", "has_palm_oil": False},
            "alt-drop": {"visibility": "visible", "has_palm_oil": True},
        },
    }

    resp = product_client.get("/rs/api/v1/product/source-prod/alternatives")
    assert resp.status_code == 200
    payload = resp.get_json()
    alt_ids = [item["id"] for item in payload["data"]["alternatives"]]
    assert alt_ids == ["alt-keep"]


@patch("search_v2.extension.recommendations.similar_products")
def test_alternatives_v2_trims_filtered_results_to_five(
    mock_similar_products, product_client, monkeypatch
):
    monkeypatch.setenv("SEARCH_ENGINE", "v2")
    alternatives = []
    cta_meta = {}
    for idx in range(1, 8):
        alt_id = f"alt-{idx}"
        alternatives.append({"id": alt_id, "name": f"Alt {idx}", "visibility": "visible", "flean_score": 9})
        cta_meta[alt_id] = {"visibility": "visible", "has_palm_oil": False}
    mock_similar_products.return_value = {
        "source_product": {"id": "source-prod", "name": "Source"},
        "alternatives": alternatives,
        "alt_cta_meta_by_id": cta_meta,
    }

    resp = product_client.get("/rs/api/v1/product/source-prod/alternatives")
    assert resp.status_code == 200
    payload = resp.get_json()
    returned_ids = [item["id"] for item in payload["data"]["alternatives"]]
    assert len(returned_ids) == 5
    assert returned_ids == ["alt-1", "alt-2", "alt-3", "alt-4", "alt-5"]
