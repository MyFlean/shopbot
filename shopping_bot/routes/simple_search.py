# shopping_bot/routes/simple_search.py
"""
Simple Search Endpoint - Direct Elasticsearch Query Search

This endpoint takes a user query and performs a direct Elasticsearch search
without any filters, tags, categories, or subcategories.
Returns enriched product information with macro tags and quantity.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher

log = logging.getLogger(__name__)
bp = Blueprint("simple_search", __name__)


# ============================================================================
# Macro Tag Generation
# ============================================================================

def _generate_macro_tags(es_product: Dict[str, Any], max_tags: int = 2) -> List[Dict[str, Any]]:
    """
    Generate macro tags from nutritional data.
    
    Collects available macros (protein, carbs, fat, calories), sorts by value,
    and returns the top N highest values as formatted tags.
    
    Args:
        es_product: Product dict from Elasticsearch
        max_tags: Maximum number of macro tags to return (default: 2)
    
    Returns:
        List of macro tag objects: [{"label": "32 gms of Protein", "nutrient": "protein", "value": 32, "unit": "g"}]
    """
    # Define macro nutrients with their display names and units
    macro_config = {
        "protein_g": {"nutrient": "protein", "display_name": "Protein", "unit": "g", "label_format": "{value} gms of Protein"},
        "carbs_g": {"nutrient": "carbs", "display_name": "Carbs", "unit": "g", "label_format": "{value} gms of Carbs"},
        "fat_g": {"nutrient": "fat", "display_name": "Fat", "unit": "g", "label_format": "{value} gms of Fat"},
        "calories": {"nutrient": "calories", "display_name": "Calories", "unit": "kcal", "label_format": "{value} Calories"},
    }
    
    # Collect available macros with their values
    available_macros: List[Tuple[str, float, Dict[str, Any]]] = []
    
    for field_name, config in macro_config.items():
        value = es_product.get(field_name)
        if value is not None:
            try:
                numeric_value = float(value)
                if numeric_value > 0:
                    available_macros.append((field_name, numeric_value, config))
            except (TypeError, ValueError):
                continue
    
    # Sort by value descending (highest first)
    available_macros.sort(key=lambda x: x[1], reverse=True)
    
    # Take top N macros
    top_macros = available_macros[:max_tags]
    
    # Format as tag objects
    macro_tags = []
    for field_name, value, config in top_macros:
        # Round value for display
        display_value = int(value) if value == int(value) else round(value, 1)
        
        tag = {
            "label": config["label_format"].format(value=display_value),
            "nutrient": config["nutrient"],
            "value": display_value,
            "unit": config["unit"]
        }
        macro_tags.append(tag)
    
    return macro_tags


def _extract_quantity(es_product: Dict[str, Any]) -> str:
    """
    Extract product quantity/weight from ES product data.
    
    Tries multiple fields and formats the quantity consistently.
    
    Args:
        es_product: Product dict from Elasticsearch
    
    Returns:
        Formatted quantity string (e.g., "250 gm", "1 Kg", "500 ml") or empty string
    """
    # Try direct qty field (now extracted from category_data.nutritional.qty by ES fetcher)
    qty = es_product.get("qty", "")
    if qty and isinstance(qty, str) and qty.strip():
        return qty.strip()
    
    # Fallback - no quantity data available
    return ""


def _parse_es_product_to_payload(es_product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse Elasticsearch product response to the desired payload format.
    
    Maps ES fields to the required structure:
    - id, name, brand, price, mrp: Direct mapping
    - qty: Extracted from various ES fields
    - image_url: From ES 'image' field
    - macro_tags: Top 2 highest nutritional values
    - nutrition: Raw nutritional values (protein, carbs, fat, fiber, calories)
    - flean_score: From ES 'flean_score' field
    - in_stock: Hardcoded to True
    """
    # Extract basic fields
    product_id = es_product.get("id", "")
    name = es_product.get("name", "")
    brand = es_product.get("brand", "")
    price = es_product.get("price")
    mrp = es_product.get("mrp")
    image_url = es_product.get("image")  # This is the CDN URL
    flean_score = es_product.get("flean_score")
    flean_percentile = es_product.get("flean_percentile")
    
    # Extract quantity
    qty = _extract_quantity(es_product)
    
    # Generate macro tags (top 2 highest nutritional values)
    macro_tags = _generate_macro_tags(es_product, max_tags=2)
    
    # Extract raw nutritional values for sorting visibility
    nutrition = {
        "protein_g": es_product.get("protein_g"),
        "carbs_g": es_product.get("carbs_g"),
        "fat_g": es_product.get("fat_g"),
        "fiber_g": es_product.get("fiber_g"),
        "calories": es_product.get("calories"),
    }
    # Remove None values for cleaner response
    nutrition = {k: v for k, v in nutrition.items() if v is not None}
    
    # Build the payload
    payload = {
        "id": product_id,
        "name": name,
        "brand": brand,
        "price": price,
        "mrp": mrp,
        "currency": "INR",
        "qty": qty,
        "image_url": image_url,
        "macro_tags": macro_tags,
        "nutrition": nutrition if nutrition else None,
        "flean_score": flean_score,
        "flean_percentile": flean_percentile,
        "in_stock": True
    }
    
    # Remove None values
    return {k: v for k, v in payload.items() if v is not None}


