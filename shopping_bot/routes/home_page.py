# shopping_bot/routes/home_page.py
"""
Home Page API - Flutter App Home Screen Endpoints

This module provides API endpoints for the Flutter app's home page:
1. GET /api/v1/home/banners - Promotional banners/ads carousel
2. GET /api/v1/home/categories - Product categories (4 by default, all with ?all=true)
3. GET /api/v1/home/best-selling - Best selling products (fetched from ES by IDs)
4. GET /api/v1/home/curated - 4 random curated products for home (fetched from ES)
5. GET /api/v1/home/curated/all - All curated products (fetched from ES)
6. GET /api/v1/home/why-flean - Value proposition cards
7. GET /api/v1/home/collaborations - Partner brand names
8. POST /api/v1/home/refresh - Clear cache and reload data
9. POST /api/v1/home/unified - Unified endpoint returning all sections in one response

Products (curated, best-selling) are fetched from Elasticsearch using stored product IDs.
Other data is loaded from JSON files in shopping_bot/data/home/
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import (
    filter_products_in_memory,
    get_es_fetcher,
    transform_to_product_card,
)

log = logging.getLogger(__name__)
bp = Blueprint("home_page", __name__)

# ============================================================================
# Data Loading & Caching
# ============================================================================

# Path to the data directory
DATA_DIR = Path(__file__).parent.parent / "data" / "home"


@lru_cache(maxsize=10)
def _load_json_file(filename: str) -> Dict[str, Any]:
    """
    Load and cache a JSON file from the data directory.
    
    Uses lru_cache for performance - files are loaded once and cached.
    To reload data, call _load_json_file.cache_clear()
    """
    file_path = DATA_DIR / filename
    log.debug(f"Loading JSON file: {file_path}")
    
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return data


def _get_json_data(filename: str, default: Dict[str, Any] = None) -> Dict[str, Any]:
    """Safely load JSON data with error handling."""
    if default is None:
        default = {}
    
    try:
        return _load_json_file(filename)
    except FileNotFoundError:
        log.error(f"HOME_PAGE_DATA_ERROR | file={filename} | error=File not found")
        return default
    except json.JSONDecodeError as e:
        log.error(f"HOME_PAGE_DATA_ERROR | file={filename} | error=Invalid JSON: {e}")
        return default
    except Exception as e:
        log.error(f"HOME_PAGE_DATA_ERROR | file={filename} | error={e}")
        return default


def _build_success_response(data: Any, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a standardized success response."""
    response = {
        "success": True,
        "data": data
    }
    if meta:
        response["meta"] = meta
    return response


def _build_error_response(code: str, message: str) -> Dict[str, Any]:
    """Build a standardized error response."""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message
        }
    }


# ============================================================================
# Elasticsearch Product Fetching (uses shared transformer)
# ============================================================================

# Filter constants (same as product_api / unified products API)
_VALID_PRICE_RANGES = {"below_99", "100_249", "250_499", "above_500"}
_VALID_FLEAN_SCORES = {"10", "9_plus", "8_plus", "7_plus"}
_VALID_PREFERENCES = {"no_palm_oil", "no_added_sugar", "no_additives"}
_VALID_DIETARY = {"dairy_free", "gluten_free"}


