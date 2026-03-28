# shopping_bot/routes/simple_search.py
"""
Simple Search Endpoint - Direct Elasticsearch Query Search

Takes a user query and performs a direct Elasticsearch search.
Returns standardized product cards with macro tags, nutrition, and quantity.
Supports sorting and filtering.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher, transform_to_product_card

log = logging.getLogger(__name__)
bp = Blueprint("simple_search", __name__)


# ============================================================================
# Sort & Filter Constants
# ============================================================================

VALID_SORT_OPTIONS = {
    "relevance",      # Default: ES score + flean_percentile
    "price_asc",      # Price Low to High
    "price_desc",     # Price High to Low
    "protein_desc",   # Protein High to Low
    "fiber_desc",     # Fibre High to Low
    "fat_asc",        # Fat Low to High
}

VALID_PRICE_RANGES = {"below_99", "100_249", "250_499", "above_500"}
VALID_FLEAN_SCORES = {"10", "9_plus", "8_plus", "7_plus"}
VALID_PREFERENCES = {"no_palm_oil", "no_added_sugar", "no_harmful_additives", "preservative_free"}
VALID_DIETARY = {"dairy_free", "gluten_free", "nut_free", "pcos_friendly"}
VALID_FOOD_TYPES = {"veg", "nonveg"}
NUTRITION_MAX = {"protein": 40, "carbs": 100, "fat": 100}
NUTRITION_STEP = {"protein": 10, "carbs": 20, "fat": 20}


def _validate_filters(filters: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate the filters object from the request."""
    if not filters:
        return None, None
    if not isinstance(filters, dict):
        return None, "filters must be an object"

    validated: Dict[str, Any] = {}

    price_range = filters.get("price_range")
    if price_range:
        if price_range not in VALID_PRICE_RANGES:
            return None, f"Invalid price_range: '{price_range}'. Valid: {sorted(VALID_PRICE_RANGES)}"
        validated["price_range"] = price_range

    flean_score = filters.get("flean_score")
    if flean_score:
        if flean_score not in VALID_FLEAN_SCORES:
            return None, f"Invalid flean_score: '{flean_score}'. Valid: {sorted(VALID_FLEAN_SCORES)}"
        validated["flean_score"] = flean_score

    preferences = filters.get("preferences", [])
    if preferences:
        if not isinstance(preferences, list):
            return None, "preferences must be an array"
        invalid = [p for p in preferences if p not in VALID_PREFERENCES]
        if invalid:
            return None, f"Invalid preferences: {invalid}. Valid: {sorted(VALID_PREFERENCES)}"
        validated["preferences"] = preferences

    dietary = filters.get("dietary", [])
    if dietary:
        if not isinstance(dietary, list):
            return None, "dietary must be an array"
        invalid = [d for d in dietary if d not in VALID_DIETARY]
        if invalid:
            return None, f"Invalid dietary: {invalid}. Valid: {sorted(VALID_DIETARY)}"
        validated["dietary"] = dietary

    food_type = filters.get("food_type")
    if food_type:
        if food_type not in VALID_FOOD_TYPES:
            return None, f"Invalid food_type: '{food_type}'. Valid: {sorted(VALID_FOOD_TYPES)}"
        validated["food_type"] = food_type

    nutrition = filters.get("nutrition")
    if nutrition and isinstance(nutrition, dict):
        validated_nutrition: Dict[str, int] = {}
        for key in ("protein", "carbs", "fat"):
            val = nutrition.get(key)
            if val is not None:
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    return None, f"nutrition.{key} must be an integer"
                max_val = NUTRITION_MAX[key]
                step = NUTRITION_STEP[key]
                allowed = set(range(0, max_val + 1, step))
                if val not in allowed:
                    return None, f"nutrition.{key} must be one of {sorted(allowed)}"
                if val > 0:
                    validated_nutrition[key] = val
        if validated_nutrition:
            validated["nutrition"] = validated_nutrition

    return validated if validated else None, None


# ============================================================================
# Search Endpoint
# ============================================================================

@bp.route("/search", methods=["POST"])
def simple_search() -> tuple[Dict[str, Any], int]:
    """
    Search endpoint returning standardized product cards.

    Request body:
        {
            "query": "protein bars",
            "sort_by": "price_asc",   // optional
            "filters": { ... }        // optional
        }

    Response: { products: [product_card, ...], total_hits, returned, sort_by, filters_applied }
    """
    try:
        data = request.get_json(force=True) or {}

        query = data.get("query")
        if not query or not isinstance(query, str) or not query.strip():
            return jsonify({"error": "Missing or invalid 'query' field. Expected a non-empty string."}), 400
        query = query.strip()

        sort_by = data.get("sort_by", "relevance")
        if sort_by and sort_by not in VALID_SORT_OPTIONS:
            return jsonify({"error": f"Invalid 'sort_by': '{sort_by}'. Valid: {sorted(VALID_SORT_OPTIONS)}"}), 400

        raw_filters = data.get("filters")
        validated_filters, filter_error = _validate_filters(raw_filters)
        if filter_error:
            return jsonify({"error": f"Invalid filters: {filter_error}"}), 400

        log.info(f"SIMPLE_SEARCH_REQUEST | query='{query}' | sort_by='{sort_by}' | filters={validated_filters}")

        food_type = data.get("food_type")
        if not food_type and validated_filters:
            food_type = validated_filters.get("food_type")

        fetcher = get_es_fetcher()
        params: Dict[str, Any] = {
            "q": query,
            "size": 20,
            "sort_by": sort_by if sort_by != "relevance" else None,
            "filters": validated_filters,
        }
        if food_type and food_type in ("veg", "nonveg"):
            params["food_type"] = food_type
        result = fetcher.search(params)

        raw_products = result.get("products", [])
        meta = result.get("meta", {})
        total_hits = meta.get("total_hits", 0)

        # Use shared product card transformer
        product_cards: List[Dict[str, Any]] = []
        for raw in raw_products:
            try:
                card = transform_to_product_card(raw)
                if card is not None:
                    product_cards.append(card)
            except Exception as e:
                log.warning(f"PRODUCT_CARD_ERROR | id={raw.get('id', '?')} | error={e}")
                continue

        log.info(f"SIMPLE_SEARCH_SUCCESS | query='{query}' | total_hits={total_hits} | returned={len(product_cards)}")

        response: Dict[str, Any] = {
            "products": product_cards,
            "total_hits": total_hits,
            "returned": len(product_cards),
            "sort_by": sort_by,
        }
        if validated_filters:
            response["filters_applied"] = validated_filters

        return jsonify(response), 200

    except Exception as exc:
        log.error(f"SIMPLE_SEARCH_ERROR | error={exc}", exc_info=True)
        return jsonify({"error": "Internal server error during search", "message": str(exc)}), 500
