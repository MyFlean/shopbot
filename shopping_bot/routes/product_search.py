# shopping_bot/routes/product_search.py
"""
Product Search API - Filtered Elasticsearch Search for Flutter App

This endpoint provides a comprehensive product search API with support for:
- Keyword search
- Category filtering
- Price range filtering
- Dietary/health label filtering
- Ingredient avoidance
- Brand filtering
- Quality threshold (healthy only)
- Pagination/size control

Designed for external app consumption (Flutter, React Native, etc.)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher

log = logging.getLogger(__name__)
bp = Blueprint("product_search", __name__)


# ============================================================================
# Constants
# ============================================================================

# Valid category groups
VALID_CATEGORY_GROUPS = {"f_and_b", "personal_care"}

# Valid dietary terms (normalized to uppercase)
VALID_DIETARY_TERMS = {
    "GLUTEN FREE", "VEGAN", "VEGETARIAN", "PALM OIL FREE",
    "SUGAR FREE", "LOW SODIUM", "LOW SUGAR", "ORGANIC",
    "NO ADDED SUGAR", "DAIRY FREE", "NUT FREE", "SOY FREE",
    "KETO", "HIGH PROTEIN", "LOW FAT", "WHOLE GRAIN",
    "NO PRESERVATIVES", "NO ARTIFICIAL COLORS", "NON GMO"
}

# Default values
DEFAULT_SIZE = 20
MAX_SIZE = 100
MIN_SIZE = 1
DEFAULT_MIN_FLEAN_PERCENTILE = 0  # No quality filter by default


# ============================================================================
# Request Validation
# ============================================================================

def _validate_request(data: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[str]]:
    """
    Validate and normalize request parameters.
    
    Returns:
        tuple: (validated_params, error_message)
        - If valid: (params_dict, None)
        - If invalid: (None, error_string)
    """
    errors = []
    params = {}
    
    # 1. Query (required unless category context is provided)
    query = data.get("query") or data.get("q")
    has_category = bool(
        data.get("category_paths") or data.get("categories") or
        data.get("category_group") or data.get("category")
    )
    if query and isinstance(query, str) and query.strip():
        params["q"] = query.strip()
    elif not has_category:
        errors.append("'query' or a category filter (category_paths / category_group) is required")
    
    # 2. Category group (optional)
    category_group = data.get("category_group") or data.get("category")
    if category_group:
        if isinstance(category_group, str):
            category_group = category_group.strip().lower()
            if category_group and category_group not in VALID_CATEGORY_GROUPS:
                errors.append(f"'category_group' must be one of: {', '.join(VALID_CATEGORY_GROUPS)}")
            else:
                params["category_group"] = category_group
        else:
            errors.append("'category_group' must be a string")
    
    # 3. Category paths (optional)
    category_paths = data.get("category_paths") or data.get("categories")
    if category_paths:
        if isinstance(category_paths, list):
            valid_paths = [str(p).strip() for p in category_paths if p and str(p).strip()]
            if valid_paths:
                params["category_paths"] = valid_paths[:3]  # Max 3 paths
        elif isinstance(category_paths, str):
            params["category_paths"] = [category_paths.strip()]
        else:
            errors.append("'category_paths' must be a string or array of strings")
    
    # 4. Price range (optional)
    price_min = data.get("price_min") or data.get("min_price")
    price_max = data.get("price_max") or data.get("max_price")
    
    if price_min is not None:
        try:
            price_min = float(price_min)
            if price_min < 0:
                errors.append("'price_min' must be a non-negative number")
            else:
                params["price_min"] = price_min
        except (TypeError, ValueError):
            errors.append("'price_min' must be a valid number")
    
    if price_max is not None:
        try:
            price_max = float(price_max)
            if price_max < 0:
                errors.append("'price_max' must be a non-negative number")
            else:
                params["price_max"] = price_max
        except (TypeError, ValueError):
            errors.append("'price_max' must be a valid number")
    
    # Validate price range logic
    if params.get("price_min") and params.get("price_max"):
        if params["price_min"] > params["price_max"]:
            errors.append("'price_min' cannot be greater than 'price_max'")
    
    # 5. Dietary terms (optional)
    dietary = data.get("dietary_terms") or data.get("dietary") or data.get("dietary_labels")
    if dietary:
        if isinstance(dietary, list):
            normalized = []
            for term in dietary:
                if term and isinstance(term, str):
                    upper_term = term.strip().upper()
                    if upper_term:
                        normalized.append(upper_term)
            if normalized:
                params["dietary_terms"] = normalized[:5]  # Max 5 terms
        elif isinstance(dietary, str):
            params["dietary_terms"] = [dietary.strip().upper()]
        else:
            errors.append("'dietary_terms' must be a string or array of strings")
    
    # 6. Avoid ingredients (optional)
    avoid = data.get("avoid_ingredients") or data.get("avoid") or data.get("exclude_ingredients")
    if avoid:
        if isinstance(avoid, list):
            valid_avoid = [str(ing).strip().lower() for ing in avoid if ing and str(ing).strip()]
            if valid_avoid:
                params["avoid_ingredients"] = valid_avoid[:6]  # Max 6 ingredients
        elif isinstance(avoid, str):
            params["avoid_ingredients"] = [avoid.strip().lower()]
        else:
            errors.append("'avoid_ingredients' must be a string or array of strings")
    
    # 7. Brands (optional)
    brands = data.get("brands") or data.get("brand")
    if brands:
        if isinstance(brands, list):
            valid_brands = [str(b).strip() for b in brands if b and str(b).strip()]
            if valid_brands:
                params["brands"] = valid_brands[:5]  # Max 5 brands
        elif isinstance(brands, str):
            params["brands"] = [brands.strip()]
        else:
            errors.append("'brands' must be a string or array of strings")
    
    # 8. Quality threshold / healthy_only (optional)
    healthy_only = data.get("healthy_only")
    min_flean = data.get("min_flean_percentile") or data.get("min_quality") or data.get("quality_threshold")
    
    if healthy_only is True or (isinstance(healthy_only, str) and healthy_only.lower() in ("true", "1", "yes")):
        # "Healthy only" means min 70 percentile
        params["min_flean_percentile"] = 70
    elif min_flean is not None:
        try:
            min_flean = float(min_flean)
            if min_flean < 0 or min_flean > 100:
                errors.append("'min_flean_percentile' must be between 0 and 100")
            else:
                params["min_flean_percentile"] = min_flean
        except (TypeError, ValueError):
            errors.append("'min_flean_percentile' must be a valid number")
    
    # 9. Page (optional, 0-indexed)
    page = data.get("page")
    if page is not None:
        try:
            page = max(0, int(page))
            params["page"] = page
        except (TypeError, ValueError):
            params["page"] = 0
    else:
        params["page"] = 0
    
    # 10. Size/limit (optional)
    size = data.get("size") or data.get("limit") or data.get("count")
    if size is not None:
        try:
            size = int(size)
            if size < MIN_SIZE:
                size = MIN_SIZE
            elif size > MAX_SIZE:
                size = MAX_SIZE
            params["size"] = size
        except (TypeError, ValueError):
            errors.append(f"'size' must be an integer between {MIN_SIZE} and {MAX_SIZE}")
    else:
        params["size"] = DEFAULT_SIZE
    
    # 11. Food type (optional) — veg / nonveg
    food_type = data.get("food_type") or data.get("diet_type")
    if food_type:
        if isinstance(food_type, str):
            food_type = food_type.strip().lower()
            if food_type in ("veg", "nonveg"):
                params["food_type"] = food_type
            else:
                errors.append("'food_type' must be 'veg' or 'nonveg'")
        else:
            errors.append("'food_type' must be a string")

    # 12. Sort by (optional)
    sort_by = data.get("sort_by") or data.get("sort")
    if sort_by:
        valid_sorts = {"relevance", "price_asc", "price_desc", "quality", "rating"}
        if isinstance(sort_by, str) and sort_by.lower() in valid_sorts:
            params["sort_by"] = sort_by.lower()
        # Silently ignore invalid sort values (use default)
    
    # Return result
    if errors:
        return None, "; ".join(errors)
    
    return params, None


# ============================================================================
# Response Formatting
# ============================================================================

def _format_product(es_product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format ES product to clean API response.
    
    Returns a standardized product object for the API response.
    """
    # Extract flean percentile with fallback
    flean_percentile = es_product.get("flean_percentile")
    flean_score = es_product.get("flean_score")
    
    # Build quality info
    quality = None
    if flean_percentile is not None:
        if flean_percentile >= 80:
            quality = "excellent"
        elif flean_percentile >= 60:
            quality = "good"
        elif flean_percentile >= 40:
            quality = "average"
        else:
            quality = "below_average"
    
    # Extract dietary labels
    dietary_labels = es_product.get("dietary_labels", [])
    if not isinstance(dietary_labels, list):
        dietary_labels = []
    
    # Extract health claims
    health_claims = es_product.get("health_claims", [])
    if not isinstance(health_claims, list):
        health_claims = []
    
    # Build response object
    product = {
        "id": es_product.get("id", ""),
        "name": es_product.get("name", ""),
        "brand": es_product.get("brand", ""),
        "price": es_product.get("price"),
        "mrp": es_product.get("mrp"),
        "currency": "INR",
        "image_url": es_product.get("image"),
        "description": es_product.get("description", ""),
        "category": es_product.get("category", ""),
        
        # Quality metrics
        "flean_score": flean_score,
        "flean_percentile": flean_percentile,
        "quality_tier": quality,
        
        # Nutrition (if available)
        "nutrition": {
            "protein_g": es_product.get("protein_g"),
            "carbs_g": es_product.get("carbs_g"),
            "fat_g": es_product.get("fat_g"),
            "calories": es_product.get("calories"),
        } if any([
            es_product.get("protein_g"),
            es_product.get("carbs_g"),
            es_product.get("fat_g"),
            es_product.get("calories")
        ]) else None,
        
        # Labels and claims
        "dietary_labels": dietary_labels,
        "health_claims": health_claims,
        
        # Reviews (if available)
        "rating": {
            "average": es_product.get("avg_rating"),
            "total_reviews": es_product.get("total_reviews"),
        } if es_product.get("avg_rating") else None,
        
        # Stock status (hardcoded for now)
        "in_stock": True,
    }
    
    # Remove None values for cleaner response
    return {k: v for k, v in product.items() if v is not None}


