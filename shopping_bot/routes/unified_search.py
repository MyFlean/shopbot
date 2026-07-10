# shopping_bot/routes/unified_search.py
"""
Unified Search Endpoint

-----------------------
APIs:
  - GET|POST /rs/v1/search
  - GET|POST /rs/v1/search/suggest

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
  - size (int, 1..100, default 100)
  - sort_by or sort (alias)
      flean_score_desc (default), relevance, price_asc, price_desc,
      protein_desc, fiber_desc, fat_asc, flean_score_desc
      Catalogue aliases: sort=flean_score -> flean_score_desc,
                         sort=price       -> price_asc
  - filters:
      price_range, flean_score,
      preferences / ingredient_preferences (aliases),
      dietary / dietary_preferences (aliases),
      food_type,
      nutrition {protein, carbs, fat},
      nutrition_profiles [high_protein, high_fiber, low_carb, low_sugar, low_sodium, low_fat]
  - Top-level food_type also accepted (simple_search quirk); folded into filters.food_type
  - GET also accepts nutrition_profiles as a comma-separated query param

Requires at least one of query/subcategory/filters (same as /api/v1/products).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher, get_search_gateway, transform_to_product_card
from ..utils.pincode_mapping import try_resolve_canonical_pincode
from .product_api import (
    VALID_SORT_OPTIONS,
    _build_filters_from_query_args,
    _extract_lab_report_url,
    _error_response,
    _has_palm_oil_ingredient,
    _normalize_filter_aliases,
    _resolve_pdp_cta,
    _success_response,
    _validate_filters,
)

log = logging.getLogger(__name__)
bp = Blueprint("unified_search", __name__)
SUGGEST_ROUTE_ENABLED = os.getenv("SEARCH_SUGGEST_DISABLED", "").strip().lower() not in {"1", "true", "yes", "on"}
MAX_SUGGEST_BRANDS = 3
MAX_PRODUCTS_PER_BRAND = 3
V1_SUGGEST_SIZE = 8
V2_SUGGEST_SIZE = 100
DEFAULT_SEARCH_PINCODE = "201303"


def _search_engine() -> str:
    return os.getenv("SEARCH_ENGINE", "auto").strip().lower()


# ---------------------------------------------------------------------------
# Sort aliases (catalogue compatibility)
# ---------------------------------------------------------------------------

SORT_ALIASES = {
    "flean_score": "flean_score_desc",
    "price": "price_asc",
}

# ---------------------------------------------------------------------------
# V1 → V2 filter translation
# ---------------------------------------------------------------------------

_PRICE_RANGE_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "below_99":  (None,   99.0),
    "100_249":   (100.0, 249.0),
    "250_499":   (250.0, 499.0),
    "above_500": (500.0,  None),
}

_FLEAN_SCORE_TO_PERCENTILE: Dict[str, float] = {
    "10":     90.0,
    "9_plus": 70.0,
    "8_plus": 50.0,
    "7_plus": 30.0,
}


def _v1_filters_to_gw_params(vf: Dict[str, Any]) -> Dict[str, Any]:
    """Translate validated_filters to SearchFilters.from_dict()-compatible params."""
    out: Dict[str, Any] = {}

    pr = vf.get("price_range")
    if pr:
        bounds = _PRICE_RANGE_BOUNDS.get(str(pr))
        if bounds:
            pmin, pmax = bounds
            if pmin is not None:
                out["price_min"] = pmin
            if pmax is not None:
                out["price_max"] = pmax

    fs = vf.get("flean_score")
    if fs:
        pct = _FLEAN_SCORE_TO_PERCENTILE.get(str(fs))
        if pct is not None:
            out["min_flean_percentile"] = pct

    dietary = vf.get("dietary")
    if dietary:
        out["dietary_labels"] = dietary

    nutrition = vf.get("nutrition")
    if nutrition and isinstance(nutrition, dict):
        mfs: List[Dict[str, Any]] = []
        if nutrition.get("protein"):
            mfs.append({"nutrient": "protein_g", "operator": "gte", "value": float(nutrition["protein"])})
        if nutrition.get("fat"):
            mfs.append({"nutrient": "fat_g", "operator": "lte", "value": float(nutrition["fat"])})
        if nutrition.get("carbs"):
            mfs.append({"nutrient": "carbs_g", "operator": "lte", "value": float(nutrition["carbs"])})
        if mfs:
            out["macro_filters"] = mfs

    preferences = vf.get("preferences")
    if preferences:
        out["ingredient_tags"] = list(preferences)

    food_type = vf.get("food_type")
    if food_type:
        out["food_type"] = food_type

    nutrition_profiles = vf.get("nutrition_profiles")
    if nutrition_profiles:
        out["nutrition_profiles"] = list(nutrition_profiles)

    return out


def _resolve_sort(raw: Optional[str], has_query: bool = False) -> str:
    """Normalize sort input: query defaults + catalogue aliases -> canonical unified values."""
    if not raw:
        # Query flows should prioritize lexical relevance unless caller overrides sort.
        return "relevance" if has_query else "flean_score_desc"
    raw = str(raw).strip().lower()
    return SORT_ALIASES.get(raw, raw)


# ---------------------------------------------------------------------------
# Filter alias normalization (see product_api._normalize_filter_aliases)
# ---------------------------------------------------------------------------


def _resolve_request_pincode(body: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve request pincode from query/header/body inputs."""
    candidates: List[Any] = [
        request.args.get("pincode"),
        request.headers.get("X-Pincode"),
        request.headers.get("x-pincode"),
    ]
    if isinstance(body, dict):
        candidates.append(body.get("pincode"))

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def _resolve_effective_pincode(body: Optional[Dict[str, Any]] = None) -> str:
    """Canonical request pincode with default fallback for search flows."""
    request_pincode = _resolve_request_pincode(body)
    canonical = try_resolve_canonical_pincode(request_pincode)
    if canonical:
        return canonical
    return DEFAULT_SEARCH_PINCODE


