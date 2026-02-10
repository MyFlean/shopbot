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

Products (curated, best-selling) are fetched from Elasticsearch using stored product IDs.
Other data is loaded from JSON files in shopping_bot/data/home/
"""

from __future__ import annotations

import json
import logging
import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

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
# Elasticsearch Product Fetching
# ============================================================================

def _fetch_products_by_ids(product_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Fetch products from Elasticsearch by their IDs.
    
    Args:
        product_ids: List of Elasticsearch product IDs
    
    Returns:
        List of product dictionaries with standardized format
    """
    if not product_ids:
        return []
    
    try:
        from ..data_fetchers.es_products import get_es_fetcher
        
        fetcher = get_es_fetcher()
        
        # Use the IDs query - returns list of raw _source dicts
        es_products = fetcher.search_by_ids(product_ids)
        
        # Transform ES products to API format
        transformed = []
        for es_prod in es_products:
            transformed.append(_transform_es_product(es_prod))
        
        log.debug(f"ES_FETCH | requested={len(product_ids)} | returned={len(transformed)}")
        return transformed
        
    except Exception as e:
        log.error(f"ES_FETCH_ERROR | error={e}", exc_info=True)
        return []


def _transform_es_product(src: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform raw ES _source to standardized API format.
    
    Includes macro_tags generation (top 2 nutritional values).
    
    Args:
        src: Raw _source dict from Elasticsearch
    
    Returns:
        Transformed product dict for API response
    """
    # Extract nutritional info (try both nutri_breakdown and nutri_breakdown_updated)
    nutritional_data = src.get("category_data", {}).get("nutritional", {})
    nutrition = nutritional_data.get("nutri_breakdown_updated", {}) or nutritional_data.get("nutri_breakdown", {})
    
    # Extract nutrition values (try both underscore and space variants)
    protein_g = nutrition.get("protein_g") or nutrition.get("protein g")
    carbs_g = nutrition.get("carbs_g") or nutrition.get("carbohydrates g") or nutrition.get("carbs g")
    fat_g = nutrition.get("fat_g") or nutrition.get("total fat g") or nutrition.get("fat g")
    calories = nutrition.get("energy_kcal") or nutrition.get("energy kcal")
    
    # Generate macro tags from nutritional data
    macro_tags = _generate_macro_tags_from_values(protein_g, carbs_g, fat_g, calories)
    
    # Extract quantity
    qty = nutritional_data.get("qty", "")
    
    # Extract flean_score (nested structure)
    flean_score_data = src.get("flean_score", {})
    flean_score = flean_score_data.get("adjusted_score") if isinstance(flean_score_data, dict) else flean_score_data
    
    # Extract flean_percentile from stats
    stats = src.get("stats", {})
    flean_percentile = None
    if stats.get("adjusted_score_percentiles"):
        flean_percentile = stats["adjusted_score_percentiles"].get("subcategory_percentile")
    
    # Get best image URL
    image_url = _get_best_image(src.get("hero_image", {}))
    
    return {
        "id": src.get("id", ""),
        "name": src.get("name", ""),
        "brand": src.get("brand", ""),
        "price": src.get("price"),
        "mrp": src.get("mrp"),
        "image_url": image_url,
        "qty": qty,
        "macro_tags": macro_tags,
        "flean_score": flean_score,
        "flean_percentile": flean_percentile,
        "in_stock": True
    }


def _get_best_image(hero_image: Dict[str, Any]) -> str:
    """Extract best available image URL from hero_image structure."""
    if not hero_image or not isinstance(hero_image, dict):
        return ""
    
    # Priority: amazon_cdn_thumbnail → amazon_cdn_link → original
    for key in ["amazon_cdn_thumbnail", "amazon_cdn_link", "url", "original"]:
        url = hero_image.get(key)
        if url and isinstance(url, str) and url.startswith("http"):
            return url
    
    return ""


def _generate_macro_tags_from_values(
    protein_g: Optional[float],
    carbs_g: Optional[float],
    fat_g: Optional[float],
    calories: Optional[float],
    max_tags: int = 2
) -> List[Dict[str, Any]]:
    """
    Generate macro tags from nutritional values.
    
    Returns top N highest nutritional values as formatted tags.
    
    Args:
        protein_g: Protein in grams
        carbs_g: Carbohydrates in grams
        fat_g: Fat in grams
        calories: Calories in kcal
        max_tags: Maximum number of tags to return (default: 2)
    
    Returns:
        List of macro tag dicts with label, nutrient, value, and unit
    """
    macro_data = [
        (protein_g, "protein", "g", "{value} gms of Protein"),
        (carbs_g, "carbs", "g", "{value} gms of Carbs"),
        (fat_g, "fat", "g", "{value} gms of Fat"),
        (calories, "calories", "kcal", "{value} Calories"),
    ]
    
    available_macros: List[Tuple[float, str, str, str]] = []
    
    for value, nutrient, unit, label_format in macro_data:
        if value is not None:
            try:
                numeric_value = float(value)
                if numeric_value > 0:
                    available_macros.append((numeric_value, nutrient, unit, label_format))
            except (TypeError, ValueError):
                continue
    
    # Sort by value descending (highest macros first)
    available_macros.sort(key=lambda x: x[0], reverse=True)
    
    # Take top N
    top_macros = available_macros[:max_tags]
    
    macro_tags = []
    for numeric_value, nutrient, unit, label_format in top_macros:
        display_value = int(numeric_value) if numeric_value == int(numeric_value) else round(numeric_value, 1)
        tag = {
            "label": label_format.format(value=display_value),
            "nutrient": nutrient,
            "value": display_value,
            "unit": unit
        }
        macro_tags.append(tag)
    
    return macro_tags


# ============================================================================
# API Endpoints
# ============================================================================

@bp.route("/api/v1/home/banners", methods=["GET"])
def get_banners() -> tuple[Dict[str, Any], int]:
    """Get promotional banners/ads for the home page carousel."""
    try:
        data = _get_json_data("banners.json", {"banners": []})
        banners = data.get("banners", [])
        active_banners = [b for b in banners if b.get("active", True)]
        
        log.info(f"HOME_BANNERS | count={len(active_banners)}")
        
        return jsonify(_build_success_response({
            "banners": active_banners
        })), 200
        
    except Exception as e:
        log.error(f"HOME_BANNERS_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load banners")), 500


@bp.route("/api/v1/home/categories", methods=["GET"])
def get_categories() -> tuple[Dict[str, Any], int]:
    """Get product categories for the home page."""
    try:
        data = _get_json_data("categories.json", {"categories": []})
        categories = data.get("categories", [])
        categories = sorted(categories, key=lambda c: c.get("display_order", 999))
        
        show_all = request.args.get("all", "").lower() in ("true", "1", "yes")
        
        if show_all:
            result_categories = categories
            has_more = False
        else:
            result_categories = categories[:4]
            has_more = len(categories) > 4
        
        log.info(f"HOME_CATEGORIES | count={len(result_categories)} | show_all={show_all}")
        
        return jsonify(_build_success_response({
            "categories": result_categories,
            "has_more": has_more,
            "total_count": len(categories)
        })), 200
        
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
        data = _get_json_data("best_selling_products.json", {"product_ids": []})
        product_ids = data.get("product_ids", [])
        
        if not product_ids:
            log.warning("HOME_BEST_SELLING | No product IDs configured")
            return jsonify(_build_success_response({
                "products": [],
                "section_title": "Best Selling"
            })), 200
        
        # Fetch products from ES
        products = _fetch_products_by_ids(product_ids)
        
        log.info(f"HOME_BEST_SELLING | requested={len(product_ids)} | returned={len(products)}")
        
        return jsonify(_build_success_response({
            "products": products,
            "section_title": "Best Selling"
        })), 200
        
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
        data = _get_json_data("curated_products.json", {"product_ids": []})
        all_product_ids = data.get("product_ids", [])
        
        if not all_product_ids:
            log.warning("HOME_CURATED | No product IDs configured")
            return jsonify(_build_success_response({
                "products": [],
                "section_title": "Curated For You",
                "has_more": False
            })), 200
        
        # Randomly select 4 product IDs
        count = min(4, len(all_product_ids))
        selected_ids = random.sample(all_product_ids, count)
        
        # Fetch products from ES
        products = _fetch_products_by_ids(selected_ids)
        
        log.info(f"HOME_CURATED | pool_size={len(all_product_ids)} | returned={len(products)}")
        
        return jsonify(_build_success_response({
            "products": products,
            "section_title": "Curated For You",
            "has_more": len(all_product_ids) > 4,
            "total_in_pool": len(all_product_ids)
        })), 200
        
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
        data = _get_json_data("why_flean.json", {"cards": []})
        cards = data.get("cards", [])
        cards = sorted(cards, key=lambda c: c.get("display_order", 999))
        
        log.info(f"HOME_WHY_FLEAN | count={len(cards)}")
        
        return jsonify(_build_success_response({
            "cards": cards,
            "section_title": "Why Flean"
        })), 200
        
    except Exception as e:
        log.error(f"HOME_WHY_FLEAN_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load Why Flean content")), 500


@bp.route("/api/v1/home/collaborations", methods=["GET"])
def get_collaborations() -> tuple[Dict[str, Any], int]:
    """Get exclusive collaboration brand partners."""
    try:
        data = _get_json_data("collaborations.json", {"collaborations": []})
        collaborations = data.get("collaborations", [])
        
        log.info(f"HOME_COLLABORATIONS | count={len(collaborations)}")
        
        return jsonify(_build_success_response({
            "brands": collaborations,
            "section_title": "Exclusive Collaborations"
        })), 200
        
    except Exception as e:
        log.error(f"HOME_COLLABORATIONS_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load collaborations")), 500


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
