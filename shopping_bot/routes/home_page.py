# shopping_bot/routes/home_page.py
"""
Home Page API - Flutter App Home Screen Endpoints

This module provides API endpoints for the Flutter app's home page:
1. GET /api/v1/home/banners - Promotional banners/ads carousel
2. GET /api/v1/home/categories - Product categories (4 by default, all with ?all=true)
3. GET /api/v1/home/best-selling - Best selling products (category-path score based)
4. GET /api/v1/home/curated - Legacy home curated strip (unified flean picks home; up to 8 from ES)
5. GET /api/v1/home/curated/all - Legacy See All (unified flean picks collections; up to 12 per bucket from ES)
6. GET /api/v1/home/why-flean - Value proposition cards
7. GET /api/v1/home/collaborations - Partner brand names
8. POST /api/v1/home/refresh - Clear cache and reload data
9. POST /api/v1/home/unified - Unified endpoint returning all sections in one response

Products are fetched from Elasticsearch (best-selling by category paths, curated by configured strategy).
Other data is loaded from JSON files in shopping_bot/data/home/
"""

from __future__ import annotations

import json
import logging
import traceback

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
        "image_url": "https://img.flean.ai/assets/Categories-Subcategories/Flean-Picks/High%20Protein%20Snacks.png?w=100",
        "es_paths": [
            "f_and_b/food/light_bites/energy_bars",
            "f_and_b/food/light_bites/dry_fruit_and_nut_snacks",
        ],
    },
    "no_guilt_spreads": {
        "name": "No Guilt Spreads",
        "image_url": "https://img.flean.ai/assets/Categories-Subcategories/Flean-Picks/No%20Guilt%20Spreads.png?w=100",
        "es_paths": [
            "f_and_b/food/spreads_and_condiments/peanut_butter",
            "f_and_b/food/spreads_and_condiments/honey_and_spreads",
        ],
    },
    "powerpacked_breakfast": {
        "name": "Powerpacked Breakfast",
        "image_url": "https://img.flean.ai/assets/Categories-Subcategories/Flean-Picks/Power-packed%20Breakfast.png?w=100",
        "es_paths": [
            "f_and_b/food/breakfast_essentials/muesli_and_oats",
            "f_and_b/food/breakfast_essentials/dates_and_seeds",
        ],
    },
    "no_guilt_munchies": {
        "name": "No Guilt Munchies",
        "image_url": "https://img.flean.ai/assets/Categories-Subcategories/Flean-Picks/No%20Guilt%20Munchies.png?w=100",
        "es_paths": [
            "f_and_b/food/light_bites/chips_and_crisps",
            "f_and_b/food/light_bites/savory_namkeen",
        ],
    },
}

BASE_PERSONALIZATION_FILTERS: Dict[str, Any] = {
    "preferences": ["no_palm_oil"],
}

BEST_SELLING_CATEGORY_PATHS: List[str] = [
    "f_and_b/food/dairy_and_bakery/bread_and_buns",
    "f_and_b/food/biscuits_and_crackers",
    "f_and_b/food/breakfast_essentials/muesli_and_oats",
]
BEST_SELLING_PER_CATEGORY = 2
BEST_SELLING_TOTAL_PRODUCTS = 6
BEST_SELLING_FETCH_BUFFER = 8

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


def _get_adjusted_score(product_src: Dict[str, Any]) -> float:
    """Extract flean_score.adjusted_score from ES source safely."""
    score_data = product_src.get("flean_score")
    if isinstance(score_data, dict):
        raw_score = score_data.get("adjusted_score")
    else:
        raw_score = score_data
    try:
        return float(raw_score)
    except (TypeError, ValueError):
        return -1.0


