# shopping_bot/routes/home_page.py
"""
Home Page API - Flutter App Home Screen Endpoints

This module provides 6 API endpoints for the Flutter app's home page:
1. GET /api/v1/home/banners - Promotional banners/ads carousel
2. GET /api/v1/home/categories - Product categories (4 by default, all with ?all=true)
3. GET /api/v1/home/best-selling - 4 random products from 10 best sellers
4. GET /api/v1/home/curated - 4 random products from 25 curated items
5. GET /api/v1/home/why-flean - Value proposition cards
6. GET /api/v1/home/collaborations - Partner brand names

All data is loaded from JSON files in shopping_bot/data/home/
"""

from __future__ import annotations

import json
import logging
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    
    Args:
        filename: Name of the JSON file (e.g., "banners.json")
    
    Returns:
        Parsed JSON as a dictionary
    
    Raises:
        FileNotFoundError: If the file doesn't exist
        json.JSONDecodeError: If the file contains invalid JSON
    """
    file_path = DATA_DIR / filename
    log.debug(f"Loading JSON file: {file_path}")
    
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return data


def _get_json_data(filename: str, default: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Safely load JSON data with error handling.
    
    Args:
        filename: Name of the JSON file
        default: Default value if file cannot be loaded
    
    Returns:
        Parsed JSON or default value
    """
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
# API Endpoints
# ============================================================================

@bp.route("/api/v1/home/banners", methods=["GET"])
def get_banners() -> tuple[Dict[str, Any], int]:
    """
    Get promotional banners/ads for the home page carousel.
    
    ---
    Response (JSON):
    {
        "success": true,
        "data": {
            "banners": [
                {
                    "id": "banner_1",
                    "main_heading": "Republic Day Offer",
                    "sub_heading": "Get 25%",
                    "button_text": "Grab Offer",
                    "image_url": "...",
                    "deep_link": "...",
                    "background_color": "#FF6B35",
                    "active": true
                }
            ]
        }
    }
    """
    try:
        data = _get_json_data("banners.json", {"banners": []})
        
        # Filter only active banners
        banners = data.get("banners", [])
        active_banners = [b for b in banners if b.get("active", True)]
        
        log.info(f"HOME_BANNERS | count={len(active_banners)}")
        
        return jsonify(_build_success_response({
            "banners": active_banners
        })), 200
        
    except Exception as e:
        log.error(f"HOME_BANNERS_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response(
            "INTERNAL_ERROR",
            "Failed to load banners"
        )), 500


@bp.route("/api/v1/home/categories", methods=["GET"])
def get_categories() -> tuple[Dict[str, Any], int]:
    """
    Get product categories for the home page.
    
    Query Parameters:
        all (bool): If true, return all categories. Default returns first 4.
    
    ---
    Response (JSON):
    {
        "success": true,
        "data": {
            "categories": [
                {
                    "id": "smart_snacks",
                    "name": "Smart Snacks",
                    "icon_url": "...",
                    "deep_link": "...",
                    "display_order": 1
                }
            ],
            "has_more": true
        }
    }
    """
    try:
        data = _get_json_data("categories.json", {"categories": []})
        categories = data.get("categories", [])
        
        # Sort by display_order
        categories = sorted(categories, key=lambda c: c.get("display_order", 999))
        
        # Check if all categories requested
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
        return jsonify(_build_error_response(
            "INTERNAL_ERROR",
            "Failed to load categories"
        )), 500


@bp.route("/api/v1/home/best-selling", methods=["GET"])
def get_best_selling() -> tuple[Dict[str, Any], int]:
    """
    Get best-selling products for the home page.
    
    Randomly selects 4 products from the pool of 10 best sellers.
    Each request may return different products.
    
    Query Parameters:
        count (int): Number of products to return (1-10, default: 4)
    
    ---
    Response (JSON):
    {
        "success": true,
        "data": {
            "products": [
                {
                    "id": "prod_bs_001",
                    "name": "Organic Peanut Butter",
                    "brand": "Pintola",
                    "price": 349.0,
                    "mrp": 399.0,
                    "image_url": "...",
                    "flean_score": 85.5,
                    "flean_percentile": 92.0,
                    "category": "Smart Snacks"
                }
            ],
            "section_title": "Best Selling"
        }
    }
    """
    try:
        data = _get_json_data("best_selling_products.json", {"products": []})
        products = data.get("products", [])
        
        # Get requested count (default 4, max 10)
        try:
            count = int(request.args.get("count", 4))
            count = max(1, min(count, len(products), 10))
        except (TypeError, ValueError):
            count = 4
        
        # Randomly select products
        if len(products) > count:
            selected = random.sample(products, count)
        else:
            selected = products[:count]
        
        log.info(f"HOME_BEST_SELLING | pool_size={len(products)} | returned={len(selected)}")
        
        return jsonify(_build_success_response({
            "products": selected,
            "section_title": "Best Selling",
            "total_in_pool": len(products)
        })), 200
        
    except Exception as e:
        log.error(f"HOME_BEST_SELLING_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response(
            "INTERNAL_ERROR",
            "Failed to load best-selling products"
        )), 500