# Valid sort_by options for the search API
VALID_SORT_OPTIONS = {
    "relevance",      # Default: ES score + flean_percentile
    "price_asc",      # Price Low to High
    "price_desc",     # Price High to Low
    "protein_desc",   # Protein High to Low
    "fiber_desc",     # Fibre High to Low
    "fat_asc",        # Fat Low to High
}

# ============================================================================
# Filter Validation Constants
# ============================================================================

# Valid price range filter options
VALID_PRICE_RANGES = {
    "below_99",   # Below Rs.99
    "100_249",    # Rs.100-249
    "250_499",    # Rs.250-499
    "above_500",  # Above Rs.500
}

# Valid Flean score filter options (0-10 scale)
VALID_FLEAN_SCORES = {
    "10",         # Perfect 10
    "9_plus",     # 9+
    "8_plus",     # 8+
    "7_plus",     # 7+
}

# Valid preference filter options
VALID_PREFERENCES = {
    "no_palm_oil",      # No Palm Oil
    "no_added_sugar",   # No Added Sugar
    "no_additives",     # No Additives (No Preservatives, No Artificial Colors)
}

# Valid dietary restriction filter options
VALID_DIETARY = {
    "dairy_free",   # Dairy Free
    "gluten_free",  # Gluten Free
}