def _build_response(
    products: List[Dict[str, Any]],
    meta: Dict[str, Any],
    filters_applied: Dict[str, Any],
    fallback_used: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build the final API response with catalogue-consistent meta format.
    """
    formatted_products = []
    for product in products:
        try:
            formatted = _format_product(product)
            formatted_products.append(formatted)
        except Exception as e:
            log.warning(f"PRODUCT_FORMAT_ERROR | id={product.get('id')} | error={e}")
            continue
    
    total = meta.get("total_hits", 0)
    page = filters_applied.get("page", meta.get("page", 0))
    size = filters_applied.get("size", DEFAULT_SIZE)
    total_pages = (total + size - 1) // size if size > 0 else 0
    offset = page * size

    non_pagination_keys = {"q", "size", "page"}
    applied = {k: v for k, v in filters_applied.items() if v is not None and k not in non_pagination_keys}

    response_meta: Dict[str, Any] = {
        "total": total,
        "page": page,
        "size": size,
        "total_pages": total_pages,
        "has_next": offset + len(formatted_products) < total,
        "has_prev": page > 0,
        "query": filters_applied.get("q"),
        "sort_by": filters_applied.get("sort_by", "relevance"),
        "filters_applied": applied if applied else None,
    }

    if fallback_used:
        response_meta["fallback_used"] = fallback_used
        response_meta["note"] = "Original query returned no results; filters were relaxed"

    response = {
        "success": True,
        "data": {
            "products": formatted_products,
        },
        "meta": response_meta,
    }
    
    return response


# ============================================================================
# API Endpoint
# ============================================================================

@bp.route("/api/v1/products/search", methods=["GET", "POST"])
def product_search() -> tuple[Dict[str, Any], int]:
    """
    Product Search API -- supports both GET (query params) and POST (JSON body).

    GET  /api/v1/products/search?query=chips&page=0&size=20&sort_by=price_asc
    POST /api/v1/products/search  { "query": "chips", "page": 0, "size": 20 }

    Response:
    {
        "success": true,
        "data": { "products": [...] },
        "meta": {
            "total": 245, "page": 0, "size": 20,
            "total_pages": 13, "has_next": true, "has_prev": false,
            "query": "chips", "sort_by": "relevance",
            "filters_applied": { ... }
        }
    }
    """
    try:
        if request.method == "GET":
            data: Dict[str, Any] = {}
            for key in ("query", "q", "category_group", "category",
                        "price_min", "price_max", "min_price", "max_price",
                        "healthy_only", "min_flean_percentile",
                        "food_type", "diet_type",
                        "size", "limit", "page", "sort_by", "sort"):
                val = request.args.get(key)
                if val is not None:
                    data[key] = val
            brands_raw = request.args.get("brands") or request.args.get("brand")
            if brands_raw:
                data["brands"] = [b.strip() for b in brands_raw.split(",") if b.strip()]
            dietary_raw = request.args.get("dietary_terms") or request.args.get("dietary")
            if dietary_raw:
                data["dietary_terms"] = [d.strip() for d in dietary_raw.split(",") if d.strip()]
            avoid_raw = request.args.get("avoid_ingredients") or request.args.get("avoid")
            if avoid_raw:
                data["avoid_ingredients"] = [a.strip() for a in avoid_raw.split(",") if a.strip()]
            category_paths_raw = request.args.get("category_paths") or request.args.get("categories")
            if category_paths_raw:
                data["category_paths"] = [c.strip() for c in category_paths_raw.split(",") if c.strip()]
        else:
            try:
                data = request.get_json(force=True) or {}
            except Exception:
                data = {}
            if not data:
                data = {
                    "query": request.args.get("query") or request.args.get("q"),
                    "category_group": request.args.get("category_group"),
                    "size": request.args.get("size"),
                    "page": request.args.get("page"),
                }
        
        log.info(f"PRODUCT_SEARCH_REQUEST | raw_data={data}")
        
        # Validate request
        params, error = _validate_request(data)
        if error:
            return jsonify({
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": error
                }
            }), 400
        
        log.info(f"PRODUCT_SEARCH_VALIDATED | params={params}")
        
        # Get ES fetcher
        fetcher = get_es_fetcher()
        
        # Perform search
        result = fetcher.search(params)
        
        # Extract data
        products = result.get("products", [])
        meta = result.get("meta", {})
        fallback = meta.get("fallback_applied")
        
        log.info(
            f"PRODUCT_SEARCH_SUCCESS | query='{params.get('q')}' | "
            f"total_hits={meta.get('total_hits', 0)} | returned={len(products)} | "
            f"fallback={fallback}"
        )
        
        # Build and return response
        response = _build_response(
            products=products,
            meta=meta,
            filters_applied=params,
            fallback_used=fallback
        )
        
        return jsonify(response), 200
        
    except Exception as exc:
        log.error(f"PRODUCT_SEARCH_ERROR | error={exc}", exc_info=True)
        return jsonify({
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred while processing your request"
            }
        }), 500


# ============================================================================
# Health Check for this Blueprint
# ============================================================================

@bp.route("/api/v1/products/health", methods=["GET"])
def product_search_health() -> tuple[Dict[str, Any], int]:
    """Health check for the product search API."""
    try:
        fetcher = get_es_fetcher()
        # Quick test query
        result = fetcher.search({"q": "test", "size": 1})
        es_ok = result.get("meta", {}).get("query_successful", False)
        
        return jsonify({
            "status": "healthy" if es_ok else "degraded",
            "elasticsearch": "connected" if es_ok else "error",
            "version": "1.0.0"
        }), 200 if es_ok else 503
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "elasticsearch": "disconnected",
            "error": str(e)
        }), 503