def _get_best_selling_data() -> Dict[str, Any]:
    """Best-selling from fixed category paths ranked by flean_score.adjusted_score."""
    fetcher = get_es_fetcher()
    selected_products: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()
    backfill_candidates: List[tuple[float, Dict[str, Any]]] = []

    for path in BEST_SELLING_CATEGORY_PATHS:
        raw_products = fetcher.search_by_category_paths(
            paths=[path],
            filters=None,
            size=BEST_SELLING_PER_CATEGORY + BEST_SELLING_FETCH_BUFFER,
            exclude_ids=None,
        )

        scored_cards: List[tuple[float, Dict[str, Any]]] = []
        for src in raw_products:
            card = transform_to_product_card(src)
            if not card:
                continue
            product_id = card.get("id")
            if not product_id:
                continue
            scored_cards.append((_get_adjusted_score(src), card))

        scored_cards.sort(key=lambda item: item[0], reverse=True)

        category_count = 0
        for score, card in scored_cards:
            product_id = card["id"]
            if product_id in selected_ids:
                continue
            if category_count < BEST_SELLING_PER_CATEGORY:
                selected_products.append(card)
                selected_ids.add(product_id)
                category_count += 1
            else:
                backfill_candidates.append((score, card))

    if len(selected_products) < BEST_SELLING_TOTAL_PRODUCTS:
        backfill_candidates.sort(key=lambda item: item[0], reverse=True)
        for _, card in backfill_candidates:
            product_id = card["id"]
            if product_id in selected_ids:
                continue
            selected_products.append(card)
            selected_ids.add(product_id)
            if len(selected_products) >= BEST_SELLING_TOTAL_PRODUCTS:
                break

    return {
        "products": selected_products[:BEST_SELLING_TOTAL_PRODUCTS],
        "section_title": "Best Selling",
    }


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


def _build_flean_hybrid_tier_filters(user_filters: Optional[Dict[str, Any]]) -> List[tuple[str, Optional[Dict[str, Any]]]]:
    """Build 3-tier filters for Flean Picks macro relaxation fallback."""
    tier1 = _merge_filters(BASE_PERSONALIZATION_FILTERS, user_filters)

    tier2 = dict(tier1)
    tier2_nutrition = dict(tier2.get("nutrition", {})) if isinstance(tier2.get("nutrition"), dict) else {}
    tier2_nutrition.pop("carbs", None)
    if tier2_nutrition:
        tier2["nutrition"] = tier2_nutrition
    else:
        tier2.pop("nutrition", None)

    tier3 = dict(tier2)
    tier3_nutrition = dict(tier3.get("nutrition", {})) if isinstance(tier3.get("nutrition"), dict) else {}
    tier3_nutrition.pop("fat", None)
    if tier3_nutrition:
        tier3["nutrition"] = tier3_nutrition
    else:
        tier3.pop("nutrition", None)

    return [
        ("tier1", tier1),
        ("tier2", tier2),
        ("tier3", tier3),
    ]