def _to_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _derive_in_stock_from_availability(raw: Dict[str, Any], effective_pincode: str) -> bool:
    """
    Derive listing stock for search cards from availability.<pincode>.

    Rule: in_stock when any provider reports stock OR flean.quantity > 0.
    Falls back to visibility-based stock when no usable availability signal exists.
    """
    visibility = str(raw.get("visibility", "visible") or "visible").strip().lower()
    fallback_in_stock = visibility == "visible"

    availability = raw.get("availability")
    if not isinstance(availability, dict):
        return fallback_in_stock

    pincode_entry = availability.get(effective_pincode)
    if not isinstance(pincode_entry, dict):
        return fallback_in_stock

    has_signal = False
    provider_in_stock = False

    for provider in ("zepto", "blinkit"):
        provider_data = pincode_entry.get(provider)
        if not isinstance(provider_data, dict):
            continue
        in_stock_value = _to_bool(provider_data.get("in_stock"))
        if in_stock_value is None:
            continue
        has_signal = True
        provider_in_stock = provider_in_stock or in_stock_value

    flean_data = pincode_entry.get("flean")
    flean_in_stock = False
    if isinstance(flean_data, dict) and "quantity" in flean_data:
        has_signal = True
        try:
            flean_in_stock = float(flean_data.get("quantity") or 0) > 0
        except (TypeError, ValueError):
            flean_in_stock = False

    if has_signal:
        return provider_in_stock or flean_in_stock
    return fallback_in_stock


