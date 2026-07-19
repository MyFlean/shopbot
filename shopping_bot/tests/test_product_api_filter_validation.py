from shopping_bot.routes.product_api import _validate_filters


def test_validate_filters_accepts_dynamic_price_range():
    validated, err = _validate_filters({"price_range": "500_999"})
    assert err is None
    assert validated == {"price_range": "500_999"}
