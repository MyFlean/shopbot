"""Tests unified search has_lab_report flag derivation (Search V2)."""

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


@patch("shopping_bot.routes.unified_search._extract_lab_report_url")
@patch("shopping_bot.routes.unified_search._derive_in_stock_from_availability", return_value=True)
@patch("shopping_bot.routes.unified_search._resolve_pdp_cta", return_value={"type": "add_to_cart"})
@patch("shopping_bot.routes.unified_search.transform_to_product_card")
@patch("shopping_bot.routes.unified_search.v2_search")
def test_unified_search_sets_has_lab_report_per_product(
    mock_v2_search,
    mock_transform_to_product_card,
    _mock_cta,
    _mock_stock,
    mock_extract_lab,
    unified_search_client,
):
    cards = [
        {"id": "prod-with-report", "parent_id": "parent-1", "variants": [{"id": "variant-1"}], "name": "A"},
        {"id": "prod-without-report", "parent_id": "parent-2", "variants": [], "name": "B"},
    ]
    raws = [
        {"id": "prod-with-report", "category_data": {"lab_reports": {"url": "https://cdn.example.com/lab-report-a.pdf"}}},
        {"id": "prod-without-report", "category_data": {}},
    ]

    def _transform(raw):
        by_id = {c["id"]: dict(c) for c in cards}
        return by_id.get(raw.get("id"))

    mock_transform_to_product_card.side_effect = _transform
    mock_extract_lab.side_effect = lambda raw: ((raw.get("category_data") or {}).get("lab_reports") or {}).get("url")
    mock_v2_search.return_value = {
        "products": raws,
        "filters": [],
        "meta": {"total_hits": 2, "took_ms": 1},
    }

    resp = unified_search_client.post("/rs/v1/search", json={"query": "oats", "size": 2})
    assert resp.status_code == 200
    payload = resp.get_json()
    products = payload["data"]["products"]
    by_id = {item["id"]: item for item in products}
    assert by_id["prod-with-report"]["has_lab_report"] is True
    assert by_id["prod-without-report"]["has_lab_report"] is False