def _group_suggestions_by_brand(suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group ranked suggestions into brand buckets for the final API response."""
    grouped: Dict[str, Dict[str, Any]] = {}
    brand_order: List[str] = []
    seen_products: Dict[str, set[str]] = {}

    for item in suggestions or []:
        if not isinstance(item, dict):
            continue

        suggestion_type = str(item.get("type") or "").strip().lower()

        if suggestion_type == "brand":
            brand_name = " ".join(str(item.get("text") or item.get("brand") or "").split()).strip()
            if not brand_name:
                continue
            brand_key = brand_name.casefold()
            if brand_key not in grouped:
                grouped[brand_key] = {"brand": brand_name, "products": []}
                brand_order.append(brand_key)
                seen_products[brand_key] = set()
            continue

        if suggestion_type != "product":
            continue

        brand_name = " ".join(str(item.get("brand") or "").split()).strip()
        product_text = " ".join(str(item.get("text") or "").split()).strip()
        if not brand_name or not product_text:
            continue

        brand_key = brand_name.casefold()
        if brand_key not in grouped:
            grouped[brand_key] = {"brand": brand_name, "products": []}
            brand_order.append(brand_key)
            seen_products[brand_key] = set()

        product_key = product_text.lower()
        if product_key in seen_products[brand_key]:
            continue
        if len(grouped[brand_key]["products"]) >= MAX_PRODUCTS_PER_BRAND:
            continue
        seen_products[brand_key].add(product_key)
        grouped[brand_key]["products"].append({"text": product_text, "type": "product"})

    return [grouped[brand_key] for brand_key in brand_order[:MAX_SUGGEST_BRANDS]]


def _extract_suggest_query() -> Tuple[Optional[str], Optional[Tuple[Dict[str, Any], int]]]:
    """Parse and validate suggest query from GET/POST."""
    if request.method == "GET":
        query = (request.args.get("query") or "").strip()
        if not query:
            return None, _error_response("MISSING_PARAMETER", "'query' is required", 400)
        return query, None

    body = request.get_json(force=True, silent=True) or {}
    query = body.get("query")
    if query is None:
        query = ""
    if not isinstance(query, str):
        return None, _error_response("INVALID_QUERY", "'query' must be a string", 400)
    query = query.strip()
    if not query:
        return None, _error_response("MISSING_PARAMETER", "'query' is required", 400)
    return query, None


def _fetch_flat_suggestions(query: str, size: int, version: str) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[Dict[str, Any], int]]]:
    """Fetch flat suggestions from Elasticsearch fetcher."""
    try:
        fetcher = get_es_fetcher()
    except RuntimeError as exc:
        log.error("UNIFIED_SEARCH_SUGGEST_CONFIG_ERROR | version=%s | error=%s", version, exc, exc_info=True)
        return None, _error_response("INTERNAL_ERROR", str(exc), 500)

    try:
        result = fetcher.search_suggestions(
            query=query,
            size=size,
        )
    except TypeError as exc:
        if "unexpected keyword argument 'size'" not in str(exc):
            raise
        # Backward-compatible call shape for older mocks/callers.
        result = fetcher.search_suggestions(query=query)
    meta = result.get("meta", {}) or {}
    if meta.get("error"):
        log.error("UNIFIED_SEARCH_SUGGEST_ES_ERROR | version=%s | error=%s", version, meta.get("error"))
        return None, _error_response("SEARCH_ERROR", f"Suggestion search failed: {meta.get('error')}", 500)
    return result, None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@bp.route("/v1/search", methods=["GET", "POST"])
def unified_search() -> Tuple[Dict[str, Any], int]:
    """Unified search/browse/catalogue endpoint."""
    try:
        body: Dict[str, Any] = {}
        if request.method == "GET":
            query = (request.args.get("query") or "").strip() or None
            subcategory = (request.args.get("subcategory") or "").strip() or None

            try:
                page = max(0, int(request.args.get("page", 0)))
            except (TypeError, ValueError):
                page = 0
            try:
                size = max(1, min(int(request.args.get("size", 100)), 100))
            except (TypeError, ValueError):
                size = 20

            sort_raw = request.args.get("sort_by") or request.args.get("sort")

            raw_filters = _build_filters_from_query_args()
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
                size = max(1, min(int(body.get("size", 100)), 100))
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
        resolved_sort = _resolve_sort(sort_raw, has_query=bool(query))
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
        effective_pincode = _resolve_effective_pincode(body)

        log.info(
            f"UNIFIED_SEARCH_REQUEST | query={query} | subcategory={subcategory} | "
            f"page={page} | size={size} | sort={resolved_sort} | filters={validated_filters} "
            f"| effective_pincode={effective_pincode}"
        )

        result: Optional[Dict[str, Any]] = None

        if _search_engine() != "v1" and query:
            # Search V2 path
            try:
                gateway = get_search_gateway()
                gw_params: Dict[str, Any] = {
                    "q": query,
                    "size": size,
                    "offset": page * size,
                    "sort_by": resolved_sort,
                    "subcategory": subcategory,
                }
                gw_params.update(_v1_filters_to_gw_params(validated_filters or {}))
                gw_result = gateway.search(gw_params)
                gw_meta = gw_result.get("meta", {}) or {}
                gw_products = gw_result.get("products", [])
                returned = len(gw_products)
                result = {
                    "products": gw_products,
                    "meta": {
                        "total": gw_meta.get("total_hits", returned),
                        "page": page,
                        "size": size,
                        "total_pages": page + (2 if returned == size else 1),
                        "has_next": returned == size,
                        "has_prev": page > 0,
                        "query": query,
                        "subcategory": subcategory,
                        "sort_by": resolved_sort,
                        "filters_applied": validated_filters,
                        "took_ms": gw_meta.get("took_ms", 0),
                        "fuzzy_fallback_used": False,
                        "prefix_fallback_used": False,
                        "phonetic_used": False,
                        "engine": "v2",
                    },
                }
            except Exception as exc:
                if _search_engine() == "v2":
                    log.error("UNIFIED_SEARCH_V2_ERROR | error=%s", exc, exc_info=True)
                    return _error_response("INTERNAL_ERROR", str(exc), 500)
                log.warning("UNIFIED_SEARCH_V2_FALLBACK | error=%s", exc)

        if result is None:
            # Legacy V1 path
            try:
                fetcher = get_es_fetcher()
            except RuntimeError as exc:
                log.error("UNIFIED_SEARCH_CONFIG_ERROR | error=%s", exc, exc_info=True)
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
                    card_in_stock = _derive_in_stock_from_availability(raw, effective_pincode)
                    card["in_stock"] = card_in_stock
                    lab_report_url = _extract_lab_report_url(raw)
                    card["has_lab_report"] = bool(lab_report_url)
                    card["cta"] = _resolve_pdp_cta(
                        product_info={
                            "in_stock": card_in_stock,
                            "visibility": card.get("visibility"),
                        },
                        flean_badge={"score": card.get("flean_score")},
                        has_palm_oil=_has_palm_oil_ingredient(raw),
                    )
                    if raw.get("_score") is not None:
                        card["_score"] = raw.get("_score")
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


@bp.route("/v1/search/suggest", methods=["GET", "POST"])
def unified_search_suggest() -> Tuple[Dict[str, Any], int]:
    """Legacy flat autocomplete suggestions endpoint."""
    try:
        if not SUGGEST_ROUTE_ENABLED:
            return _error_response("FEATURE_DISABLED", "Search suggestions are disabled", 503)

        query, query_error = _extract_suggest_query()
        if query_error is not None:
            return query_error
        assert query is not None

        result, fetch_error = _fetch_flat_suggestions(query=query, size=V1_SUGGEST_SIZE, version="v1")
        if fetch_error is not None:
            return fetch_error
        assert result is not None

        flat_suggestions = result.get("suggestions", []) or []
        meta = result.get("meta", {}) or {}
        meta["returned"] = len(flat_suggestions)

        log.info(
            "UNIFIED_SEARCH_SUGGEST_COMPLETE | version=%s | query=%s | returned=%s | took_ms=%s | fallback=%s",
            "v1",
            query,
            len(flat_suggestions),
            meta.get("took_ms"),
            meta.get("fallback_used"),
        )
        return jsonify(_success_response({"suggestions": flat_suggestions}, meta=meta)), 200
    except Exception as exc:
        log.error("UNIFIED_SEARCH_SUGGEST_ERROR | version=%s | error=%s", "v1", exc, exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch suggestions", 500)


@bp.route("/v2/search/suggest", methods=["GET", "POST"])
def unified_search_suggest_v2() -> Tuple[Dict[str, Any], int]:
    """Grouped autocomplete suggestions endpoint."""
    try:
        if not SUGGEST_ROUTE_ENABLED:
            return _error_response("FEATURE_DISABLED", "Search suggestions are disabled", 503)

        query, query_error = _extract_suggest_query()
        if query_error is not None:
            return query_error
        assert query is not None

        result, fetch_error = _fetch_flat_suggestions(query=query, size=V2_SUGGEST_SIZE, version="v2")
        if fetch_error is not None:
            return fetch_error
        assert result is not None

        grouped_suggestions = _group_suggestions_by_brand(result.get("suggestions", []) or [])
        meta = result.get("meta", {}) or {}
        meta["returned"] = len(grouped_suggestions)

        log.info(
            "UNIFIED_SEARCH_SUGGEST_COMPLETE | version=%s | query=%s | returned=%s | took_ms=%s | fallback=%s",
            "v2",
            query,
            len(grouped_suggestions),
            meta.get("took_ms"),
            meta.get("fallback_used"),
        )
        return jsonify(_success_response({"suggestions": grouped_suggestions}, meta=meta)), 200
    except Exception as exc:
        log.error("UNIFIED_SEARCH_SUGGEST_ERROR | version=%s | error=%s", "v2", exc, exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch suggestions", 500)
