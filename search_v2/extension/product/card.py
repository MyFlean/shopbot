"""
Native Search V2 product-card transform.

Maps a raw OpenSearch _source document directly to the flat product-card
shape every route/frontend consumes. Decoupled from ranking (a RankedItem's
final_score/source are unpacked by the caller before this runs — see
search_v2/extension/search/core.py) so it can be reused by any capability
that just needs "raw doc -> card", not only the ranked-search path.
"""
from __future__ import annotations

from typing import Any, Dict

from shopping_bot.data_fetchers.es_products import _copy_if_present, _generate_macro_tags


def to_product_card(source: Dict[str, Any], rank: int = 0, score: float = 0.0) -> Dict[str, Any]:
    stats = source.get("stats") or {}
    nutritional = (source.get("category_data") or {}).get("nutritional") or {}
    nutrition = nutritional.get("nutri_breakdown_updated") or nutritional.get("nutri_breakdown") or {}
    claims = source.get("package_claims") or {}
    review = source.get("review_stats") or {}
    score_pcts = stats.get("adjusted_score_percentiles") or {}
    images = source.get("images") or []
    image = images[0] if images else None

    bonus_percentiles = {
        "protein":       (stats.get("protein_percentiles") or {}).get("subcategory_percentile"),
        "fiber":         (stats.get("fiber_percentiles") or {}).get("subcategory_percentile"),
        "wholefood":     (stats.get("wholefood_percentiles") or {}).get("subcategory_percentile"),
        "fortification": (stats.get("fortification_percentiles") or {}).get("subcategory_percentile"),
        "simplicity":    (stats.get("simplicity_percentiles") or {}).get("subcategory_percentile"),
    }
    penalty_percentiles = {
        "sugar":         (stats.get("sugar_penalty_percentiles") or {}).get("subcategory_percentile"),
        "sodium":        (stats.get("sodium_penalty_percentiles") or {}).get("subcategory_percentile"),
        "trans_fat":     (stats.get("trans_fat_penalty_percentiles") or {}).get("subcategory_percentile"),
        "saturated_fat": (stats.get("saturated_fat_penalty_percentiles") or {}).get("subcategory_percentile"),
        "oil":           (stats.get("oil_penalty_percentiles") or {}).get("subcategory_percentile"),
        "sweetener":     (stats.get("sweetener_penalty_percentiles") or {}).get("subcategory_percentile"),
        "calories":      (stats.get("calories_penalty_percentiles") or {}).get("subcategory_percentile"),
        "empty_food":    (stats.get("empty_food_penalty_percentiles") or {}).get("subcategory_percentile"),
    }
    health_claims = claims.get("health_claims") or []
    dietary_labels = claims.get("dietary_labels") or []
    avg_rating = review.get("avg_rating")

    protein_g = nutrition.get("protein_g") or nutrition.get("protein g")
    carbs_g = nutrition.get("carbs_g") or nutrition.get("carbohydrates g") or nutrition.get("carbs g")
    fat_g = nutrition.get("fat_g") or nutrition.get("total fat g") or nutrition.get("fat g")
    fiber_g = nutrition.get("fiber_g") or nutrition.get("fiber g")
    calories = nutrition.get("energy_kcal") or nutrition.get("energy kcal")
    # V1's exact "nutrition" shape/key (transform_to_product_card()) — kept
    # alongside "nutritional_breakdown" (a different, richer shape some
    # callers, e.g. llm_service.py's XML prompt, already depend on) rather
    # than replacing it, since mobile/frontend clients built against V1
    # read `product.nutrition.<field>`, not `nutritional_breakdown`.
    nutrition_v1_shape = {
        "protein_g": protein_g, "carbs_g": carbs_g, "fat_g": fat_g,
        "fiber_g": fiber_g, "calories": calories,
    }
    nutrition_v1_shape = {k: v for k, v in nutrition_v1_shape.items() if v is not None}

    card = {
        "rank": rank,
        "score": round(score, 6),
        "id": source.get("id"),
        "parent_id": source.get("parent_id") or source.get("id"),
        "name": source.get("name"),
        "brand": source.get("brand"),
        "price": source.get("price"),
        "mrp": source.get("mrp"),
        "currency": "INR",
        "in_stock": True,
        "category": source.get("category_group"),
        "category_paths": source.get("category_paths") or [],
        "description": source.get("description"),
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "fiber_g": fiber_g,
        "calories": calories,
        "macro_tags": _generate_macro_tags(nutrition_v1_shape),
        "qty": nutritional.get("qty", ""),
        "size": source.get("size", ""),
        "nutrition": nutrition_v1_shape or None,
        "nutritional_breakdown": nutrition,
        "nutritional_qty": nutritional.get("qty", ""),
        "health_claims": health_claims if isinstance(health_claims, list) else [],
        "dietary_labels": dietary_labels if isinstance(dietary_labels, list) else [],
        "package_claims": claims,
        "flean_percentile": score_pcts.get("subcategory_percentile"),
        "flean_score": (source.get("flean_score") or {}).get("adjusted_score"),
        "bonus_percentiles": {k: v for k, v in bonus_percentiles.items() if v is not None},
        "penalty_percentiles": {k: v for k, v in penalty_percentiles.items() if v is not None},
        "image": image,
        "image_url": image,
        "ingredients": (source.get("ingredients") or {}).get("raw_text"),
        "avg_rating": avg_rating,
        "total_reviews": review.get("total_reviews"),
        "rating": avg_rating,
        "review_stats": review,
        "variants": source.get("variants") or [],
        "skin_compatibility": source.get("skin_compatibility", {}),
        "efficacy": source.get("efficacy", {}),
        "side_effects": source.get("side_effects", {}),
        "visibility": source.get("visibility", "visible"),
        "has_lab_report": bool(((source.get("category_data") or {}).get("lab_reports") or {}).get("url")),
    }
    # Passthrough-only-if-present fields (V1's transform_to_product_card()
    # uses the same _copy_if_present() pattern) — found missing during the
    # final V1-vs-V2 parity audit.
    _copy_if_present(source, card, "scheduled")
    return card
