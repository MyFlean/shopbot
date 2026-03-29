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

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher, transform_to_product_card
from .product_api import _validate_filters

log = logging.getLogger(__name__)
bp = Blueprint("home_page", __name__)

# ============================================================================
# Flean Picks – subcategory-to-ES-path mapping and base filters
# ============================================================================

FLEAN_PICKS_CATEGORIES = {
    "high_protein_snacks": {
        "name": "High Protein Snacks",
        "es_paths": [
            "f_and_b/food/light_bites/energy_bars",
            "f_and_b/food/light_bites/dry_fruit_and_nut_snacks",
        ],
    },
    "no_guilt_spreads": {
        "name": "No Guilt Spreads",
        "es_paths": [
            "f_and_b/food/spreads_and_condiments/peanut_butter",
            "f_and_b/food/spreads_and_condiments/honey_and_spreads",
        ],
    },
    "powerpacked_breakfast": {
        "name": "Powerpacked Breakfast",
        "es_paths": [
            "f_and_b/food/breakfast_essentials/muesli_and_oats",
            "f_and_b/food/breakfast_essentials/dates_and_seeds",
        ],
    },
    "no_guilt_munchies": {
        "name": "No Guilt Munchies",
        "es_paths": [
            "f_and_b/food/light_bites/chips_and_crisps",
            "f_and_b/food/light_bites/savory_namkeen",
        ],
    },
}

BASE_PERSONALIZATION_FILTERS: Dict[str, Any] = {
    "preferences": ["no_palm_oil"],
}

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
        cards = [c for c in (transform_to_product_card(src) for src in es_products if src) if c is not None]
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


def _get_curated_data(use_top_4: bool = True) -> Dict[str, Any]:
    """
    Fetch hand-curated products.

    Args:
        use_top_4: If True return the 4 top-tier picks (home page).
                   If False return all 25 curated products (See All).
    """
    data = _get_json_data("curated_products.json", {"product_ids": [], "top_4": []})
    all_product_ids = data.get("product_ids", [])
    top_4_ids = data.get("top_4", [])

    if not all_product_ids:
        return {
            "products": [],
            "section_title": "Curated For You",
            "has_more": False,
            "total_in_pool": 0,
        }

    selected_ids = top_4_ids if use_top_4 else all_product_ids
    products = _fetch_products_by_ids(selected_ids)

    return {
        "products": products,
        "section_title": "Curated For You",
        "has_more": len(all_product_ids) > len(selected_ids),
        "total_in_pool": len(all_product_ids),
    }


def _get_flean_picks_data() -> Dict[str, Any]:
    """Load Flean Picks collections metadata and product IDs."""
    return _get_json_data("flean_picks.json", {"collections": []})


def _extract_curate_filters() -> Optional[Dict[str, Any]]:
    """Parse and validate personalization filters from a POST body."""
    if request.method != "POST":
        return None
    body = request.get_json(force=True, silent=True) or {}
    raw_filters = body.get("filters")
    if not raw_filters:
        return None
    validated, error = _validate_filters(raw_filters)
    if error:
        log.warning(f"CURATE_FILTER_VALIDATION | error={error}")
        return None
    return validated


def _search_curated_with_filters(filters: Dict[str, Any], size: int = 4) -> Dict[str, Any]:
    """Use search_products_unified with filters to produce curated results."""
    fetcher = get_es_fetcher()
    result = fetcher.search_products_unified(
        query=None,
        subcategory=None,
        page=0,
        size=size,
        sort_by="relevance",
        filters=filters,
    )
    products = result.get("products", [])
    cards = [transform_to_product_card(p) for p in products]
    cards = [c for c in cards if c is not None]
    total = result.get("meta", {}).get("total", len(cards))
    return {
        "products": cards,
        "section_title": "Curated For You",
        "has_more": total > size,
        "total_in_pool": total,
    }


