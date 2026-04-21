# shopping_bot/routes/unified_search.py
"""
Unified Search Endpoint
-----------------------
GET|POST /rs/v1/search

A functional superset of:
  - POST /rs/search
  - GET  /rs/api/v1/catalogue
  - GET|POST /rs/api/v1/products

Returns the wrapped `{success, data:{products}, meta}` shape used by
/rs/api/v1/products. All three legacy routes remain unchanged.

Accepted params (GET query or POST JSON body):
  - query (string, optional)
  - subcategory (string, optional)         ES path
  - page (int, default 0)
  - size (int, 1..100, default 20)
  - sort_by or sort (alias)
      relevance (default), price_asc, price_desc,
      protein_desc, fiber_desc, fat_asc, flean_score_desc
      Catalogue aliases: sort=flean_score -> flean_score_desc,
                         sort=price       -> price_asc
  - filters:
      price_range, flean_score,
      preferences / ingredient_preferences (aliases),
      dietary / dietary_preferences (aliases),
      food_type,
      nutrition {protein, carbs, fat}
  - Top-level food_type also accepted (simple_search quirk); folded into filters.food_type

Requires at least one of query/subcategory/filters (same as /api/v1/products).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher, transform_to_product_card
from .product_api import (
    VALID_SORT_OPTIONS,
    _error_response,
    _success_response,
    _validate_filters,
)

log = logging.getLogger(__name__)
bp = Blueprint("unified_search", __name__)


# ---------------------------------------------------------------------------
# Sort aliases (catalogue compatibility)
# ---------------------------------------------------------------------------

SORT_ALIASES = {
    "flean_score": "flean_score_desc",
    "price": "price_asc",
}


def _resolve_sort(raw: Optional[str]) -> str:
    """Normalize sort input: catalogue aliases -> canonical unified values."""
    if not raw:
        return "relevance"
    raw = str(raw).strip().lower()
    return SORT_ALIASES.get(raw, raw)


# ---------------------------------------------------------------------------
# Filter alias normalization
# ---------------------------------------------------------------------------

def _normalize_filter_aliases(filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Accept both simple_search-style keys (preferences, dietary) and
    product_api-style keys (ingredient_preferences, dietary_preferences) so
    the shared _validate_filters from product_api can handle the result.

    Returns a new dict (never mutates the caller's). Returns None if input is None.
    """
    if not filters:
        return None
    if not isinstance(filters, dict):
        return filters  # let _validate_filters surface the type error

    normalized: Dict[str, Any] = dict(filters)

    # preferences -> ingredient_preferences (product_api canonical key)
    if "preferences" in normalized and "ingredient_preferences" not in normalized:
        normalized["ingredient_preferences"] = normalized.pop("preferences")
    else:
        normalized.pop("preferences", None)

    # dietary -> dietary_preferences (product_api canonical key)
    if "dietary" in normalized and "dietary_preferences" not in normalized:
        normalized["dietary_preferences"] = normalized.pop("dietary")
    else:
        normalized.pop("dietary", None)

    return normalized


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@bp.route("/v1/search", methods=["GET", "POST"])
def unified_search() -> Tuple[Dict[str, Any], int]:
    """Unified search/browse/catalogue endpoint."""
    try:
        if request.method == "GET":
            query = (request.args.get("query") or "").strip() or None
            subcategory = (request.args.get("subcategory") or "").strip() or None

            try:
                page = max(0, int(request.args.get("page", 0)))
            except (TypeError, ValueError):
                page = 0
            try:
                size = max(1, min(int(request.args.get("size", 20)), 100))
            except (TypeError, ValueError):
                size = 20

            sort_raw = request.args.get("sort_by") or request.args.get("sort")

            raw_filters: Dict[str, Any] = {}
            if request.args.get("price_range"):
                raw_filters["price_range"] = request.args["price_range"]
            if request.args.get("flean_score"):
                raw_filters["flean_score"] = request.args["flean_score"]

            # preferences alias (product_api uses comma-separated list)
            prefs = request.args.get("preferences") or request.args.get("ingredient_preferences")
            if prefs:
                raw_filters["preferences"] = [p.strip() for p in prefs.split(",") if p.strip()]

            diet = request.args.get("dietary") or request.args.get("dietary_preferences")
            if diet:
                raw_filters["dietary"] = [d.strip() for d in diet.split(",") if d.strip()]

            if request.args.get("food_type"):
                raw_filters["food_type"] = request.args["food_type"]

            nutrition_params: Dict[str, Any] = {}
            for key in ("protein", "carbs", "fat"):
                v = request.args.get(key)
                if v is not None:
                    nutrition_params[key] = v
            if nutrition_params:
                raw_filters["nutrition"] = nutrition_params
        else:
            body = request.get_json(force=True, silent=True) or {}

            query = body.get("query")
            if query is not None:
                if not isinstance(query, str):
                    return _error_response("INVALID_QUERY", "'query' must be a string", 400)
                query = query.strip() or None

            subcategory = body.get("subcategory")
            if subcategory is not None:
                if not isinstance(subcategory, str):
                    return _error_response("INVALID_SUBCATEGORY", "'subcategory' must be a string", 400)
                subcategory = subcategory.strip() or None

            try:
                page = max(0, int(body.get("page", 0)))
            except (TypeError, ValueError):
                page = 0
            try:
                size = max(1, min(int(body.get("size", 20)), 100))
            except (TypeError, ValueError):
                size = 20

            sort_raw = body.get("sort_by") if body.get("sort_by") is not None else body.get("sort")

            raw_filters = body.get("filters") or {}
            if not isinstance(raw_filters, dict):
                return _error_response("INVALID_FILTERS", "'filters' must be an object", 400)

            # Top-level food_type (simple_search quirk) folds into filters
            top_food_type = body.get("food_type")
            if top_food_type and "food_type" not in raw_filters:
                raw_filters = dict(raw_filters)
                raw_filters["food_type"] = top_food_type

        # At least one selector is required
        if not query and not subcategory and not raw_filters:
            return _error_response(
                "MISSING_PARAMETER",
                "At least one of 'query', 'subcategory', or 'filters' must be provided",
                400,
            )

        # Resolve sort (with catalogue aliases) and validate
        resolved_sort = _resolve_sort(sort_raw)
        if resolved_sort not in VALID_SORT_OPTIONS:
            return _error_response(
                "INVALID_SORT",
                f"Invalid 'sort_by': '{sort_raw}'. Valid: {sorted(VALID_SORT_OPTIONS)}",
                400,
            )

        # Normalize filter key aliases, then validate via product_api validator
        normalized_filters = _normalize_filter_aliases(raw_filters if raw_filters else None)
        validated_filters, filter_error = _validate_filters(normalized_filters)
        if filter_error:
            return _error_response("INVALID_FILTERS", filter_error, 400)

        log.info(
            f"UNIFIED_SEARCH_REQUEST | query={query} | subcategory={subcategory} | "
            f"page={page} | size={size} | sort={resolved_sort} | filters={validated_filters}"
        )

        try:
            fetcher = get_es_fetcher()
        except RuntimeError as exc:
            # Misconfiguration (e.g. Elastic Cloud URL with IAM, missing API key)
            log.error(f"UNIFIED_SEARCH_CONFIG_ERROR | error={exc}", exc_info=True)
            return _error_response("INTERNAL_ERROR", str(exc), 500)

        result = fetcher.search_products_unified(
            query=query,
            subcategory=subcategory,
            page=page,
            size=size,
            sort_by=resolved_sort,
            filters=validated_filters,
        )

        meta = result.get("meta", {}) or {}
        if meta.get("error"):
            log.error(f"UNIFIED_SEARCH_ES_ERROR | error={meta.get('error')}")
            return _error_response("SEARCH_ERROR", f"Search failed: {meta.get('error')}", 500)

        # Ensure meta.sort_by echoes the resolved canonical value
        meta["sort_by"] = resolved_sort

        raw_products = result.get("products", [])
        product_cards: List[Dict[str, Any]] = []
        for raw in raw_products:
            if not raw:
                continue
            try:
                card = transform_to_product_card(raw)
                if card is not None:
                    product_cards.append(card)
            except Exception as e:
                log.warning(
                    "UNIFIED_SEARCH_PRODUCT_CARD_ERROR | id=%s | error=%s",
                    raw.get("id", "?"),
                    e,
                )

        log.info(
            f"UNIFIED_SEARCH_COMPLETE | query={query} | subcategory={subcategory} | "
            f"total={meta.get('total', 0)} | returned={len(product_cards)}"
        )

        return jsonify(_success_response({"products": product_cards}, meta=meta)), 200

    except Exception as exc:
        log.error(f"UNIFIED_SEARCH_ERROR | error={exc}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch products", 500)
