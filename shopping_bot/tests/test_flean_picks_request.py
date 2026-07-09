"""Tests for flean-picks GET/POST request parity."""

import json
from unittest.mock import patch

import pytest
from flask import Flask

from shopping_bot.routes.home_page import (
    FLEAN_PICKS_CATEGORIES,
    _unified_flean_picks_logic,
    bp as home_page_bp,
)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(home_page_bp, url_prefix="/rs")
    app.config["TESTING"] = True
    return app.test_client()


def _see_all_result() -> dict:
    return {
        "source": "see_all",
        "collections": [],
        "filters_applied": {"preferences": ["no_palm_oil"]},
        "fallback_meta": {"fallback_used": False, "total_tier_counts": {}},
    }


def _home_result() -> dict:
    return {
        "source": "home",
        "products": [],
        "filters_applied": {"preferences": ["no_palm_oil", "no_added_sugar"]},
        "fallback_meta": {"fallback_used": False, "total_tier_counts": {}},
    }


def _build_products(prefix: str, count: int) -> list[dict]:
    return [
        {
            "id": f"{prefix}_{idx}",
            "flean_score": 9.0,
            "flean_percentile": 100 - idx,
        }
        for idx in range(count)
    ]


def _legacy_payload(products_per_category: int) -> tuple:
    products_by_key = {
        key: _build_products(key, products_per_category)
        for key in FLEAN_PICKS_CATEGORIES
    }
    per_subcategory = {
        key: {
            "requested_count": products_per_category,
            "collected_count": len(products),
            "tier_counts": {"tier1": len(products), "tier2": 0, "tier3": 0},
            "used_fallback": False,
        }
        for key, products in products_by_key.items()
    }
    total_products = sum(len(items) for items in products_by_key.values())
    total_tier_counts = {"tier1": total_products, "tier2": 0, "tier3": 0}
    return [], [], per_subcategory, total_tier_counts, products_by_key


@patch("shopping_bot.routes.home_page._resolve_canonical_request_pincode", return_value="201303")
@patch("shopping_bot.routes.home_page._unified_flean_picks_logic")
def test_get_default_source_see_all(mock_logic, _mock_pincode, client):
    mock_logic.return_value = _see_all_result()

    resp = client.get("/rs/api/v1/home/flean-picks")

    assert resp.status_code == 200
    mock_logic.assert_called_once_with("see_all", None, effective_pincode="201303")


@patch("shopping_bot.routes.home_page._resolve_canonical_request_pincode", return_value="201303")
@patch("shopping_bot.routes.home_page._unified_flean_picks_logic")
def test_get_with_filters(mock_logic, _mock_pincode, client):
    mock_logic.return_value = _home_result()

    resp = client.get(
        "/rs/api/v1/home/flean-picks"
        "?source=home&preferences=no_palm_oil,no_added_sugar&nutrition_profiles=high_protein,low_carb"
    )

    assert resp.status_code == 200
    mock_logic.assert_called_once()
    source, user_filters = mock_logic.call_args[0]
    assert mock_logic.call_args.kwargs == {"effective_pincode": "201303"}
    assert source == "home"
    assert user_filters == {
        "preferences": ["no_palm_oil", "no_added_sugar"],
        "nutrition_profiles": ["high_protein", "low_carb"],
    }


@patch("shopping_bot.routes.home_page._resolve_canonical_request_pincode", return_value="201303")
@patch("shopping_bot.routes.home_page._unified_flean_picks_logic")
def test_get_invalid_filter_ignored(mock_logic, _mock_pincode, client):
    mock_logic.return_value = _see_all_result()

    resp = client.get("/rs/api/v1/home/flean-picks?price_range=invalid_bucket")

    assert resp.status_code == 200
    mock_logic.assert_called_once_with("see_all", None, effective_pincode="201303")


@patch("shopping_bot.routes.home_page._resolve_canonical_request_pincode", return_value="201303")
@patch("shopping_bot.routes.home_page._unified_flean_picks_logic")
def test_post_with_filters(mock_logic, _mock_pincode, client):
    mock_logic.return_value = _see_all_result()

    resp = client.post(
        "/rs/api/v1/home/flean-picks",
        json={
            "source": "see_all",
            "filters": {
                "preferences": ["no_palm_oil"],
                "nutrition_profiles": ["high_protein"],
            },
        },
    )

    assert resp.status_code == 200
    mock_logic.assert_called_once()
    source, user_filters = mock_logic.call_args[0]
    assert mock_logic.call_args.kwargs == {"effective_pincode": "201303"}
    assert source == "see_all"
    assert user_filters == {
        "preferences": ["no_palm_oil"],
        "nutrition_profiles": ["high_protein"],
    }