def _merge_filters(base: Dict[str, Any], user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge base filters with user personalization filters.

    User filters override base filters on a per-key basis. Lists are merged
    (union) so that base preferences are always applied alongside user ones.
    """
    if not user:
        return dict(base)
    merged: Dict[str, Any] = {}
    for key in {*base, *user}:
        bv = base.get(key)
        uv = user.get(key)
        if isinstance(bv, list) and isinstance(uv, list):
            seen: set = set()
            merged[key] = [x for x in (bv + uv) if not (x in seen or seen.add(x))]
        elif uv is not None:
            merged[key] = uv
        else:
            merged[key] = bv
    return merged


def _fetch_subcategory_products(
    es_paths: List[str],
    user_filters: Optional[Dict[str, Any]],
    needed: int = 6,
) -> List[Dict[str, Any]]:
    """Fetch *needed* products for a subcategory using the 3-tier fallback.

    Tier 1: user filters + base filters
    Tier 2: base filters only (exclude already-fetched IDs)
    Tier 3: no filters at all (exclude already-fetched IDs)
    """
    fetcher = get_es_fetcher()
    collected: List[Dict[str, Any]] = []
    collected_ids: List[str] = []

    effective_filters = _merge_filters(BASE_PERSONALIZATION_FILTERS, user_filters)

    # --- Tier 1: full filters ---
    raw = fetcher.search_by_category_paths(
        paths=es_paths, filters=effective_filters, size=needed, exclude_ids=None,
    )
    for src in raw:
        card = transform_to_product_card(src)
        if card and card["id"] not in collected_ids:
            collected.append(card)
            collected_ids.append(card["id"])
        if len(collected) >= needed:
            return collected[:needed]

    # --- Tier 2: base filters only ---
    remaining = needed - len(collected)
    raw = fetcher.search_by_category_paths(
        paths=es_paths, filters=BASE_PERSONALIZATION_FILTERS,
        size=remaining + 4, exclude_ids=collected_ids,
    )
    for src in raw:
        card = transform_to_product_card(src)
        if card and card["id"] not in collected_ids:
            collected.append(card)
            collected_ids.append(card["id"])
        if len(collected) >= needed:
            return collected[:needed]

    # --- Tier 3: no filters ---
    remaining = needed - len(collected)
    raw = fetcher.search_by_category_paths(
        paths=es_paths, filters=None, size=remaining + 4,
        exclude_ids=collected_ids,
    )
    for src in raw:
        card = transform_to_product_card(src)
        if card and card["id"] not in collected_ids:
            collected.append(card)
            collected_ids.append(card["id"])
        if len(collected) >= needed:
            break

    return collected[:needed]


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
    """Legacy: 4 curated products for home. Now delegates to unified flean picks."""
    try:
        filters = _extract_curate_filters()
        result = _unified_flean_picks_logic("home", filters)
        wrapped = {
            "products": result.get("products", []),
            "section_title": "Curated For You",
            "has_more": True,
            "total_in_pool": 4,
        }
        log.info(f"HOME_CURATED_LEGACY | returned={len(wrapped['products'])}")
        return jsonify(_build_success_response(wrapped)), 200
    except Exception as e:
        log.error(f"HOME_CURATED_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load curated products")), 500


@bp.route("/api/v1/home/curated/all", methods=["GET", "POST"])
def get_curated_all() -> tuple[Dict[str, Any], int]:
    """Legacy: all curated products (See All). Now delegates to unified flean picks see_all."""
    try:
        filters = _extract_curate_filters()
        result = _unified_flean_picks_logic("see_all", filters)
        all_products: List[Dict[str, Any]] = []
        for coll in result.get("collections", []):
            all_products.extend(coll.get("products", []))
        wrapped = {
            "products": all_products,
            "section_title": "Curated For You",
            "total_count": len(all_products),
        }
        log.info(f"HOME_CURATED_ALL_LEGACY | returned={len(all_products)}")
        return jsonify(_build_success_response(wrapped)), 200
    except Exception as e:
        log.error(f"HOME_CURATED_ALL_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load curated products")), 500


# ============================================================================
# Unified Flean Picks API  (replaces curated + flean-picks endpoints)
# ============================================================================

def _unified_flean_picks_logic(source: str, user_filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Core logic shared by the new unified endpoint and legacy wrappers.

    source == "home"  -> 4 products (1 per subcategory, best flean score)
    source != "home"  -> 4 subcategories × 6 products each
    """
    filters_applied = _merge_filters(BASE_PERSONALIZATION_FILTERS, user_filters)

    if source == "home":
        products: List[Dict[str, Any]] = []
        for key, cfg in FLEAN_PICKS_CATEGORIES.items():
            sub_products = _fetch_subcategory_products(
                es_paths=cfg["es_paths"],
                user_filters=user_filters,
                needed=1,
            )
            products.extend(sub_products)
        return {
            "source": "home",
            "products": products,
            "filters_applied": filters_applied,
        }

    # --- see_all ---
    collections: List[Dict[str, Any]] = []
    for key, cfg in FLEAN_PICKS_CATEGORIES.items():
        sub_products = _fetch_subcategory_products(
            es_paths=cfg["es_paths"],
            user_filters=user_filters,
            needed=6,
        )
        collections.append({
            "key": key,
            "name": cfg["name"],
            "products": sub_products,
        })
    return {
        "source": "see_all",
        "collections": collections,
        "filters_applied": filters_applied,
    }


@bp.route("/api/v1/home/flean-picks", methods=["POST", "GET"])
def get_flean_picks_unified() -> tuple[Dict[str, Any], int]:
    """
    Unified Flean Picks endpoint.

    GET  -> equivalent to source=home with base filters only.
    POST ->
        {
          "source": "home" | "see_all",   // default "see_all"
          "filters": { ... }              // optional personalization
        }

    source = "home":
        Returns 4 products (1 best per subcategory).
    source = anything else / omitted:
        Returns 4 subcategories with 6 products each.

    Personalization filters always applied (base filter used as fallback).
    Three-tier fallback guarantees product count per subcategory.
    """
    try:
        if request.method == "GET":
            source = request.args.get("source", "home")
            user_filters = None
        else:
            body = request.get_json(force=True, silent=True) or {}
            source = body.get("source", "see_all")
            raw_filters = body.get("filters")
            if raw_filters:
                validated, err = _validate_filters(raw_filters)
                if err:
                    log.warning(f"FLEAN_PICKS_FILTER_ERR | {err}")
                    user_filters = None
                else:
                    user_filters = validated
            else:
                user_filters = None

        result = _unified_flean_picks_logic(source, user_filters)

        if source == "home":
            log.info(f"FLEAN_PICKS_UNIFIED | source=home | products={len(result.get('products', []))}")
        else:
            total = sum(len(c["products"]) for c in result.get("collections", []))
            log.info(f"FLEAN_PICKS_UNIFIED | source=see_all | collections={len(result.get('collections', []))} | total_products={total}")

        return jsonify(_build_success_response(result)), 200

    except Exception as exc:
        log.error(f"FLEAN_PICKS_UNIFIED_ERROR | error={exc}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load Flean Picks")), 500


