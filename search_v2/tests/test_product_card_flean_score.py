"""Flean score display on V2 product cards must match PDP badge semantics."""

from search_v2.extension.product.card import to_product_card
from shopping_bot.data_fetchers.es_products import transform_to_pdp


def _minimal_source(**flean_score):
    return {
        "id": "test-1",
        "name": "Test Product",
        "category_data": {"nutritional": {}},
        "flean_score": flean_score,
    }


def test_floor_product_shows_badge_1_not_10():
    src = _minimal_source(adjusted_score=10, adjusted_score_label=1.0)
    assert to_product_card(src)["flean_score"] == 1
    assert transform_to_pdp(src)["flean_badge"]["score"] == 1


def test_healthy_product_shows_badge_10():
    src = _minimal_source(adjusted_score=100, adjusted_score_label=10.0)
    assert to_product_card(src)["flean_score"] == 10
    assert transform_to_pdp(src)["flean_badge"]["score"] == 10


def test_half_point_label_rounds_like_pdp():
    src = _minimal_source(adjusted_score=54.97, adjusted_score_label=5.5)
    assert to_product_card(src)["flean_score"] == 6
    assert transform_to_pdp(src)["flean_badge"]["score"] == 6


def test_fallback_to_adjusted_score_when_label_missing():
    src = _minimal_source(adjusted_score=85)
    assert to_product_card(src)["flean_score"] == 9
    assert transform_to_pdp(src)["flean_badge"]["score"] == 9


def test_v2_card_matches_pdp_for_chilli_oil_shape():
    src = _minimal_source(
        adjusted_score=10,
        adjusted_score_label=1.0,
        raw_score=-31.65,
        score_base=80,
    )
    card_score = to_product_card(src)["flean_score"]
    pdp_score = transform_to_pdp(src)["flean_badge"]["score"]
    assert card_score == pdp_score == 1