def _fetch_subcategory_products(
    es_paths: List[str],
    user_filters: Optional[Dict[str, Any]],
    needed: int = 6,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch *needed* products for a subcategory using 3-tier macro relaxation.

    Tier 1: user filters + base filters
    Tier 2: remove carbs
    Tier 3: remove fat (carbs already removed)
    """
    fetcher = get_es_fetcher()
    collected: List[Dict[str, Any]] = []
    collected_ids: List[str] = []
    tier_counts: Dict[str, int] = {"tier1": 0, "tier2": 0, "tier3": 0}

    tiers = _build_flean_hybrid_tier_filters(user_filters)
    used_filters: List[Optional[Dict[str, Any]]] = []

    for tier_name, tier_filters in tiers:
        if len(collected) >= needed:
            break
        if any(tier_filters == seen for seen in used_filters):
            continue
        used_filters.append(tier_filters)

        remaining = needed - len(collected)
        query_size = needed if tier_name == "tier1" else remaining + 4
        raw = fetcher.search_by_category_paths(
            paths=es_paths,
            filters=tier_filters,
            size=query_size,
            exclude_ids=collected_ids or None,
        )
        for src in raw:
            card = transform_to_product_card(src)
            if card and card["id"] not in collected_ids:
                collected.append(card)
                collected_ids.append(card["id"])
                tier_counts[tier_name] += 1
            if len(collected) >= needed:
                break

    collected = collected[:needed]
    fallback_used_count = tier_counts["tier2"] + tier_counts["tier3"]
    stats = {
        "requested_count": needed,
        "collected_count": len(collected),
        "tier_counts": tier_counts,
        "used_fallback": fallback_used_count > 0,
        "matched_with_user_filters_count": tier_counts["tier1"] if bool(user_filters) else 0,
        "fallback_used_count": fallback_used_count,
    }
    return collected, stats


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

    Fetches products from fixed category paths in Elasticsearch, picks top 2
    per category by flean_score.adjusted_score, and backfills to return up to 6.
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
    """Legacy: home curated strip. Delegates to unified flean picks (``source=home``, up to 8 products)."""
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
    """Legacy: See All curated products. Delegates to unified flean picks (``see_all``: 4 collections × up to 12)."""
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

    source == "home":
        Flat ``products`` list: up to 8 items (2 per Flean Picks subcategory),
        each bucket ordered by flean percentile descending in the ES query.
    source != "home" (e.g. ``see_all``):
        ``collections`` with 4 subcategories, up to 12 products each.
    Actual counts can be lower if Elasticsearch returns fewer matches after
    3-tier fallback (tier1 all filters -> tier2 remove carbs -> tier3 remove fat).
    """
    filters_applied = _merge_filters(BASE_PERSONALIZATION_FILTERS, user_filters)
    no_match_message = "No products matched your selected filters. Try relaxing your filters."

    if source == "home":
        products: List[Dict[str, Any]] = []
        per_subcategory: Dict[str, Any] = {}
        total_tier_counts = {"tier1": 0, "tier2": 0, "tier3": 0}
        requested_total = len(FLEAN_PICKS_CATEGORIES) * 2
        for key, cfg in FLEAN_PICKS_CATEGORIES.items():
            sub_products, stats = _fetch_subcategory_products(
                es_paths=cfg["es_paths"],
                user_filters=user_filters,
                needed=2,
            )
            products.extend(sub_products)
            per_subcategory[key] = {
                "requested_count": stats["requested_count"],
                "collected_count": stats["collected_count"],
                "tier_counts": stats["tier_counts"],
                "used_fallback": stats["used_fallback"],
            }
            for tier_name, count in stats["tier_counts"].items():
                total_tier_counts[tier_name] += int(count)

        fallback_used_count = total_tier_counts["tier2"] + total_tier_counts["tier3"]
        response_data: Dict[str, Any] = {
            "source": "home",
            "products": products,
            "filters_applied": filters_applied,
            "fallback_meta": {
                "requested_products": requested_total,
                "returned_products": len(products),
                "user_filters_supplied": bool(user_filters),
                "per_subcategory": per_subcategory,
                "total_tier_counts": total_tier_counts,
                "matched_with_user_filters_count": total_tier_counts["tier1"] if bool(user_filters) else 0,
                "fallback_used_count": fallback_used_count,
                "fallback_used": fallback_used_count > 0,
            },
        }
        if len(products) == 0:
            response_data["message"] = no_match_message
        return response_data

    # --- see_all ---
    collections: List[Dict[str, Any]] = []
    per_subcategory: Dict[str, Any] = {}
    total_tier_counts = {"tier1": 0, "tier2": 0, "tier3": 0}
    requested_total = len(FLEAN_PICKS_CATEGORIES) * 12
    for key, cfg in FLEAN_PICKS_CATEGORIES.items():
        sub_products, stats = _fetch_subcategory_products(
            es_paths=cfg["es_paths"],
            user_filters=user_filters,
            needed=12,
        )
        collections.append({
            "key": key,
            "name": cfg["name"],
            "image_url": cfg["image_url"],
            "products": sub_products,
        })
        per_subcategory[key] = {
            "requested_count": stats["requested_count"],
            "collected_count": stats["collected_count"],
            "tier_counts": stats["tier_counts"],
            "used_fallback": stats["used_fallback"],
        }
        for tier_name, count in stats["tier_counts"].items():
            total_tier_counts[tier_name] += int(count)

    returned_total = sum(len(c["products"]) for c in collections)
    fallback_used_count = total_tier_counts["tier2"] + total_tier_counts["tier3"]
    response_data = {
        "source": "see_all",
        "collections": collections,
        "filters_applied": filters_applied,
        "fallback_meta": {
            "requested_products": requested_total,
            "returned_products": returned_total,
            "user_filters_supplied": bool(user_filters),
            "per_subcategory": per_subcategory,
            "total_tier_counts": total_tier_counts,
            "matched_with_user_filters_count": total_tier_counts["tier1"] if bool(user_filters) else 0,
            "fallback_used_count": fallback_used_count,
            "fallback_used": fallback_used_count > 0,
        },
    }
    if returned_total == 0:
        response_data["message"] = no_match_message
    return response_data


@bp.route("/api/v1/home/flean-picks", methods=["POST", "GET"])
def get_flean_picks_unified() -> tuple[Dict[str, Any], int]:
    """
    Unified Flean Picks endpoint.

    GET:
        Query param ``source`` (default ``home``). No JSON body; ``user_filters`` are not used.
        Use ``?source=see_all`` for the collections shape (same as POST see_all).
    POST:
        JSON body: ``source`` (default ``see_all``), optional ``filters`` for personalization
        (validated; invalid ``filters`` are ignored and only base filters apply).

    Mode selection uses strict equality: only ``source == "home"`` selects home mode.

    ``source == "home"``:
        Response data includes flat ``products`` (up to 8: 2 top picks per subcategory).
    Any other ``source`` (including omitted on POST, or ``see_all`` on GET):
        Response data includes ``collections`` (4 subcategories, up to 12 products each).

    Base personalization filters are merged with validated user filters (see ``_merge_filters``).
    Per-subcategory fetch uses 3-tier macro relaxation:
      tier1 all filters -> tier2 remove carbs -> tier3 remove fat.
    Response includes ``fallback_meta`` with per-tier counts.
    If no products match after tier3, response remains HTTP 200 and includes ``message``.
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
            fallback_meta = result.get("fallback_meta", {})
            tier_counts = fallback_meta.get("total_tier_counts", {})
            log.info(
                "FLEAN_PICKS_UNIFIED | source=home | "
                f"products={len(result.get('products', []))} | "
                f"tier_counts={tier_counts} | "
                f"fallback_used={fallback_meta.get('fallback_used', False)}"
            )
        else:
            total = sum(len(c["products"]) for c in result.get("collections", []))
            fallback_meta = result.get("fallback_meta", {})
            tier_counts = fallback_meta.get("total_tier_counts", {})
            log.info(
                "FLEAN_PICKS_UNIFIED | source=see_all | "
                f"collections={len(result.get('collections', []))} | "
                f"total_products={total} | tier_counts={tier_counts} | "
                f"fallback_used={fallback_meta.get('fallback_used', False)}"
            )

        return jsonify(_build_success_response(result)), 200

    except Exception as exc:
        log.error(f"FLEAN_PICKS_UNIFIED_ERROR | error={exc}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load Flean Picks")), 500


# Legacy endpoints – thin wrappers around the unified logic

@bp.route("/api/v1/home/flean-picks/<collection_key>", methods=["GET"])
def get_flean_picks_collection(collection_key: str) -> tuple[Dict[str, Any], int]:
    """Legacy: one Flean Picks subcategory; up to 6 products (``needed=6``), independent of unified counts."""
    try:
        if collection_key not in FLEAN_PICKS_CATEGORIES:
            valid = list(FLEAN_PICKS_CATEGORIES.keys())
            return jsonify(_build_error_response(
                "COLLECTION_NOT_FOUND",
                f"Unknown collection '{collection_key}'. Valid: {valid}",
            )), 404

        cfg = FLEAN_PICKS_CATEGORIES[collection_key]
        products, _stats = _fetch_subcategory_products(cfg["es_paths"], user_filters=None, needed=6)

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