def _parse_filters_from_dict(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse and validate filters from a dict (e.g. request body)."""
    filters = data.get("filters")
    if not filters or not isinstance(filters, dict):
        return None
    validated: Dict[str, Any] = {}
    if filters.get("price_range") in _VALID_PRICE_RANGES:
        validated["price_range"] = filters["price_range"]
    if filters.get("flean_score") in _VALID_FLEAN_SCORES:
        validated["flean_score"] = filters["flean_score"]
    prefs = filters.get("preferences", [])
    if isinstance(prefs, list):
        prefs = [p for p in prefs if p in _VALID_PREFERENCES]
    elif isinstance(prefs, str):
        prefs = [p.strip() for p in prefs.split(",") if p.strip() in _VALID_PREFERENCES]
    if prefs:
        validated["preferences"] = prefs
    diet = filters.get("dietary", [])
    if isinstance(diet, list):
        diet = [d for d in diet if d in _VALID_DIETARY]
    elif isinstance(diet, str):
        diet = [d.strip() for d in diet.split(",") if d.strip() in _VALID_DIETARY]
    if diet:
        validated["dietary"] = diet
    return validated if validated else None


def _parse_filters_from_request() -> Optional[Dict[str, Any]]:
    """
    Parse personalization filters from request (GET query params or POST body).
    Returns validated filters dict or None if no filters provided.
    """
    filters = None
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        filters = _parse_filters_from_dict({"filters": payload.get("filters")})
    if not filters and request.args:
        # GET with query params
        f = {}
        if request.args.get("price_range") in _VALID_PRICE_RANGES:
            f["price_range"] = request.args.get("price_range")
        if request.args.get("flean_score") in _VALID_FLEAN_SCORES:
            f["flean_score"] = request.args.get("flean_score")
        pref = request.args.get("preferences", "")
        if pref:
            prefs = [p.strip() for p in pref.split(",") if p.strip() in _VALID_PREFERENCES]
            if prefs:
                f["preferences"] = prefs
        diet = request.args.get("dietary", "")
        if diet:
            diets = [d.strip() for d in diet.split(",") if d.strip() in _VALID_DIETARY]
            if diets:
                f["dietary"] = diets
        filters = f if f else None
    if not filters or not isinstance(filters, dict):
        return None
    # Validate and normalize
    validated: Dict[str, Any] = {}
    if filters.get("price_range") in _VALID_PRICE_RANGES:
        validated["price_range"] = filters["price_range"]
    if filters.get("flean_score") in _VALID_FLEAN_SCORES:
        validated["flean_score"] = filters["flean_score"]
    prefs = filters.get("preferences", [])
    if isinstance(prefs, list):
        prefs = [p for p in prefs if p in _VALID_PREFERENCES]
    elif isinstance(prefs, str):
        prefs = [p.strip() for p in prefs.split(",") if p.strip() in _VALID_PREFERENCES]
    if prefs:
        validated["preferences"] = prefs
    diet = filters.get("dietary", [])
    if isinstance(diet, list):
        diet = [d for d in diet if d in _VALID_DIETARY]
    elif isinstance(diet, str):
        diet = [d.strip() for d in diet.split(",") if d.strip() in _VALID_DIETARY]
    if diet:
        validated["dietary"] = diet
    return validated if validated else None


def _fetch_products_by_ids(product_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetch products from ES by IDs and return standardized product cards."""
    if not product_ids:
        return []
    try:
        fetcher = get_es_fetcher()
        es_products = fetcher.search_by_ids(product_ids)
        cards = [transform_to_product_card(src) for src in es_products if src]
        log.debug(f"ES_FETCH | requested={len(product_ids)} | returned={len(cards)}")
        return cards
    except Exception as e:
        log.error(f"ES_FETCH_ERROR | error={e}", exc_info=True)
        return []


def _fetch_raw_products_by_ids(product_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetch raw ES _source docs by IDs (for filtering before transform)."""
    if not product_ids:
        return []
    try:
        fetcher = get_es_fetcher()
        return fetcher.search_by_ids(product_ids)
    except Exception as e:
        log.error(f"ES_FETCH_RAW_ERROR | error={e}", exc_info=True)
        return []


# ============================================================================
# Internal Data Fetchers (for unified endpoint)
# ============================================================================

def _get_banners_data() -> Dict[str, Any]:
    """Fetch banners data for unified response."""
    data = _get_json_data("banners.json", {"banners": []})
    banners = data.get("banners", [])
    active_banners = [b for b in banners if b.get("active", True)]
    return {"banners": active_banners}


def _get_categories_data(show_all: bool = False) -> Dict[str, Any]:
    """Fetch categories data for unified response."""
    data = _get_json_data("categories.json", {"categories": []})
    categories = data.get("categories", [])
    categories = sorted(categories, key=lambda c: c.get("display_order", 999))
    
    if show_all:
        result_categories = categories
        has_more = False
    else:
        result_categories = categories[:4]
        has_more = len(categories) > 4
    
    return {
        "categories": result_categories,
        "has_more": has_more,
        "total_count": len(categories)
    }


def _get_best_selling_data() -> Dict[str, Any]:
    """Fetch best-selling products data for unified response."""
    data = _get_json_data("best_selling_products.json", {"product_ids": []})
    product_ids = data.get("product_ids", [])
    
    if not product_ids:
        return {"products": [], "section_title": "Best Selling"}
    
    products = _fetch_products_by_ids(product_ids)
    return {"products": products, "section_title": "Best Selling"}


def _get_curated_data(
    filters: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = 4,
) -> Dict[str, Any]:
    """
    Fetch curated products data for unified response (randomized).
    When filters are provided, fetches all curated products, filters in-memory
    by personalization criteria, then randomly samples from the filtered pool.

    Args:
        filters: Optional personalization filters (price_range, flean_score, preferences, dietary)
        limit: Max products to return. 4 for home, None for "all" (return entire filtered pool)
    """
    data = _get_json_data("curated_products.json", {"product_ids": []})
    all_product_ids = data.get("product_ids", [])

    if not all_product_ids:
        return {
            "products": [],
            "section_title": "Curated For You",
            "has_more": False,
            "total_in_pool": 0,
            "filters_applied": bool(filters),
        }

    if filters:
        # Fetch all curated products (raw), filter in-memory, then sample or return all
        raw_products = _fetch_raw_products_by_ids(all_product_ids)
        filtered_raw = filter_products_in_memory(raw_products, filters)
        if limit is not None:
            count = min(limit, len(filtered_raw))
            selected_raw = random.sample(filtered_raw, count) if filtered_raw else []
        else:
            selected_raw = filtered_raw
        products = [transform_to_product_card(src) for src in selected_raw if src]
        total_in_pool = len(filtered_raw)
    else:
        # Original behavior: random sample from IDs (or all if limit is None), then fetch
        if limit is not None:
            count = min(limit, len(all_product_ids))
            selected_ids = random.sample(all_product_ids, count)
        else:
            selected_ids = all_product_ids
        products = _fetch_products_by_ids(selected_ids)
        total_in_pool = len(all_product_ids)

    return {
        "products": products,
        "section_title": "Curated For You",
        "has_more": total_in_pool > (limit or 4),
        "total_in_pool": total_in_pool,
        "filters_applied": bool(filters),
    }


def _get_why_flean_data() -> Dict[str, Any]:
    """Fetch Why Flean cards data for unified response."""
    data = _get_json_data("why_flean.json", {"cards": []})
    cards = data.get("cards", [])
    cards = sorted(cards, key=lambda c: c.get("display_order", 999))
    return {"cards": cards, "section_title": "Why Flean"}


def _get_collaborations_data() -> Dict[str, Any]:
    """Fetch collaborations data for unified response."""
    data = _get_json_data("collaborations.json", {"collaborations": []})
    collaborations = data.get("collaborations", [])
    return {"brands": collaborations, "section_title": "Exclusive Collaborations"}


# ============================================================================
# API Endpoints
# ============================================================================

@bp.route("/api/v1/home/banners", methods=["GET"])
def get_banners() -> tuple[Dict[str, Any], int]:
    """Get promotional banners/ads for the home page carousel."""
    try:
        result = _get_banners_data()
        log.info(f"HOME_BANNERS | count={len(result.get('banners', []))}")
        return jsonify(_build_success_response(result)), 200
    except Exception as e:
        log.error(f"HOME_BANNERS_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load banners")), 500


@bp.route("/api/v1/home/categories", methods=["GET"])
def get_categories() -> tuple[Dict[str, Any], int]:
    """Get product categories for the home page."""
    try:
        show_all = request.args.get("all", "").lower() in ("true", "1", "yes")
        result = _get_categories_data(show_all=show_all)
        log.info(f"HOME_CATEGORIES | count={len(result.get('categories', []))} | show_all={show_all}")
        return jsonify(_build_success_response(result)), 200
    except Exception as e:
        log.error(f"HOME_CATEGORIES_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load categories")), 500


@bp.route("/api/v1/home/best-selling", methods=["GET"])
def get_best_selling() -> tuple[Dict[str, Any], int]:
    """
    Get best-selling products for the home page.
    
    Fetches products from Elasticsearch using stored product IDs.
    Returns exactly the products specified in the JSON (no randomization).
    """
    try:
        result = _get_best_selling_data()
        log.info(f"HOME_BEST_SELLING | returned={len(result.get('products', []))}")
        return jsonify(_build_success_response(result)), 200
    except Exception as e:
        log.error(f"HOME_BEST_SELLING_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load best-selling products")), 500


@bp.route("/api/v1/home/curated", methods=["GET", "POST"])
def get_curated_home() -> tuple[Dict[str, Any], int]:
    """
    Get 4 random curated products for the home page.

    Randomly selects from the curated pool. Supports personalization filters:
    - GET: ?price_range=below_99&flean_score=8_plus&preferences=no_palm_oil&dietary=gluten_free
    - POST: {"filters": {"price_range": "below_99", "flean_score": "8_plus", ...}}

    Same filter schema as POST /api/v1/products (price_range, flean_score, preferences, dietary).
    When filters are provided, products are filtered in-memory before random sampling.
    """
    try:
        filters = _parse_filters_from_request()
        result = _get_curated_data(filters=filters, limit=4)
        log.info(
            f"HOME_CURATED | pool_size={result.get('total_in_pool', 0)} | "
            f"returned={len(result.get('products', []))} | filters_applied={result.get('filters_applied', False)}"
        )
        return jsonify(_build_success_response(result)), 200
    except Exception as e:
        log.error(f"HOME_CURATED_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load curated products")), 500


@bp.route("/api/v1/home/curated/all", methods=["GET", "POST"])
def get_curated_all() -> tuple[Dict[str, Any], int]:
    """
    Get all curated products (for "See All" page).

    Supports same personalization filters as /curated (GET query params or POST body).
    When filters are provided, returns only products matching the criteria.
    """
    try:
        filters = _parse_filters_from_request()
        result = _get_curated_data(filters=filters, limit=None)
        # Normalize response for "all" endpoint (total_count for backward compat)
        response_data = {
            "products": result["products"],
            "section_title": "Curated For You",
            "total_count": result["total_in_pool"],
            "filters_applied": result.get("filters_applied", False),
        }
        log.info(
            f"HOME_CURATED_ALL | total={result['total_in_pool']} | "
            f"returned={len(result['products'])} | filters_applied={result.get('filters_applied', False)}"
        )
        return jsonify(_build_success_response(response_data)), 200
    except Exception as e:
        log.error(f"HOME_CURATED_ALL_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load curated products")), 500


@bp.route("/api/v1/home/why-flean", methods=["GET"])
def get_why_flean() -> tuple[Dict[str, Any], int]:
    """Get 'Why Flean' value proposition cards."""
    try:
        result = _get_why_flean_data()
        log.info(f"HOME_WHY_FLEAN | count={len(result.get('cards', []))}")
        return jsonify(_build_success_response(result)), 200
    except Exception as e:
        log.error(f"HOME_WHY_FLEAN_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load Why Flean content")), 500


@bp.route("/api/v1/home/collaborations", methods=["GET"])
def get_collaborations() -> tuple[Dict[str, Any], int]:
    """Get exclusive collaboration brand partners."""
    try:
        result = _get_collaborations_data()
        log.info(f"HOME_COLLABORATIONS | count={len(result.get('brands', []))}")
        return jsonify(_build_success_response(result)), 200
    except Exception as e:
        log.error(f"HOME_COLLABORATIONS_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load collaborations")), 500


# ============================================================================
# Unified Home Page Endpoint
# ============================================================================

@bp.route("/api/v1/home/unified", methods=["POST"])
def get_unified_home() -> tuple[Dict[str, Any], int]:
    """
    Unified endpoint returning all home page sections in a single response.
    
    This endpoint is designed for the "Save and Refresh" flow from the Curate
    bottom sheet in the Flutter app. It aggregates all 6 sections into one
    response, with the curated section re-randomized on each call.
    
    Request Body (optional):
    {
        "filters": {
            "price_range": "below_99",
            "flean_score": "8_plus",
            "preferences": ["no_palm_oil"],
            "dietary": ["gluten_free"]
        }
    }
    Same filter schema as POST /api/v1/products. When provided, curated
    products are filtered by these criteria before random sampling.
    macro_preferences is also accepted but reserved for future use.
    
    Response:
    {
        "success": true,
        "data": {
            "banners": {...},
            "categories": {...},
            "best_selling": {...},
            "curated": {...},
            "why_flean": {...},
            "collaborations": {...}
        },
        "meta": {
            "timestamp": "2025-02-26T...",
            "filters_applied": true/false,
            "macro_preferences_received": true/false
        }
    }
    """
    try:
        # Parse request body (optional)
        request_data = request.get_json(silent=True) or {}
        macro_preferences = request_data.get("macro_preferences", {})
        curated_filters = _parse_filters_from_dict(request_data)

        log.info(
            f"HOME_UNIFIED_START | has_macro_prefs={bool(macro_preferences)} | "
            f"curated_filters={bool(curated_filters)}"
        )
        
        # Track any errors for partial success reporting
        errors = {}
        
        # Fetch all sections
        try:
            banners = _get_banners_data()
        except Exception as e:
            log.error(f"HOME_UNIFIED_BANNERS_ERROR | error={e}")
            banners = {"banners": [], "_error": str(e)}
            errors["banners"] = str(e)
        
        try:
            categories = _get_categories_data(show_all=False)
        except Exception as e:
            log.error(f"HOME_UNIFIED_CATEGORIES_ERROR | error={e}")
            categories = {"categories": [], "has_more": False, "total_count": 0, "_error": str(e)}
            errors["categories"] = str(e)
        
        try:
            best_selling = _get_best_selling_data()
        except Exception as e:
            log.error(f"HOME_UNIFIED_BEST_SELLING_ERROR | error={e}")
            best_selling = {"products": [], "section_title": "Best Selling", "_error": str(e)}
            errors["best_selling"] = str(e)
        
        try:
            curated = _get_curated_data(filters=curated_filters, limit=4)
        except Exception as e:
            log.error(f"HOME_UNIFIED_CURATED_ERROR | error={e}")
            curated = {"products": [], "section_title": "Curated For You", "has_more": False, "_error": str(e)}
            errors["curated"] = str(e)
        
        try:
            why_flean = _get_why_flean_data()
        except Exception as e:
            log.error(f"HOME_UNIFIED_WHY_FLEAN_ERROR | error={e}")
            why_flean = {"cards": [], "section_title": "Why Flean", "_error": str(e)}
            errors["why_flean"] = str(e)
        
        try:
            collaborations = _get_collaborations_data()
        except Exception as e:
            log.error(f"HOME_UNIFIED_COLLABORATIONS_ERROR | error={e}")
            collaborations = {"brands": [], "section_title": "Exclusive Collaborations", "_error": str(e)}
            errors["collaborations"] = str(e)
        
        # Build unified response
        unified_data = {
            "banners": banners,
            "categories": categories,
            "best_selling": best_selling,
            "curated": curated,
            "why_flean": why_flean,
            "collaborations": collaborations
        }
        
        # Build meta information
        meta = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "filters_applied": bool(curated_filters),
            "macro_preferences_received": bool(macro_preferences),
            "sections_count": 6,
            "errors_count": len(errors)
        }
        
        if errors:
            meta["errors"] = errors
        
        log.info(
            f"HOME_UNIFIED_SUCCESS | "
            f"banners={len(banners.get('banners', []))} | "
            f"categories={len(categories.get('categories', []))} | "
            f"best_selling={len(best_selling.get('products', []))} | "
            f"curated={len(curated.get('products', []))} | "
            f"why_flean={len(why_flean.get('cards', []))} | "
            f"collaborations={len(collaborations.get('brands', []))} | "
            f"errors={len(errors)}"
        )
        
        return jsonify(_build_success_response(unified_data, meta)), 200
        
    except Exception as e:
        log.error(f"HOME_UNIFIED_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load unified home page data")), 500


@bp.route("/api/v1/home/unified", methods=["GET"])
def get_unified_home_get() -> tuple[Dict[str, Any], int]:
    """
    GET version of unified endpoint for convenience.
    Equivalent to POST with empty body.
    """
    try:
        log.info("HOME_UNIFIED_GET_START")
        
        # Reuse the same logic as POST
        unified_data = {
            "banners": _get_banners_data(),
            "categories": _get_categories_data(show_all=False),
            "best_selling": _get_best_selling_data(),
            "curated": _get_curated_data(),
            "why_flean": _get_why_flean_data(),
            "collaborations": _get_collaborations_data()
        }
        
        meta = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "macro_preferences_received": False,
            "macro_preferences_applied": False,
            "sections_count": 6
        }
        
        log.info(f"HOME_UNIFIED_GET_SUCCESS | curated_count={len(unified_data['curated'].get('products', []))}")
        
        return jsonify(_build_success_response(unified_data, meta)), 200
        
    except Exception as e:
        log.error(f"HOME_UNIFIED_GET_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load unified home page data")), 500


# ============================================================================
# Health Check & Utilities
# ============================================================================

@bp.route("/api/v1/home/health", methods=["GET"])
def home_page_health() -> tuple[Dict[str, Any], int]:
    """Health check for the home page API."""
    try:
        files_status = {}
        all_ok = True
        
        for filename in [
            "banners.json",
            "categories.json", 
            "best_selling_products.json",
            "curated_products.json",
            "why_flean.json",
            "collaborations.json"
        ]:
            try:
                _get_json_data(filename)
                files_status[filename] = "ok"
            except Exception as e:
                files_status[filename] = f"error: {str(e)}"
                all_ok = False
        
        return jsonify({
            "status": "healthy" if all_ok else "degraded",
            "data_files": files_status,
            "version": "1.1.0"
        }), 200 if all_ok else 503
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 503


@bp.route("/api/v1/home/refresh", methods=["POST"])
def refresh_data() -> tuple[Dict[str, Any], int]:
    """
    Clear the JSON file cache to force reload on next request.
    
    Useful for updating data without restarting the server.
    """
    try:
        _load_json_file.cache_clear()
        log.info("HOME_PAGE_CACHE_CLEARED")
        
        return jsonify({
            "success": True,
            "message": "Cache cleared successfully. Data will be reloaded on next request."
        }), 200
        
    except Exception as e:
        log.error(f"HOME_PAGE_REFRESH_ERROR | error={e}")
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to refresh cache")), 500


# Keep the old route for backwards compatibility
@bp.route("/api/v1/home/reload", methods=["POST"])
def reload_cache() -> tuple[Dict[str, Any], int]:
    """Alias for refresh_data (backwards compatibility)."""
    return refresh_data()
