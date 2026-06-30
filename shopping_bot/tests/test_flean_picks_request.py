"""Tests for flean-picks GET/POST request parity."""

import json
from unittest.mock import patch

import pytest
from flask import Flask

from shopping_bot.routes.home_page import bp as home_page_bp


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
