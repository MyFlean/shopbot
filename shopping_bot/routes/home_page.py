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

from ..data_fetchers.es_products import get_es_fetcher, transform_to_product_card

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


def _get_curated_data() -> Dict[str, Any]:
    """Fetch curated products data for unified response (randomized)."""
    data = _get_json_data("curated_products.json", {"product_ids": []})
    all_product_ids = data.get("product_ids", [])
    
    if not all_product_ids:
        return {
            "products": [],
            "section_title": "Curated For You",
            "has_more": False,
            "total_in_pool": 0
        }
    
    count = min(4, len(all_product_ids))
    selected_ids = random.sample(all_product_ids, count)
    products = _fetch_products_by_ids(selected_ids)
    
    return {
        "products": products,
        "section_title": "Curated For You",
        "has_more": len(all_product_ids) > 4,
        "total_in_pool": len(all_product_ids)
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


@bp.route("/api/v1/home/curated", methods=["GET"])
def get_curated_home() -> tuple[Dict[str, Any], int]:
    """
    Get 4 random curated products for the home page.
    
    Randomly selects 4 product IDs from the curated list,
    then fetches full product details from Elasticsearch.
    """
    try:
        result = _get_curated_data()
        log.info(f"HOME_CURATED | pool_size={result.get('total_in_pool', 0)} | returned={len(result.get('products', []))}")
        return jsonify(_build_success_response(result)), 200
    except Exception as e:
        log.error(f"HOME_CURATED_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load curated products")), 500


@bp.route("/api/v1/home/curated/all", methods=["GET"])
def get_curated_all() -> tuple[Dict[str, Any], int]:
    """
    Get all curated products (for "See All" page).
    
    Fetches full product details from Elasticsearch for all curated product IDs.
    """
    try:
        data = _get_json_data("curated_products.json", {"product_ids": []})
        all_product_ids = data.get("product_ids", [])
        
        if not all_product_ids:
            log.warning("HOME_CURATED_ALL | No product IDs configured")
            return jsonify(_build_success_response({
                "products": [],
                "section_title": "Curated For You"
            })), 200
        
        # Fetch all products from ES
        products = _fetch_products_by_ids(all_product_ids)
        
        log.info(f"HOME_CURATED_ALL | total={len(all_product_ids)} | returned={len(products)}")
        
        return jsonify(_build_success_response({
            "products": products,
            "section_title": "Curated For You",
            "total_count": len(products)
        })), 200
        
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
        "macro_preferences": {
            "protein": {"operator": "gte", "value": 15},
            "sodium": {"operator": "lte", "value": 300}
        }
    }
    
    Note: macro_preferences is accepted but NOT applied to home page sections.
    It's reserved for future use. Currently, curated products are randomly
    selected from a pre-defined pool without any filtering.
    
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
            "macro_preferences_received": true/false,
            "macro_preferences_applied": false
        }
    }
    """
    try:
        # Parse request body (optional)
        request_data = request.get_json(silent=True) or {}
        macro_preferences = request_data.get("macro_preferences", {})
        
        log.info(f"HOME_UNIFIED_START | has_macro_prefs={bool(macro_preferences)}")
        
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
            curated = _get_curated_data()
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
            "macro_preferences_received": bool(macro_preferences),
            "macro_preferences_applied": False,  # Not applied on home page
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
