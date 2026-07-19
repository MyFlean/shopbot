from shopping_bot.routes.unified_search import _v1_filters_to_gw_params


def test_v1_to_v2_translation_maps_flean_score_to_min_badge():
    out = _v1_filters_to_gw_params({"flean_score": "8_plus"})
    assert out.get("min_flean_score") == 8.0
    assert "min_flean_percentile" not in out


def test_v1_to_v2_translation_maps_dynamic_price_range():
    out = _v1_filters_to_gw_params({"price_range": "500_999"})
    assert out.get("price_min") == 500.0
    assert out.get("price_max") == 999.0
