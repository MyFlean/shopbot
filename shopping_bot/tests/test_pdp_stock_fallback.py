"""Tests PDP in_stock precedence: Redis override -> availability -> visibility fallback."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from shopping_bot.routes.product_api import bp as product_api_bp


@pytest.fixture
def pdp_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(product_api_bp, url_prefix="/rs")
    return app.test_client()


def _base_raw_src(availability=None):
    return {
        "id": "prod-1",
        "name": "Test Product",
        "visibility": "visible",
        "availability": availability or {},
        "category_data": {"tags": {"ingredient_tags": ["no_palm_oil"]}},
    }


def _base_pdp(in_stock=True, visibility="visible"):
    return {
        "product_info": {
            "id": "prod-1",
            "name": "Test Product",
            "in_stock": in_stock,
            "visibility": visibility,
        },
        "flean_badge": {"score": 8},
    }


@patch("shopping_bot.routes.product_api._get_cached_in_stock_override", return_value=None)
@patch("shopping_bot.routes.product_api.try_resolve_canonical_pincode", return_value="201303")
@patch("shopping_bot.routes.product_api.transform_to_pdp")
@patch("shopping_bot.routes.product_api.get_es_fetcher")
def test_pdp_redis_miss_uses_availability_positive(
    mock_get_fetcher,
    mock_transform_to_pdp,
    _mock_resolve_pincode,
    _mock_cache_override,
    pdp_client,
):
    raw_src = _base_raw_src(
        availability={
            "201303": {
                "zepto": {"in_stock": False},
                "blinkit": {"in_stock": True},
                "flean": {"quantity": 0},
            }
        }
    )
    mock_get_fetcher.return_value = SimpleNamespace(get_product_by_id=lambda _pid: raw_src)
    mock_transform_to_pdp.return_value = _base_pdp(in_stock=True, visibility="visible")

    resp = pdp_client.get("/rs/api/v1/product/prod-1?pincode=201303")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["data"]["product_info"]["in_stock"] is True


@patch("shopping_bot.routes.product_api._get_cached_in_stock_override", return_value=None)
@patch("shopping_bot.routes.product_api.try_resolve_canonical_pincode", return_value="201303")
@patch("shopping_bot.routes.product_api.transform_to_pdp")
@patch("shopping_bot.routes.product_api.get_es_fetcher")
def test_pdp_redis_miss_uses_availability_negative(
    mock_get_fetcher,
    mock_transform_to_pdp,
    _mock_resolve_pincode,
    _mock_cache_override,
    pdp_client,
):
    raw_src = _base_raw_src(
        availability={
            "201303": {
                "zepto": {"in_stock": False},
                "blinkit": {"in_stock": False},
                "flean": {"quantity": 0},
            }
        }
    )
    mock_get_fetcher.return_value = SimpleNamespace(get_product_by_id=lambda _pid: raw_src)
    mock_transform_to_pdp.return_value = _base_pdp(in_stock=True, visibility="visible")

    resp = pdp_client.get("/rs/api/v1/product/prod-1?pincode=201303")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["data"]["product_info"]["in_stock"] is False


@patch("shopping_bot.routes.product_api._get_cached_in_stock_override", return_value=None)
@patch("shopping_bot.routes.product_api.try_resolve_canonical_pincode", return_value="201303")
@patch("shopping_bot.routes.product_api.transform_to_pdp")
@patch("shopping_bot.routes.product_api.get_es_fetcher")
def test_pdp_redis_miss_no_availability_signal_keeps_visibility_fallback(
    mock_get_fetcher,
    mock_transform_to_pdp,
    _mock_resolve_pincode,
    _mock_cache_override,
    pdp_client,
):
    raw_src = _base_raw_src(availability={"201303": {"zepto": {}, "blinkit": {}}})
    mock_get_fetcher.return_value = SimpleNamespace(get_product_by_id=lambda _pid: raw_src)
    # Simulate existing visibility-derived stock from transform_to_pdp.
    mock_transform_to_pdp.return_value = _base_pdp(in_stock=False, visibility="soft")

    resp = pdp_client.get("/rs/api/v1/product/prod-1?pincode=201303")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["data"]["product_info"]["in_stock"] is False


@patch("shopping_bot.routes.product_api._get_cached_in_stock_override", return_value=False)
@patch("shopping_bot.routes.product_api.try_resolve_canonical_pincode", return_value="201303")
@patch("shopping_bot.routes.product_api.transform_to_pdp")
@patch("shopping_bot.routes.product_api.get_es_fetcher")
def test_pdp_redis_hit_overrides_availability_fallback(
    mock_get_fetcher,
    mock_transform_to_pdp,
    _mock_resolve_pincode,
    _mock_cache_override,
    pdp_client,
):
    raw_src = _base_raw_src(
        availability={
            "201303": {
                "zepto": {"in_stock": True},
                "blinkit": {"in_stock": True},
                "flean": {"quantity": 10},
            }
        }
    )
    mock_get_fetcher.return_value = SimpleNamespace(get_product_by_id=lambda _pid: raw_src)
    mock_transform_to_pdp.return_value = _base_pdp(in_stock=True, visibility="visible")

    resp = pdp_client.get("/rs/api/v1/product/prod-1?pincode=201303")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["data"]["product_info"]["in_stock"] is False
