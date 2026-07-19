from shopping_bot.data_fetchers.dynamic_search_filters import (
    build_dynamic_price_ranges,
    parse_dynamic_filters_from_aggs,
)


def test_build_dynamic_price_ranges_returns_three_to_four_ranges():
    ranges = build_dynamic_price_ranges(37.0, 612.0, target_buckets=4)
    assert 3 <= len(ranges) <= 4
    assert all("from" in r and "to" in r and "key" in r for r in ranges)
    assert ranges[0]["from"] <= 37.0
    assert ranges[-1]["to"] >= 612.0


def test_parse_dynamic_filters_skips_zero_count_buckets():
    aggs = {
        "price_ranges": {
            "buckets": {
                "0_99": {"key": "0_99", "from": 0, "to": 100, "doc_count": 3},
                "100_199": {"key": "100_199", "from": 100, "to": 200, "doc_count": 0},
            }
        },
        "flean_score_counts": {
            "buckets": {
                "9_plus": {"doc_count": 0},
                "8_plus": {"doc_count": 4},
                "7_plus": {"doc_count": 2},
            }
        },
        "dietary_preferences": {
            "buckets": [
                {"key": "gluten_free", "doc_count": 5},
                {"key": "dairy_free", "doc_count": 0},
            ]
        },
        "ingredient_preferences": {
            "buckets": [
                {"key": "no_palm_oil", "doc_count": 6},
                {"key": "no_maida", "doc_count": 0},
            ]
        },
        "nutrition_profile_counts": {
            "buckets": {
                "high_protein": {"doc_count": 8},
                "low_carb": {"doc_count": 0},
                "high_fiber": {"doc_count": 2},
                "low_sugar": {"doc_count": 4},
                "low_sodium": {"doc_count": 0},
                "low_fat": {"doc_count": 0},
            }
        },
    }

    filters = parse_dynamic_filters_from_aggs(aggs)
    by_id = {group["id"]: group for group in filters}

    assert "filter_price" in by_id
    assert len(by_id["filter_price"]["items"]) == 1
    assert by_id["filter_price"]["items"][0]["count"] == 3

    assert "filter_flean_score" in by_id
    flean_label_keys = [item["labelKey"] for item in by_id["filter_flean_score"]["items"]]
    assert flean_label_keys == ["8_plus", "7_plus"]

    assert "filter_preferences" in by_id
    assert [item["value"] for item in by_id["filter_preferences"]["items"]] == ["gluten_free"]

    assert "ingredient_preferences" in by_id
    assert [item["value"] for item in by_id["ingredient_preferences"]["items"]] == ["no_palm_oil"]
    assert "filter_nutrition" in by_id
    assert [item["value"] for item in by_id["filter_nutrition"]["items"]] == [
        "high_protein",
        "high_fiber",
        "low_sugar",
    ]


def test_parse_dynamic_filters_uses_price_bucket_map_key_when_bucket_key_missing():
    aggs = {
        "price_ranges": {
            "buckets": {
                "0_99": {"from": 0, "to": 100, "doc_count": 112},
                "100_199": {"from": 100, "to": 200, "doc_count": 5},
                "200_299": {"from": 200, "to": 300, "doc_count": 2},
            }
        },
        "flean_score_counts": {"buckets": {}},
        "dietary_preferences": {"buckets": []},
        "ingredient_preferences": {"buckets": []},
        "nutrition_profile_counts": {"buckets": {}},
    }

    filters = parse_dynamic_filters_from_aggs(aggs)
    by_id = {group["id"]: group for group in filters}
    price_items = by_id["filter_price"]["items"]
    assert [item["labelKey"] for item in price_items] == ["0_99", "100_199", "200_299"]
    assert [item["id"] for item in price_items] == ["price_below_100", "price_100_200", "price_200_300"]


def test_parse_dynamic_filters_excludes_pcos_friendly():
    aggs = {
        "dietary_preferences": {"buckets": []},
        "ingredient_preferences": {"buckets": [{"key": "pcos_friendly", "doc_count": 7}]},
        "flean_score_counts": {"buckets": {}},
    }

    filters = parse_dynamic_filters_from_aggs(aggs)
    by_id = {group["id"]: group for group in filters}

    assert "filter_preferences" not in by_id
    assert "ingredient_preferences" not in by_id
    assert "filter_nutrition" not in by_id
