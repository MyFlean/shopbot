# shopping_bot/routes/home_page.py
"""
Home Page API - Flutter App Home Screen Endpoints

This module provides API endpoints for the Flutter app's home page:
1. GET /api/v1/home/banners - Promotional banners/ads carousel
2. GET /api/v1/home/categories - Product categories (4 by default, all with ?all=true)
3. GET /api/v1/home/best-selling - Best selling products (category-path score based)
4. GET|POST /api/v1/home/curated - Legacy home curated strip (up to 8 from ES)
5. GET|POST /api/v1/home/curated/all - Legacy See All (up to 12 per collection from ES)
6. GET|POST /api/v1/home/flean-picks - Flean Picks collections (all or source-specific)
7. GET /api/v1/home/flean-picks/<collection_key> - Single Flean Picks collection (legacy)
8. GET /api/v1/home/why-flean - Value proposition cards
9. GET /api/v1/home/collaborations - Partner brand names
10. POST /api/v1/home/unified - Unified endpoint returning all sections in one response
11. GET /api/v1/home/unified - Unified endpoint (GET equivalent with empty body)
12. GET /api/v1/home/health - Health check endpoint
13. POST /api/v1/home/refresh - Clear cache and reload data
14. POST /api/v1/home/reload - Alias for refresh endpoint

Products are fetched from Elasticsearch (best-selling by category paths, curated by configured strategy).
Other data is loaded from JSON files in shopping_bot/data/home/

"""

from __future__ import annotations

import json
import logging
import os
import requests
import time
import traceback

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher, transform_to_product_card
from ..utils.pincode_mapping import (
    PincodeMappingError,
    is_placeholder_pincode,
    resolve_canonical_pincode,
)
from .product_api import _validate_filters

log = logging.getLogger(__name__)
bp = Blueprint("home_page", __name__)

# ============================================================================
# Flean Picks – subcategory-to-ES-path mapping and base filters
# ============================================================================