def _validate_filters(filters: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate the filters object from the request.
    
    Args:
        filters: The filters dict from request body
        
    Returns:
        Tuple of (validated_filters, error_message)
        - If valid: (filters_dict, None)
        - If invalid: (None, error_string)
    """
    if not filters:
        return None, None
    
    if not isinstance(filters, dict):
        return None, "filters must be an object"
    
    validated = {}
    
    # Validate price_range (single value)
    price_range = filters.get("price_range")
    if price_range:
        if price_range not in VALID_PRICE_RANGES:
            return None, f"Invalid price_range: '{price_range}'. Valid options: {sorted(VALID_PRICE_RANGES)}"
        validated["price_range"] = price_range
    
    # Validate flean_score (single value)
    flean_score = filters.get("flean_score")
    if flean_score:
        if flean_score not in VALID_FLEAN_SCORES:
            return None, f"Invalid flean_score: '{flean_score}'. Valid options: {sorted(VALID_FLEAN_SCORES)}"
        validated["flean_score"] = flean_score
    
    # Validate preferences (list)
    preferences = filters.get("preferences", [])
    if preferences:
        if not isinstance(preferences, list):
            return None, "preferences must be an array"
        invalid_prefs = [p for p in preferences if p not in VALID_PREFERENCES]
        if invalid_prefs:
            return None, f"Invalid preferences: {invalid_prefs}. Valid options: {sorted(VALID_PREFERENCES)}"
        validated["preferences"] = preferences
    
    # Validate dietary (list)
    dietary = filters.get("dietary", [])
    if dietary:
        if not isinstance(dietary, list):
            return None, "dietary must be an array"
        invalid_dietary = [d for d in dietary if d not in VALID_DIETARY]
        if invalid_dietary:
            return None, f"Invalid dietary: {invalid_dietary}. Valid options: {sorted(VALID_DIETARY)}"
        validated["dietary"] = dietary
    
    return validated if validated else None, None


@bp.route("/search", methods=["POST"])
def simple_search() -> tuple[Dict[str, Any], int]:
    """
    Simple search endpoint with sorting and filtering capabilities.
    
    Request body:
        {
            "query": "user search query",
            "sort_by": "price_asc",  // Optional, defaults to "relevance"
            "filters": {             // Optional, all filters use AND logic
                "price_range": "100_249",
                "flean_score": "8_plus",
                "preferences": ["no_palm_oil", "no_added_sugar"],
                "dietary": ["gluten_free", "dairy_free"]
            }
        }
    
    Sort options:
        - "relevance" (default): ES score + flean_percentile ranking
        - "price_asc": Price Low to High
        - "price_desc": Price High to Low
        - "protein_desc": Protein High to Low
        - "fiber_desc": Fibre High to Low
        - "fat_asc": Fat Low to High
    
    Filter options:
        price_range (single):
            - "below_99": Below Rs.99
            - "100_249": Rs.100-249
            - "250_499": Rs.250-499
            - "above_500": Above Rs.500
        
        flean_score (single):
            - "10": Perfect 10
            - "9_plus": 9+
            - "8_plus": 8+
            - "7_plus": 7+
        
        preferences (array, OR logic within):
            - "no_palm_oil": No Palm Oil
            - "no_added_sugar": No Added Sugar
            - "no_additives": No Additives
        
        dietary (array, AND logic - each must match):
            - "dairy_free": Dairy Free
            - "gluten_free": Gluten Free
    
    Response:
        {
            "products": [...],
            "total_hits": 100,
            "returned": 20,
            "sort_by": "price_asc",
            "filters_applied": {
                "price_range": "100_249",
                "flean_score": "8_plus",
                "preferences": ["no_palm_oil"],
                "dietary": ["gluten_free"]
            }
        }
    """
    try:
        # Parse request
        data = request.get_json(force=True) or {}
        
        # Validate required field
        query = data.get("query")
        if not query or not isinstance(query, str) or not query.strip():
            return jsonify({
                "error": "Missing or invalid 'query' field. Expected a non-empty string."
            }), 400
        
        query = query.strip()
        
        # Extract and validate sort_by parameter
        sort_by = data.get("sort_by", "relevance")
        if sort_by and sort_by not in VALID_SORT_OPTIONS:
            return jsonify({
                "error": f"Invalid 'sort_by' value: '{sort_by}'. Valid options: {sorted(VALID_SORT_OPTIONS)}"
            }), 400
        
        # Extract and validate filters
        raw_filters = data.get("filters")
        validated_filters, filter_error = _validate_filters(raw_filters)
        if filter_error:
            return jsonify({
                "error": f"Invalid filters: {filter_error}"
            }), 400
        
        log.info(f"SIMPLE_SEARCH_REQUEST | query='{query}' | sort_by='{sort_by}' | filters={validated_filters}")
        
        # Get ES fetcher instance
        fetcher = get_es_fetcher()
        
        # Build params with query, sort, and filters
        params = {
            "q": query,
            "size": 20,  # Default to 20 results
            "sort_by": sort_by if sort_by != "relevance" else None,  # Only pass if not default
            "filters": validated_filters  # Pass validated filters to ES
        }
        
        # Perform ES search
        result = fetcher.search(params)
        
        # Extract products and meta from ES response
        es_products = result.get("products", [])
        meta = result.get("meta", {})
        total_hits = meta.get("total_hits", 0)
        
        log.info(
            f"SIMPLE_SEARCH_SUCCESS | query='{query}' | total_hits={total_hits} | returned={len(es_products)}"
        )
        
        # DEBUG: Print complete ES response structure for first product (if available)
        if es_products:
            first_product = es_products[0]
            try:
                # Pretty print the first product's complete structure
                product_json = json.dumps(first_product, indent=2, ensure_ascii=False, default=str)
                log.info("="*80)
                log.info("ES_RESPONSE_FIRST_PRODUCT | Complete structure:")
                log.info(product_json)
                log.info("="*80)
                
                # Also print all top-level keys available in products
                all_keys = set()
                for product in es_products[:3]:  # Check first 3 products
                    all_keys.update(product.keys())
                log.info(f"ES_RESPONSE_AVAILABLE_KEYS | Keys found in products: {sorted(all_keys)}")
            except Exception as e:
                log.warning(f"ES_RESPONSE_PRINT_ERROR | Could not print ES response: {e}")
        
        # Parse ES products to desired payload format
        parsed_products = []
        for es_product in es_products:
            try:
                parsed_product = _parse_es_product_to_payload(es_product)
                parsed_products.append(parsed_product)
            except Exception as e:
                log.warning(f"PRODUCT_PARSE_ERROR | product_id={es_product.get('id', 'unknown')} | error={e}")
                # Skip products that fail to parse
                continue
        
        # Log first parsed product for debugging
        if parsed_products:
            try:
                first_parsed_json = json.dumps(parsed_products[0], indent=2, ensure_ascii=False, default=str)
                log.info("="*80)
                log.info("PARSED_FIRST_PRODUCT | Transformed payload:")
                log.info(first_parsed_json)
                log.info("="*80)
            except Exception as e:
                log.warning(f"PARSED_PRODUCT_PRINT_ERROR | Could not print parsed product: {e}")
        
        # Build response
        response = {
            "products": parsed_products,
            "total_hits": total_hits,
            "returned": len(parsed_products),
            "sort_by": sort_by
        }
        
        # Include filters_applied if any filters were used
        if validated_filters:
            response["filters_applied"] = validated_filters
        
        return jsonify(response), 200
        
    except Exception as exc:
        log.error(f"SIMPLE_SEARCH_ERROR | error={exc}", exc_info=True)
        return jsonify({
            "error": "Internal server error during search",
            "message": str(exc)
        }), 500