@bp.route("/api/v1/home/curated", methods=["GET"])
def get_curated() -> tuple[Dict[str, Any], int]:
    """
    Get curated products for the home page.
    
    Randomly selects 4 products from the pool of 25 curated items.
    Each request may return different products.
    
    Query Parameters:
        count (int): Number of products to return (1-25, default: 4)
    
    ---
    Response (JSON):
    {
        "success": true,
        "data": {
            "products": [
                {
                    "id": "prod_cur_001",
                    "name": "A2 Cow Milk",
                    "brand": "Akshayakalpa",
                    "price": 85.0,
                    "mrp": 95.0,
                    "image_url": "...",
                    "flean_score": 92.0,
                    "flean_percentile": 98.0,
                    "category": "Dairy & Bakery"
                }
            ],
            "section_title": "Curated For You"
        }
    }
    """
    try:
        data = _get_json_data("curated_products.json", {"products": []})
        products = data.get("products", [])
        
        # Get requested count (default 4, max 25)
        try:
            count = int(request.args.get("count", 4))
            count = max(1, min(count, len(products), 25))
        except (TypeError, ValueError):
            count = 4
        
        # Randomly select products
        if len(products) > count:
            selected = random.sample(products, count)
        else:
            selected = products[:count]
        
        log.info(f"HOME_CURATED | pool_size={len(products)} | returned={len(selected)}")
        
        return jsonify(_build_success_response({
            "products": selected,
            "section_title": "Curated For You",
            "total_in_pool": len(products)
        })), 200
        
    except Exception as e:
        log.error(f"HOME_CURATED_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response(
            "INTERNAL_ERROR",
            "Failed to load curated products"
        )), 500


@bp.route("/api/v1/home/why-flean", methods=["GET"])
def get_why_flean() -> tuple[Dict[str, Any], int]:
    """
    Get "Why Flean" value proposition cards.
    
    ---
    Response (JSON):
    {
        "success": true,
        "data": {
            "cards": [
                {
                    "id": "why_1",
                    "main_heading": "AI-Powered Curation",
                    "text_body": "Our AI analyzes 300+ toxins...",
                    "icon_url": "...",
                    "display_order": 1
                }
            ],
            "section_title": "Why Flean"
        }
    }
    """
    try:
        data = _get_json_data("why_flean.json", {"cards": []})
        cards = data.get("cards", [])
        
        # Sort by display_order
        cards = sorted(cards, key=lambda c: c.get("display_order", 999))
        
        log.info(f"HOME_WHY_FLEAN | count={len(cards)}")
        
        return jsonify(_build_success_response({
            "cards": cards,
            "section_title": "Why Flean"
        })), 200
        
    except Exception as e:
        log.error(f"HOME_WHY_FLEAN_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response(
            "INTERNAL_ERROR",
            "Failed to load Why Flean content"
        )), 500


@bp.route("/api/v1/home/collaborations", methods=["GET"])
def get_collaborations() -> tuple[Dict[str, Any], int]:
    """
    Get exclusive collaboration brand partners.
    
    ---
    Response (JSON):
    {
        "success": true,
        "data": {
            "collaborations": [
                {
                    "id": "collab_1",
                    "brand_name": "Organic India",
                    "logo_url": "...",
                    "description": "Premium organic wellness products"
                }
            ],
            "section_title": "Exclusive Collaborations"
        }
    }
    """
    try:
        data = _get_json_data("collaborations.json", {"collaborations": []})
        collaborations = data.get("collaborations", [])
        
        log.info(f"HOME_COLLABORATIONS | count={len(collaborations)}")
        
        return jsonify(_build_success_response({
            "collaborations": collaborations,
            "section_title": "Exclusive Collaborations"
        })), 200
        
    except Exception as e:
        log.error(f"HOME_COLLABORATIONS_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response(
            "INTERNAL_ERROR",
            "Failed to load collaborations"
        )), 500


# ============================================================================
# Health Check & Utilities
# ============================================================================

@bp.route("/api/v1/home/health", methods=["GET"])
def home_page_health() -> tuple[Dict[str, Any], int]:
    """Health check for the home page API."""
    try:
        # Try loading all JSON files
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
            "version": "1.0.0"
        }), 200 if all_ok else 503
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 503


@bp.route("/api/v1/home/reload", methods=["POST"])
def reload_cache() -> tuple[Dict[str, Any], int]:
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
        log.error(f"HOME_PAGE_CACHE_CLEAR_ERROR | error={e}")
        return jsonify(_build_error_response(
            "INTERNAL_ERROR",
            "Failed to clear cache"
        )), 500