@patch("shopping_bot.routes.home_page._resolve_canonical_request_pincode", return_value="201303")
@patch("shopping_bot.routes.home_page._unified_flean_picks_logic")
def test_get_post_equivalent_filters(mock_logic, _mock_pincode, client):
    mock_logic.return_value = _see_all_result()

    client.get(
        "/rs/api/v1/home/flean-picks"
        "?preferences=no_palm_oil&nutrition_profiles=high_protein"
    )
    get_call = mock_logic.call_args

    mock_logic.reset_mock()
    mock_logic.return_value = _see_all_result()

    client.post(
        "/rs/api/v1/home/flean-picks",
        json={
            "filters": {
                "preferences": ["no_palm_oil"],
                "nutrition_profiles": ["high_protein"],
            },
        },
    )
    post_call = mock_logic.call_args

    assert get_call.kwargs == post_call.kwargs
    assert get_call.args[0] == post_call.args[0]
    assert get_call.args[1] == post_call.args[1]


@patch("shopping_bot.routes.home_page._resolve_canonical_request_pincode", return_value="201303")
@patch("shopping_bot.routes.home_page._unified_flean_picks_logic")
def test_post_invalid_filter_ignored(mock_logic, _mock_pincode, client):
    mock_logic.return_value = _see_all_result()

    resp = client.post(
        "/rs/api/v1/home/flean-picks",
        json={"filters": {"price_range": "invalid_bucket"}},
    )

    assert resp.status_code == 200
    mock_logic.assert_called_once_with("see_all", None, effective_pincode="201303")
    data = json.loads(resp.data)
    assert data["success"] is True


@patch("shopping_bot.routes.home_page._filter_cards_with_validation_cache")
@patch("shopping_bot.routes.home_page._legacy_unified_flean_picks_fetch")
@patch("shopping_bot.routes.home_page.os.getenv", return_value="1")
def test_home_mode_targets_three_per_subcategory(
    _mock_getenv, mock_legacy_fetch, mock_validate_cache
):
    mock_legacy_fetch.return_value = _legacy_payload(products_per_category=5)
    seen_target_counts = []

    def _validation_side_effect(products, *_args, **kwargs):
        seen_target_counts.append(kwargs["target_count"])
        return products[: kwargs["target_count"]]

    mock_validate_cache.side_effect = _validation_side_effect

    result = _unified_flean_picks_logic("home", None, effective_pincode="201303")

    assert result["source"] == "home"
    assert len(result["products"]) == 12
    assert result["fallback_meta"]["requested_products"] == 12
    for key in FLEAN_PICKS_CATEGORIES:
        assert result["fallback_meta"]["per_subcategory"][key]["collected_count"] == 3
    assert seen_target_counts == [3, 3, 3, 3]


@patch("shopping_bot.routes.home_page._filter_cards_with_validation_cache")
@patch("shopping_bot.routes.home_page._legacy_unified_flean_picks_fetch")
@patch("shopping_bot.routes.home_page.os.getenv", return_value="1")
def test_see_all_mode_still_targets_twelve_per_subcategory(
    _mock_getenv, mock_legacy_fetch, mock_validate_cache
):
    mock_legacy_fetch.return_value = _legacy_payload(products_per_category=14)
    seen_target_counts = []

    def _validation_side_effect(products, *_args, **kwargs):
        seen_target_counts.append(kwargs["target_count"])
        return products[: kwargs["target_count"]]

    mock_validate_cache.side_effect = _validation_side_effect

    result = _unified_flean_picks_logic("see_all", None, effective_pincode="201303")

    assert result["source"] == "see_all"
    assert len(result["collections"]) == 4
    assert sum(len(item["products"]) for item in result["collections"]) == 48
    assert result["fallback_meta"]["requested_products"] == 48
    for key in FLEAN_PICKS_CATEGORIES:
        assert result["fallback_meta"]["per_subcategory"][key]["collected_count"] == 12
    assert seen_target_counts == [12, 12, 12, 12]
