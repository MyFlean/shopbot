"""Smoke tests for additives/preservatives PDP score cards and watch_outs."""

from shopping_bot.data_fetchers.es_products import transform_to_pdp


def _base_src(**overrides):
    src = {
        "id": "test-1",
        "name": "Test Product",
        "category_data": {
            "processing_type": "minimally_processed",
            "tags": {"highlight_tags": {}},
        },
        "stats": {},
    }
    for key, val in overrides.items():
        if key == "ingredients_tags":
            src.setdefault("category_data", {})["tags"] = {
                "highlight_tags": {"ingredients_tags": val}
            }
        elif key == "processing_type":
            src["category_data"]["processing_type"] = val
        elif key == "stats":
            src["stats"] = val
        else:
            src[key] = val
    return src


def test_additives_average_from_penalty_percentile():
    src = _base_src(
        stats={
            "additives_penalty_percentiles": {"subcategory_percentile": 38.1},
        }
    )
    pdp = transform_to_pdp(src)
    card = pdp["score_cards"]["additives"]
    assert card["value"] == "Average"
    assert card["theme"] == "average"
    assert card["percentile"] == 38.1
    assert card["subtitle"] == "Percentile: 38"


def test_additives_percentile_with_tag_subtitle_new():
    src = _base_src(
        stats={
            "additives_penalty_percentiles": {"subcategory_percentile": 60.0},
        },
        ingredients_tags={
            "positive": [],
            "neutral": ["artificial_additives_present"],
            "negative": [],
        },
    )
    pdp = transform_to_pdp(src)
    card = pdp["score_cards"]["additives"]
    assert card["value"] == "Caution"
    assert card["theme"] == "subpar"
    assert len(card["subtitle_new"]) == 1
    assert card["subtitle_new"][0]["tag_label"]


def test_watch_outs_ultra_processed_static_subtitle():
    src = _base_src(
        processing_type="ultra_processed",
        ingredients_tags={"positive": [], "neutral": [], "negative": []},
    )
    src["stats"] = {
        "adjusted_score_percentiles": {"subcategory_percentile": 80},
    }
    pdp = transform_to_pdp(src)
    assert "watch_outs" in pdp["score_cards"]
    assert "flean_rank" not in pdp["score_cards"]
    wo = pdp["score_cards"]["watch_outs"]
    assert wo["value"] == "Processed"
    assert wo["subtitle"] == "Ultra Processed"
    assert "subtitle_new" not in wo


def test_preservatives_elite_preservative_free():
    src = _base_src(
        ingredients_tags={
            "positive": ["preservative_free"],
            "neutral": [],
            "negative": [],
        }
    )
    pdp = transform_to_pdp(src)
    card = pdp["score_cards"]["preservatives"]
    assert card["value"] == "None"
    assert card["theme"] == "elite"


def test_preservatives_villain_preservative_present():
    src = _base_src(
        ingredients_tags={
            "positive": [],
            "neutral": [],
            "negative": ["preservative_present"],
        }
    )
    pdp = transform_to_pdp(src)
    card = pdp["score_cards"]["preservatives"]
    assert card["value"] == "Harmful"
    assert card["theme"] == "villain"


def test_additives_omitted_when_no_percentile():
    src = _base_src(
        ingredients_tags={
            "positive": ["high_protein"],
            "neutral": ["artificial_additives_present"],
            "negative": [],
        }
    )
    pdp = transform_to_pdp(src)
    assert "additives" not in pdp["score_cards"]


def test_both_domain_cards_when_both_match():
    src = _base_src(
        stats={
            "additives_penalty_percentiles": {"subcategory_percentile": 38.1},
        },
        ingredients_tags={
            "positive": ["preservative_free"],
            "neutral": ["artificial_additives_present"],
            "negative": [],
        },
    )
    pdp = transform_to_pdp(src)
    assert "additives" in pdp["score_cards"]
    assert "preservatives" in pdp["score_cards"]