# Legacy endpoints – thin wrappers around the unified logic

@bp.route("/api/v1/home/flean-picks/<collection_key>", methods=["GET"])
def get_flean_picks_collection(collection_key: str) -> tuple[Dict[str, Any], int]:
    """Legacy: return products for a single subcategory."""
    try:
        if collection_key not in FLEAN_PICKS_CATEGORIES:
            valid = list(FLEAN_PICKS_CATEGORIES.keys())
            return jsonify(_build_error_response(
                "COLLECTION_NOT_FOUND",
                f"Unknown collection '{collection_key}'. Valid: {valid}",
            )), 404

        cfg = FLEAN_PICKS_CATEGORIES[collection_key]
        products = _fetch_subcategory_products(cfg["es_paths"], user_filters=None, needed=6)

        log.info(f"FLEAN_PICKS_LEGACY | key={collection_key} | returned={len(products)}")
        return jsonify(_build_success_response({
            "key": collection_key,
            "name": cfg["name"],
            "products": products,
        })), 200
    except Exception as exc:
        log.error(f"FLEAN_PICKS_LEGACY_ERROR | key={collection_key} | error={exc}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load collection")), 500


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

    Aggregates all 6 sections into one response. Curated section is
    re-randomized on each call.

    Request Body (optional, accepted but preferences not applied):
        {
            "ingredient_preferences": [...],
            "daily_macros": {...},
            "dietary_restrictions": [...]
        }
    """
    try:
        log.info("HOME_UNIFIED_START")

        errors = {}

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
            curated = _get_curated_data(use_top_4=True)
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

        unified_data = {
            "banners": banners,
            "categories": categories,
            "best_selling": best_selling,
            "curated": curated,
            "why_flean": why_flean,
            "collaborations": collaborations
        }

        meta = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
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
    """GET version of unified endpoint. Equivalent to POST with empty body."""
    try:
        log.info("HOME_UNIFIED_GET_START")

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