FLEAN_PICKS_CATEGORIES = {
    "high_protein_snacks": {
        "name": "High Protein Snacks",
        "image_url": "https://img.flean.ai/assets/Categories-Subcategories/Flean-Picks/High%20Protein%20Snacks.png",
        "es_paths": [
            "f_and_b/food/light_bites/energy_bars",
            "f_and_b/food/light_bites/dry_fruit_and_nut_snacks",
        ],
    },
    "no_guilt_spreads": {
        "name": "No Guilt Spreads",
        "image_url": "https://img.flean.ai/assets/Categories-Subcategories/Flean-Picks/No%20Guilt%20Spreads.png",
        "es_paths": [
            "f_and_b/food/spreads_and_condiments/peanut_butter",
            "f_and_b/food/spreads_and_condiments/honey_and_spreads",
        ],
    },
    "powerpacked_breakfast": {
        "name": "Powerpacked Breakfast",
        "image_url": "https://img.flean.ai/assets/Categories-Subcategories/Flean-Picks/Power-packed%20Breakfast.png",
        "es_paths": [
            "f_and_b/food/breakfast_essentials/muesli_and_oats",
            "f_and_b/food/breakfast_essentials/dates_and_seeds",
        ],
    },
    "no_guilt_munchies": {
        "name": "No Guilt Munchies",
        "image_url": "https://img.flean.ai/assets/Categories-Subcategories/Flean-Picks/No%20Guilt%20Munchies.png",
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
BEST_SELLING_FETCH_BUFFER = 13
SUPPLEMENTS_CATEGORY_PATHS: List[str] = [
    "f_and_b/supplements/performance/creatine",
    "f_and_b/supplements/amino_acids/bcaa",
    "f_and_b/supplements/protein/plant_protein",
    "f_and_b/supplements/protein/whey_isolate",
    "f_and_b/supplements/protein/whey_concentrate",
    "f_and_b/supplements/protein/whey_hydro",
]
SUPPLEMENTS_PER_CATEGORY = 1
SUPPLEMENTS_TOTAL_PRODUCTS = 6
SUPPLEMENTS_FETCH_BUFFER = 9
FLEAN_PICKS_HOME_FETCH_PER_SUBCATEGORY = 12
FLEAN_PICKS_SEE_ALL_FETCH_PER_SUBCATEGORY = 24

HOME_VALIDATION_FILTER_ENABLED = True
HOME_VALIDATION_FAIL_OPEN = True
HOME_VALIDATION_ALLOWED_PINCODES = {"201303", "201304", "201305"}

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


def _get_validation_cache_redis_client() -> Any:
    """Return Redis client for validation cache lookup when available."""
    try:
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        if ctx_mgr is None and "_get_or_init_redis" in current_app.extensions:
            ctx_mgr = current_app.extensions["_get_or_init_redis"]()
        return getattr(ctx_mgr, "redis", None) if ctx_mgr else None
    except Exception:
        return None


def _resolve_request_pincode() -> Optional[str]:
    """Resolve pincode from request context (query/header/body)."""
    candidates: List[Optional[str]] = [
        request.args.get("pincode"),
        request.headers.get("X-Pincode"),
        request.headers.get("x-pincode"),
    ]
    if request.method in {"POST", "PUT", "PATCH"}:
        body = request.get_json(force=True, silent=True) or {}
        if isinstance(body, dict):
            candidates.extend(
                [
                    body.get("pincode"),
                    body.get("postal_code"),
                    body.get("zip_code"),
                ]
            )
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _resolve_canonical_request_pincode() -> Optional[str]:
    request_pincode = _resolve_request_pincode()
    if is_placeholder_pincode(request_pincode):
        return None
    canonical_pincode = resolve_canonical_pincode(request_pincode)
    log.info(
        "PINCODE_CANONICAL_RESOLVED | request_pincode=%s | canonical_pincode=%s",
        request_pincode,
        canonical_pincode,
    )
    return canonical_pincode


def _is_validation_filter_active_for_pincode(pincode: Optional[str]) -> bool:
    if not HOME_VALIDATION_FILTER_ENABLED:
        return False
    if is_placeholder_pincode(pincode):
        return False
    if not pincode:
        return False
    if HOME_VALIDATION_ALLOWED_PINCODES and pincode not in HOME_VALIDATION_ALLOWED_PINCODES:
        return False
    return True


def _derive_is_available_from_validation_entry(entry: Dict[str, Any]) -> Optional[bool]:
    """Map cached validation entry to availability boolean."""
    status = entry.get("status")
    payload = entry.get("payload")
    if status == "success" and isinstance(payload, dict):
        category_group = payload.get("category_group")
        stock_message = (payload.get("stock_message", "") or "").lower()
        return True if category_group == "meals" else "out of stock" not in stock_message
    if status == "failed":
        return False
    return None


def _filter_cards_with_validation_cache(
    cards: List[Dict[str, Any]],
    pincode: Optional[str],
    section: str,
    subcategory_key: str,
    target_count: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Filter cards by Redis validation cache with optional validated-first backfill."""
    if not cards:
        return cards
    if not _is_validation_filter_active_for_pincode(pincode):
        return cards

    redis_client = _get_validation_cache_redis_client()
    if not redis_client:
        return cards if HOME_VALIDATION_FAIL_OPEN else []

    validated_available: List[Dict[str, Any]] = []
    uncached_or_unknown: List[Dict[str, Any]] = []
    excluded_unavailable = 0
    for card in cards:
        product_id = str(card.get("id") or "").strip()
        if not product_id:
            continue
        cache_key = f"product_val:{pincode}:{product_id}"
        try:
            raw = redis_client.get(cache_key)
        except Exception as exc:
            log.warning(
                "HOME_VALIDATION_CACHE_READ_FAILED | section=%s | subcategory=%s | pincode=%s | key=%s | error=%s",
                section,
                subcategory_key,
                pincode,
                cache_key,
                exc,
            )
            return cards if HOME_VALIDATION_FAIL_OPEN else []
        if not raw:
            if target_count is not None:
                uncached_or_unknown.append(card)
            continue

        try:
            entry = raw if isinstance(raw, dict) else json.loads(raw)
        except Exception:
            if target_count is not None:
                uncached_or_unknown.append(card)
            continue
        if not isinstance(entry, dict):
            if target_count is not None:
                uncached_or_unknown.append(card)
            continue

        available = _derive_is_available_from_validation_entry(entry)
        if available is True:
            validated_available.append(card)
        elif available is False:
            excluded_unavailable += 1
        elif target_count is not None:
            uncached_or_unknown.append(card)

    if target_count is not None:
        target = max(0, int(target_count))
        selected = list(validated_available)
        backfilled_unknown = 0
        if len(selected) < target:
            for card in uncached_or_unknown:
                if len(selected) >= target:
                    break
                selected.append(card)
                backfilled_unknown += 1
        log.info(
            "HOME_VALIDATION_FILTER_APPLIED | section=%s | subcategory=%s | pincode=%s | before=%s | after=%s | validated_available=%s | excluded_unavailable=%s | backfilled_unknown=%s | target_count=%s",
            section,
            subcategory_key,
            pincode,
            len(cards),
            len(selected),
            len(validated_available),
            excluded_unavailable,
            backfilled_unknown,
            target,
        )
        return selected

    if validated_available:
        log.info(
            "HOME_VALIDATION_FILTER_APPLIED | section=%s | subcategory=%s | pincode=%s | before=%s | after=%s | validated_available=%s | excluded_unavailable=%s | backfilled_unknown=%s | target_count=%s",
            section,
            subcategory_key,
            pincode,
            len(cards),
            len(validated_available),
            len(validated_available),
            excluded_unavailable,
            0,
            None,
        )
        return validated_available

    # Requirement: if no products satisfy validation, keep current behavior.
    log.info(
        "HOME_VALIDATION_FILTER_FALLBACK | section=%s | subcategory=%s | pincode=%s | before=%s | validated_available=%s | excluded_unavailable=%s | backfilled_unknown=%s | reason=empty_after_filter",
        section,
        subcategory_key,
        pincode,
        len(cards),
        len(validated_available),
        excluded_unavailable,
        0,
    )
    return cards


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


def _is_validation_candidate_eligible(product_src: Dict[str, Any]) -> bool:
    """Candidate eligibility for ecom warm flow."""
    visibility = str(product_src.get("visibility", "") or "").strip().lower()
    if visibility != "visible":
        return False
    # adjusted_score is on a 0-100 scale (8.0 badge ~= 80 adjusted_score)
    return _get_adjusted_score(product_src) >= 80.0


def _get_card_flean_sort_key(card: Dict[str, Any]) -> tuple[float, float]:
    """
    Sort key for product cards by Flean quality.
    Primary: badge score (10, 9, 8...); Secondary: percentile.
    """
    score_value = -1.0
    percentile_value = -1.0

    try:
        score = card.get("flean_score")
        if score is not None:
            score_value = float(score)
    except (TypeError, ValueError):
        pass

    try:
        percentile = card.get("flean_percentile")
        if percentile is not None:
            percentile_value = float(percentile)
    except (TypeError, ValueError):
        pass

    return (score_value, percentile_value)


def _get_best_selling_data(effective_pincode: Optional[str] = None) -> Dict[str, Any]:
    """Best-selling from fixed category paths ranked by flean_score.adjusted_score."""
    fetcher = get_es_fetcher()
    selected_products: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()
    backfill_candidates: List[tuple[float, Dict[str, Any]]] = []

    force_legacy = (os.getenv("BEST_SELLING_FORCE_LEGACY") or "").strip().lower() in ("1", "true", "yes", "on")

    # Prefer a single ES request (terms + top_hits aggregation) when mapping supports it.
    # Fallback to the legacy per-path query loop if aggregation isn't available or is forced off.
    agg_results: Optional[Dict[str, List[Dict[str, Any]]]] = None
    mode = "legacy"
    es_fetch_ms: Optional[float] = None

    if not force_legacy:
        _t0 = time.perf_counter()
        try:
            agg_results = fetcher.best_selling_by_category_paths_agg(
                paths=BEST_SELLING_CATEGORY_PATHS,
                per_category=BEST_SELLING_PER_CATEGORY,
                fetch_per_category=BEST_SELLING_PER_CATEGORY + BEST_SELLING_FETCH_BUFFER,
                filters=None,
                exclude_ids=None,
            )
            mode = "agg"
        except Exception:
            agg_results = None
            mode = "legacy"
        finally:
            es_fetch_ms = (time.perf_counter() - _t0) * 1000.0
    else:
        mode = "legacy_forced"

    for path in BEST_SELLING_CATEGORY_PATHS:
        if agg_results is not None and path in agg_results:
            raw_products = agg_results.get(path) or []
        else:
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
        cards_before_validation = [card for _, card in scored_cards]
        cards_after_validation = _filter_cards_with_validation_cache(
            cards_before_validation,
            effective_pincode,
            section="best_selling",
            subcategory_key=path,
        )
        if len(cards_after_validation) != len(cards_before_validation):
            score_by_id = {card.get("id"): score for score, card in scored_cards}
            scored_cards = [
                (float(score_by_id.get(card.get("id"), -1.0)), card)
                for card in cards_after_validation
                if card.get("id")
            ]
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

    # Final response is consistently ordered by Flean score descending.
    selected_products.sort(key=_get_card_flean_sort_key, reverse=True)

    try:
        log.info(
            "HOME_BEST_SELLING_FETCH",
            extra={
                "mode": mode,
                "force_legacy": force_legacy,
                "es_fetch_ms": round(es_fetch_ms, 2) if es_fetch_ms is not None else None,
                "paths": BEST_SELLING_CATEGORY_PATHS,
                "per_category": BEST_SELLING_PER_CATEGORY,
                "fetch_per_category": BEST_SELLING_PER_CATEGORY + BEST_SELLING_FETCH_BUFFER,
                "returned": min(len(selected_products), BEST_SELLING_TOTAL_PRODUCTS),
            },
        )
    except Exception:
        pass

    return {
        "products": selected_products[:BEST_SELLING_TOTAL_PRODUCTS],
        "section_title": "Best Selling",
    }


def _get_supplements_data(effective_pincode: Optional[str] = None) -> Dict[str, Any]:
    """Supplements from fixed category paths ranked by flean_score.adjusted_score."""
    fetcher = get_es_fetcher()
    selected_products: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()
    backfill_candidates: List[tuple[float, Dict[str, Any]]] = []

    force_legacy = (os.getenv("SUPPLEMENTS_FORCE_LEGACY") or "").strip().lower() in ("1", "true", "yes", "on")

    # Prefer a single ES request (terms + top_hits aggregation) when mapping supports it.
    # Fallback to the legacy per-path query loop if aggregation isn't available or is forced off.
    agg_results: Optional[Dict[str, List[Dict[str, Any]]]] = None
    mode = "legacy"
    es_fetch_ms: Optional[float] = None

    if not force_legacy:
        _t0 = time.perf_counter()
        try:
            agg_results = fetcher.best_selling_by_category_paths_agg(
                paths=SUPPLEMENTS_CATEGORY_PATHS,
                per_category=SUPPLEMENTS_PER_CATEGORY,
                fetch_per_category=SUPPLEMENTS_PER_CATEGORY + SUPPLEMENTS_FETCH_BUFFER,
                filters=None,
                exclude_ids=None,
            )
            mode = "agg"
        except Exception:
            agg_results = None
            mode = "legacy"
        finally:
            es_fetch_ms = (time.perf_counter() - _t0) * 1000.0
    else:
        mode = "legacy_forced"

    for path in SUPPLEMENTS_CATEGORY_PATHS:
        if agg_results is not None and path in agg_results:
            raw_products = agg_results.get(path) or []
        else:
            raw_products = fetcher.search_by_category_paths(
                paths=[path],
                filters=None,
                size=SUPPLEMENTS_PER_CATEGORY + SUPPLEMENTS_FETCH_BUFFER,
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
        cards_before_validation = [card for _, card in scored_cards]
        cards_after_validation = _filter_cards_with_validation_cache(
            cards_before_validation,
            effective_pincode,
            section="supplements",
            subcategory_key=path,
        )
        if len(cards_after_validation) != len(cards_before_validation):
            score_by_id = {card.get("id"): score for score, card in scored_cards}
            scored_cards = [
                (float(score_by_id.get(card.get("id"), -1.0)), card)
                for card in cards_after_validation
                if card.get("id")
            ]
            scored_cards.sort(key=lambda item: item[0], reverse=True)

        category_count = 0
        for score, card in scored_cards:
            product_id = card["id"]
            if product_id in selected_ids:
                continue
            if category_count < SUPPLEMENTS_PER_CATEGORY:
                selected_products.append(card)
                selected_ids.add(product_id)
                category_count += 1
            else:
                backfill_candidates.append((score, card))

    if len(selected_products) < SUPPLEMENTS_TOTAL_PRODUCTS:
        backfill_candidates.sort(key=lambda item: item[0], reverse=True)
        for _, card in backfill_candidates:
            product_id = card["id"]
            if product_id in selected_ids:
                continue
            selected_products.append(card)
            selected_ids.add(product_id)
            if len(selected_products) >= SUPPLEMENTS_TOTAL_PRODUCTS:
                break

    # Final response is consistently ordered by Flean score descending.
    selected_products.sort(key=_get_card_flean_sort_key, reverse=True)

    try:
        log.info(
            "HOME_SUPPLEMENTS_FETCH",
            extra={
                "mode": mode,
                "force_legacy": force_legacy,
                "es_fetch_ms": round(es_fetch_ms, 2) if es_fetch_ms is not None else None,
                "paths": SUPPLEMENTS_CATEGORY_PATHS,
                "per_category": SUPPLEMENTS_PER_CATEGORY,
                "fetch_per_category": SUPPLEMENTS_PER_CATEGORY + SUPPLEMENTS_FETCH_BUFFER,
                "returned": min(len(selected_products), SUPPLEMENTS_TOTAL_PRODUCTS),
            },
        )
    except Exception:
        pass

    return {
        "products": selected_products[:SUPPLEMENTS_TOTAL_PRODUCTS],
        "section_title": "Supplements",
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


def _legacy_unified_flean_picks_fetch(
    source: str,
    user_filters: Optional[Dict[str, Any]],
    needed: int,
) -> tuple[
    Optional[List[Dict[str, Any]]],
    Optional[List[Dict[str, Any]]],
    Dict[str, Any],
    Dict[str, int],
    Dict[str, List[Dict[str, Any]]],
]:
    """Sequential ES path: one ``_fetch_subcategory_products`` per Flean Picks bucket."""
    per_subcategory: Dict[str, Any] = {}
    total_tier_counts = {"tier1": 0, "tier2": 0, "tier3": 0}
    products_by_key: Dict[str, List[Dict[str, Any]]] = {}
    if source == "home":
        products: List[Dict[str, Any]] = []
        for key, cfg in FLEAN_PICKS_CATEGORIES.items():
            sub_products, stats = _fetch_subcategory_products(
                es_paths=cfg["es_paths"],
                user_filters=user_filters,
                needed=needed,
            )
            products_by_key[key] = sub_products
            products.extend(sub_products)
            per_subcategory[key] = {
                "requested_count": stats["requested_count"],
                "collected_count": stats["collected_count"],
                "tier_counts": stats["tier_counts"],
                "used_fallback": stats["used_fallback"],
            }
            for tier_name, count in stats["tier_counts"].items():
                total_tier_counts[tier_name] += int(count)
        return products, None, per_subcategory, total_tier_counts, products_by_key

    collections: List[Dict[str, Any]] = []
    for key, cfg in FLEAN_PICKS_CATEGORIES.items():
        sub_products, stats = _fetch_subcategory_products(
            es_paths=cfg["es_paths"],
            user_filters=user_filters,
            needed=needed,
        )
        products_by_key[key] = sub_products
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
    return None, collections, per_subcategory, total_tier_counts, products_by_key


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

    # Keep per-subcategory response ordered by Flean score descending.
    collected.sort(key=_get_card_flean_sort_key, reverse=True)
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


def _fetch_full_catalog_validation_candidates() -> Dict[str, Any]:
    """Fetch all candidate docs in one ES request (no pagination)."""
    fetcher = get_es_fetcher()
    query_body: Dict[str, Any] = {
        "bool": {
            "filter": [
                {
                    "bool": {
                        "should": [
                            {"term": {"visibility": "visible"}},
                            {"term": {"visibility.keyword": "visible"}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
                {
                    "bool": {
                        "should": [
                            {"range": {"flean_score.adjusted_score_label": {"gte": 8}}},
                            {"range": {"flean_score.adjusted_score": {"gte": 80}}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
            ]
        }
    }

    count_endpoint = f"{fetcher.base_url}/{fetcher.index}/_count"
    count_response = requests.post(
        count_endpoint,
        json={"query": query_body},
        timeout=20,
        **fetcher._request_kwargs(),
    )
    count_response.raise_for_status()
    count_payload = count_response.json() or {}
    total_catalog_hits = int(count_payload.get("count", 0) or 0)
    if total_catalog_hits <= 0:
        return {"docs": [], "total_catalog_hits": 0}

    body: Dict[str, Any] = {
        "size": total_catalog_hits,
        "track_total_hits": True,
        "_source": {
            "includes": [
                "id",
                "visibility",
                "flean_score.adjusted_score",
                "flean_score.adjusted_score_label",
            ]
        },
        "query": query_body,
        "sort": [
            {"flean_score.adjusted_score": {"order": "desc", "missing": "_last"}},
        ],
    }
    search_endpoint = f"{fetcher.base_url}/{fetcher.index}/_search"
    response = requests.post(
        search_endpoint,
        json=body,
        timeout=20,
        **fetcher._request_kwargs(),
    )
    response.raise_for_status()
    payload = response.json() or {}
    hits = ((payload.get("hits") or {}).get("hits") or [])
    docs = [hit.get("_source") for hit in hits if isinstance(hit, dict) and hit.get("_source")]

    return {
        "docs": docs,
        "total_catalog_hits": total_catalog_hits,
    }


def _build_validation_candidates_payload() -> Dict[str, Any]:
    """Build full-catalog validation candidates in a single group."""
    requested_at = datetime.utcnow().isoformat() + "Z"
    fetched = _fetch_full_catalog_validation_candidates()
    docs = fetched.get("docs", []) if isinstance(fetched, dict) else []
    total_catalog_hits = int((fetched.get("total_catalog_hits", 0) if isinstance(fetched, dict) else 0) or 0)

    seen_ids: set[str] = set()
    product_ids: List[str] = []
    inspected_total = len(docs)

    for product in docs:
        if not isinstance(product, dict):
            continue
        if not _is_validation_candidate_eligible(product):
            continue
        product_id = str(product.get("id", "")).strip()
        if not product_id or product_id in seen_ids:
            continue
        seen_ids.add(product_id)
        product_ids.append(product_id)

    candidates = []
    if product_ids:
        candidates.append(
            {
                "section": "full_catalog",
                "subcategory_key": "all_products",
                "es_paths": [],
                "product_ids": product_ids,
                "inspected_count": inspected_total,
                "eligible_count": len(product_ids),
            }
        )

    return {
        "version": "v2",
        "requested_at": requested_at,
        "candidate_groups": len(candidates),
        "is_paginated": False,
        "total_catalog_hits": total_catalog_hits,
        "inspected_total": inspected_total,
        "eligible_total": len(product_ids),
        "truncated": total_catalog_hits > inspected_total,
        "candidates": candidates,
    }


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


@bp.route("/api/v1/home/validation-candidates", methods=["GET"])
def get_validation_candidates() -> tuple[Dict[str, Any], int]:
    """
    Internal endpoint for ecom cache warm jobs.

    Returns all full-catalog product IDs for visible products with flean score >= 8
    in a single candidate group (no pagination/batching).
    """
    try:
        payload = _build_validation_candidates_payload()
        total_candidates = sum(len(item.get("product_ids", [])) for item in payload.get("candidates", []))
        log.info(
            "HOME_VALIDATION_CANDIDATES | groups=%s | total_products=%s | inspected_total=%s | eligible_total=%s | truncated=%s",
            len(payload.get("candidates", [])),
            total_candidates,
            payload.get("inspected_total", 0),
            payload.get("eligible_total", 0),
            payload.get("truncated", False),
        )
        return jsonify(_build_success_response(payload)), 200
    except Exception as exc:
        log.error("HOME_VALIDATION_CANDIDATES_ERROR | error=%s", exc, exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to build validation candidates")), 500


@bp.route("/api/v1/home/best-selling", methods=["GET"])
def get_best_selling() -> tuple[Dict[str, Any], int]:
    """
    Get best-selling products for the home page.

    Fetches products from fixed category paths in Elasticsearch, picks top 2
    per category by flean_score.adjusted_score, and backfills to return up to 6.
    """
    try:
        effective_pincode = _resolve_canonical_request_pincode()
        result = _get_best_selling_data(effective_pincode=effective_pincode)
        log.info(f"HOME_BEST_SELLING | returned={len(result.get('products', []))}")
        return jsonify(_build_success_response(result)), 200
    except PincodeMappingError as exc:
        log.warning("HOME_BEST_SELLING_PINCODE_ERROR | error=%s", exc)
        return jsonify(_build_error_response("PINCODE_MAPPING_ERROR", str(exc))), 400
    except Exception as e:
        log.error(f"HOME_BEST_SELLING_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load best-selling products")), 500


@bp.route("/api/v1/home/supplements", methods=["GET"])
def get_supplements() -> tuple[Dict[str, Any], int]:
    """
    Get supplement products for the home page.

    Fetches products from fixed supplement category paths in Elasticsearch, picks
    top 1 per category by flean_score.adjusted_score, and backfills to return up to 6.
    """
    try:
        effective_pincode = _resolve_canonical_request_pincode()
        result = _get_supplements_data(effective_pincode=effective_pincode)
        log.info(f"HOME_SUPPLEMENTS | returned={len(result.get('products', []))}")
        return jsonify(_build_success_response(result)), 200
    except PincodeMappingError as exc:
        log.warning("HOME_SUPPLEMENTS_PINCODE_ERROR | error=%s", exc)
        return jsonify(_build_error_response("PINCODE_MAPPING_ERROR", str(exc))), 400
    except Exception as e:
        log.error(f"HOME_SUPPLEMENTS_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load supplements")), 500


@bp.route("/api/v1/home/curated", methods=["GET", "POST"])
def get_curated_home() -> tuple[Dict[str, Any], int]:
    """Legacy: home curated strip. Delegates to unified flean picks (``source=home``, up to 8 products)."""
    try:
        filters = _extract_curate_filters()
        effective_pincode = _resolve_canonical_request_pincode()
        result = _unified_flean_picks_logic("home", filters, effective_pincode=effective_pincode)
        wrapped = {
            "products": result.get("products", []),
            "section_title": "Curated For You",
            "has_more": True,
            "total_in_pool": 4,
        }
        log.info(f"HOME_CURATED_LEGACY | returned={len(wrapped['products'])}")
        return jsonify(_build_success_response(wrapped)), 200
    except PincodeMappingError as exc:
        log.warning("HOME_CURATED_PINCODE_ERROR | error=%s", exc)
        return jsonify(_build_error_response("PINCODE_MAPPING_ERROR", str(exc))), 400
    except Exception as e:
        log.error(f"HOME_CURATED_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load curated products")), 500


@bp.route("/api/v1/home/curated/all", methods=["GET", "POST"])
def get_curated_all() -> tuple[Dict[str, Any], int]:
    """Legacy: See All curated products. Delegates to unified flean picks (``see_all``: 4 collections × up to 12)."""
    try:
        filters = _extract_curate_filters()
        effective_pincode = _resolve_canonical_request_pincode()
        result = _unified_flean_picks_logic("see_all", filters, effective_pincode=effective_pincode)
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
    except PincodeMappingError as exc:
        log.warning("HOME_CURATED_ALL_PINCODE_ERROR | error=%s", exc)
        return jsonify(_build_error_response("PINCODE_MAPPING_ERROR", str(exc))), 400
    except Exception as e:
        log.error(f"HOME_CURATED_ALL_ERROR | error={e}", exc_info=True)
        return jsonify(_build_error_response("INTERNAL_ERROR", "Failed to load curated products")), 500


# ============================================================================
# Unified Flean Picks API  (replaces curated + flean-picks endpoints)
# ============================================================================

def _unified_flean_picks_logic(
    source: str,
    user_filters: Optional[Dict[str, Any]],
    effective_pincode: Optional[str] = None,
) -> Dict[str, Any]:
    """Core logic shared by the new unified endpoint and legacy wrappers.

    source == "home":
        Flat ``products`` list: up to 8 items (2 per Flean Picks subcategory),
        each bucket ordered by flean percentile descending in the ES query.
    source != "home" (e.g. ``see_all``):
        ``collections`` with 4 subcategories, up to 12 products each.
    Actual counts can be lower if Elasticsearch returns fewer matches after
    3-tier fallback (tier1 all filters -> tier2 remove carbs -> tier3 remove fat).

    When ``FLEAN_PICKS_FORCE_LEGACY`` is unset/false, prefers one ES aggregation per
    relaxation tier (up to 3 calls for all buckets) via ``flean_picks_by_subcategories_agg``,
    then supplements any short bucket with the legacy per-bucket search.
    """
    filters_applied = _merge_filters(BASE_PERSONALIZATION_FILTERS, user_filters)
    no_match_message = "No products matched your selected filters. Try relaxing your filters."

    force_legacy = (os.getenv("FLEAN_PICKS_FORCE_LEGACY") or "").strip().lower() in ("1", "true", "yes", "on")
    needed = 2 if source == "home" else 12
    fetch_needed = (
        FLEAN_PICKS_HOME_FETCH_PER_SUBCATEGORY
        if source == "home"
        else FLEAN_PICKS_SEE_ALL_FETCH_PER_SUBCATEGORY
    )
    requested_total = len(FLEAN_PICKS_CATEGORIES) * needed

    def _apply_validation_for_collected(collected_map: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for key, products in collected_map.items():
            out[key] = _filter_cards_with_validation_cache(
                products,
                effective_pincode,
                section="flean_picks",
                subcategory_key=key,
                target_count=needed,
            )
        return out

    def _sync_per_subcategory_collected_counts(
        per_subcategory_meta: Dict[str, Any],
        collected_map: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        for key, stats in per_subcategory_meta.items():
            if not isinstance(stats, dict):
                continue
            post_count = len(collected_map.get(key, []))
            pre_count = int(stats.get("collected_count", 0))
            stats["pre_validation_collected_count"] = pre_count
            stats["collected_count"] = post_count

    def _log_fetch(
        mode: str,
        es_calls: int,
        es_fetch_ms: float,
        used_legacy_supplement: bool,
        agg_rounds: int = 0,
    ) -> None:
        try:
            payload = {
                "event": "HOME_FLEAN_PICKS_FETCH",
                "source": source,
                "mode": mode,
                "force_legacy": force_legacy,
                "needed_per_subcategory": needed,
                "fetch_needed_per_subcategory": fetch_needed,
                "es_calls": es_calls,
                "es_fetch_ms": round(es_fetch_ms, 2),
                "used_legacy_supplement": used_legacy_supplement,
                "agg_rounds": agg_rounds,
            }
            # Use stdout so CloudWatch shows it reliably (same as other DEBUG: ES lines).
            print(f"DEBUG: HOME_FLEAN_PICKS_FETCH | {json.dumps(payload, default=str)}")
        except Exception:
            pass

    if force_legacy:
        _products_l, _collections_l, per_subcategory, total_tier_counts, products_by_key = _legacy_unified_flean_picks_fetch(
            source, user_filters, fetch_needed
        )
        _log_fetch("legacy_forced", 0, 0.0, False)
        products_by_key = _apply_validation_for_collected(products_by_key)
        _sync_per_subcategory_collected_counts(per_subcategory, products_by_key)
        if source == "home":
            products = []
            for key in FLEAN_PICKS_CATEGORIES:
                products.extend(products_by_key.get(key, [])[:needed])
            products.sort(key=_get_card_flean_sort_key, reverse=True)
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

        collections = []
        for key, cfg in FLEAN_PICKS_CATEGORIES.items():
            collections.append(
                {
                    "key": key,
                    "name": cfg["name"],
                    "image_url": cfg["image_url"],
                    "products": products_by_key.get(key, [])[:needed],
                }
            )
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

    # --- aggregation path (tiers), then legacy supplement for any short bucket ---
    fetcher = get_es_fetcher()
    fetch_per = min(fetch_needed, 50)
    collected: Dict[str, List[Dict[str, Any]]] = {k: [] for k in FLEAN_PICKS_CATEGORIES}
    collected_ids: set[str] = set()
    tier_by_key: Dict[str, Dict[str, int]] = {
        k: {"tier1": 0, "tier2": 0, "tier3": 0} for k in FLEAN_PICKS_CATEGORIES
    }
    tiers = _build_flean_hybrid_tier_filters(user_filters)
    used_filters_seen: List[Optional[Dict[str, Any]]] = []
    es_calls = 0
    es_fetch_ms = 0.0
    agg_rounds_ok = 0
    tier1_agg_failed = False

    for tier_name, tier_filters in tiers:
        if any(tier_filters == seen for seen in used_filters_seen):
            continue
        used_filters_seen.append(tier_filters)
        short_keys = [k for k in FLEAN_PICKS_CATEGORIES if len(collected[k]) < fetch_needed]
        if not short_keys:
            break
        submap = {k: FLEAN_PICKS_CATEGORIES[k]["es_paths"] for k in short_keys}
        t0 = time.perf_counter()
        raw_map = fetcher.flean_picks_by_subcategories_agg(
            subcategories=submap,
            per_subcategory=fetch_needed,
            fetch_per_subcategory=fetch_per,
            filters=tier_filters,
            exclude_ids=sorted(collected_ids) if collected_ids else None,
        )
        es_fetch_ms += (time.perf_counter() - t0) * 1000.0
        es_calls += 1
        if not raw_map:
            if tier_name == "tier1":
                tier1_agg_failed = True
            break
        agg_rounds_ok += 1
        for key in short_keys:
            for src in raw_map.get(key) or []:
                card = transform_to_product_card(src)
                if not card:
                    continue
                pid = card.get("id")
                if not pid or pid in collected_ids:
                    continue
                if len(collected[key]) >= fetch_needed:
                    break
                collected[key].append(card)
                collected_ids.add(pid)
                tier_by_key[key][tier_name] += 1

    used_legacy_supplement = False
    if tier1_agg_failed:
        _products_l, _collections_l, per_subcategory, total_tier_counts, products_by_key = _legacy_unified_flean_picks_fetch(
            source, user_filters, fetch_needed
        )
        _log_fetch("agg_failed_fallback", es_calls, es_fetch_ms, False, agg_rounds=0)
        products_by_key = _apply_validation_for_collected(products_by_key)
        _sync_per_subcategory_collected_counts(per_subcategory, products_by_key)
        if source == "home":
            products = []
            for key in FLEAN_PICKS_CATEGORIES:
                products.extend(products_by_key.get(key, [])[:needed])
            products.sort(key=_get_card_flean_sort_key, reverse=True)
            fallback_used_count = total_tier_counts["tier2"] + total_tier_counts["tier3"]
            response_data = {
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

        collections = []
        for key, cfg in FLEAN_PICKS_CATEGORIES.items():
            collections.append(
                {
                    "key": key,
                    "name": cfg["name"],
                    "image_url": cfg["image_url"],
                    "products": products_by_key.get(key, [])[:needed],
                }
            )
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

    for key in FLEAN_PICKS_CATEGORIES:
        if len(collected[key]) >= fetch_needed:
            continue
        used_legacy_supplement = True
        before_len = len(collected[key])
        sub_products, stats = _fetch_subcategory_products(
            es_paths=FLEAN_PICKS_CATEGORIES[key]["es_paths"],
            user_filters=user_filters,
            needed=fetch_needed,
        )
        for card in sub_products:
            pid = card.get("id")
            if not pid or pid in collected_ids:
                continue
            if len(collected[key]) >= fetch_needed:
                break
            collected[key].append(card)
            collected_ids.add(pid)
        stc = stats.get("tier_counts") or {}
        if before_len == 0:
            tier_by_key[key] = {
                "tier1": int(stc.get("tier1", 0)),
                "tier2": int(stc.get("tier2", 0)),
                "tier3": int(stc.get("tier3", 0)),
            }
        else:
            for tn in ("tier1", "tier2", "tier3"):
                tier_by_key[key][tn] = int(tier_by_key[key].get(tn, 0)) + int(stc.get(tn, 0))

    per_subcategory = {}
    total_tier_counts = {"tier1": 0, "tier2": 0, "tier3": 0}
    for key in FLEAN_PICKS_CATEGORIES:
        bucket = collected[key]
        bucket.sort(key=_get_card_flean_sort_key, reverse=True)
        collected[key] = bucket[:fetch_needed]
        tc = tier_by_key[key]
        fallback_used = int(tc.get("tier2", 0)) + int(tc.get("tier3", 0)) > 0
        per_subcategory[key] = {
            "requested_count": needed,
            "collected_count": len(collected[key]),
            "tier_counts": dict(tc),
            "used_fallback": fallback_used,
        }
        for tn, cnt in tc.items():
            total_tier_counts[tn] += int(cnt)

    collected = _apply_validation_for_collected(collected)
    _sync_per_subcategory_collected_counts(per_subcategory, collected)

    mode = "agg_partial_fallback" if used_legacy_supplement else "agg"
    _log_fetch(mode, es_calls, es_fetch_ms, used_legacy_supplement, agg_rounds=agg_rounds_ok)

    if source == "home":
        products: List[Dict[str, Any]] = []
        for key in FLEAN_PICKS_CATEGORIES:
            products.extend(collected[key][:needed])
        products.sort(key=_get_card_flean_sort_key, reverse=True)
        fallback_used_count = total_tier_counts["tier2"] + total_tier_counts["tier3"]
        response_data = {
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

    collections: List[Dict[str, Any]] = []
    for key, cfg in FLEAN_PICKS_CATEGORIES.items():
        collections.append({
            "key": key,
            "name": cfg["name"],
            "image_url": cfg["image_url"],
            # Final response keeps the existing per-bucket contract (max 12).
            "products": collected[key][:needed],
        })
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
        Supported filter shape includes numeric ``nutrition`` sliders and
        flag-based ``nutrition_profiles`` (e.g. ``["high_protein", "low_sugar"]``).

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

        effective_pincode = _resolve_canonical_request_pincode()
        result = _unified_flean_picks_logic(source, user_filters, effective_pincode=effective_pincode)

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

    except PincodeMappingError as exc:
        log.warning("FLEAN_PICKS_PINCODE_ERROR | error=%s", exc)
        return jsonify(_build_error_response("PINCODE_MAPPING_ERROR", str(exc))), 400
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
        effective_pincode = _resolve_canonical_request_pincode()
        products, _stats = _fetch_subcategory_products(cfg["es_paths"], user_filters=None, needed=6)
        products = _filter_cards_with_validation_cache(
            products,
            effective_pincode,
            section="flean_picks",
            subcategory_key=collection_key,
            target_count=6,
        )

        log.info(f"FLEAN_PICKS_LEGACY | key={collection_key} | returned={len(products)}")
        return jsonify(_build_success_response({
            "key": collection_key,
            "name": cfg["name"],
            "products": products,
        })), 200
    except PincodeMappingError as exc:
        log.warning("FLEAN_PICKS_COLLECTION_PINCODE_ERROR | key=%s | error=%s", collection_key, exc)
        return jsonify(_build_error_response("PINCODE_MAPPING_ERROR", str(exc))), 400
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
