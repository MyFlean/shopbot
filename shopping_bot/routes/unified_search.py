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
  - goal (string|list, optional)
      Multi: ?goal=a,b or ?goal=a&goal=b (POST: string or array)
  - diet (string|list, optional)
      Multi: ?diet=a,b or ?diet=a&diet=b (POST: string or array)
  - department (string|list, optional)     category_hierarchies.segments[1]
      Multi: ?department=a,b or ?department=a&department=b (POST: string or array)
  - category (string|list, optional)       category_hierarchies.segments[2]
      Multi: ?category=a,b or repeated params (POST: string or array)
  - subcategory (string|list, optional)    category_hierarchies.segments[3]
      Multi: ?subcategory=a,b or repeated params (POST: string or array)
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

Requires at least one of query/goal/diet/department/category/subcategory/filters.
Single-value hierarchy without query uses exclusive browse; multi-value hierarchy
uses the V2 search/filter path (OR within level, AND across levels, same nested row).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher, transform_to_product_card
from search_v2.extension.category_browsing import (
    browse_by_category_segment,
    browse_by_department_segment,
    browse_by_subcategory_segment,
)
from search_v2.extension.search import search as v2_search
from search_v2.extension.shop_by_goal import (
    GoalConfigError,
    resolve_diet_selection,
    resolve_goal_selection,
)
from search_v2.retrieval.filters import SearchFilters
from ..utils.pincode_mapping import try_resolve_canonical_pincode
from .product_api import (
    VALID_SORT_OPTIONS,
    _build_filters_from_query_args,
    _enrich_listing_card,
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


def _normalize_hierarchy_values(raw_parts: List[Any], *, strip_path: bool = False) -> List[str]:
    """Dedupe/lower hierarchy tokens from comma-separated and repeated params."""
    out: List[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        if not isinstance(part, str):
            continue
        for token in part.split(","):
            normalized = token.strip().lower()
            if not normalized:
                continue
            if strip_path and "/" in normalized:
                normalized = normalized.rsplit("/", 1)[-1]
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
    return out


def _parse_hierarchy_query_param(name: str, *, strip_path: bool = False) -> List[str]:
    return _normalize_hierarchy_values(list(request.args.getlist(name)), strip_path=strip_path)


def _parse_hierarchy_body_param(
    value: Any,
    *,
    field_name: str,
    strip_path: bool = False,
) -> Tuple[Optional[List[str]], Optional[Tuple[str, int]]]:
    """Return (values, error). error is (message, status) when invalid."""
    if value is None:
        return [], None
    if isinstance(value, str):
        return _normalize_hierarchy_values([value], strip_path=strip_path), None
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            return None, (f"'{field_name}' must be a string or array of strings", 400)
        return _normalize_hierarchy_values(value, strip_path=strip_path), None
    return None, (f"'{field_name}' must be a string or array of strings", 400)


def _hierarchy_meta_value(values: List[str]) -> Any:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return list(values)


# ---------------------------------------------------------------------------
# V1 → V2 filter translation
# ---------------------------------------------------------------------------

_PRICE_RANGE_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "below_99":  (None,   99.0),
    "100_249":   (100.0, 249.0),
    "250_499":   (250.0, 499.0),
    "above_500": (500.0,  None),
}

_FLEAN_SCORE_TO_MIN_BADGE: Dict[str, float] = {
    "10": 10.0,
    "9_plus": 9.0,
    "8_plus": 8.0,
    "7_plus": 7.0,
}


def _v1_filters_to_gw_params(vf: Dict[str, Any]) -> Dict[str, Any]:
    """Translate validated_filters to SearchFilters.from_dict()-compatible params."""
    out: Dict[str, Any] = {}

    def _dynamic_bounds(price_key: str) -> Optional[Tuple[float, float]]:
        token = str(price_key or "").strip()
        if "_" not in token:
            return None
        left_s, right_s = token.split("_", 1)
        if not (left_s.isdigit() and right_s.isdigit()):
            return None
        left = int(left_s)
        right = int(right_s)
        if left < 0 or right < left:
            return None
        return float(left), float(right)

    pr = vf.get("price_range")
    if pr:
        pr_key = str(pr)
        bounds = _PRICE_RANGE_BOUNDS.get(pr_key)
        if bounds is None:
            dyn = _dynamic_bounds(pr_key)
            if dyn is not None:
                bounds = dyn
        if bounds:
            pmin, pmax = bounds
            if pmin is not None:
                out["price_min"] = pmin
            if pmax is not None:
                out["price_max"] = pmax

    fs = vf.get("flean_score")
    if fs:
        min_badge = _FLEAN_SCORE_TO_MIN_BADGE.get(str(fs))
        if min_badge is not None:
            out["min_flean_score"] = min_badge

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

    flavour = vf.get("flavour")
    if flavour:
        out["flavour"] = list(flavour)

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
    """Fetch flat suggestions — V2-native unless SEARCH_ENGINE=v1 explicitly.
    Auto-fallback-to-V1-on-exception was removed (final pre-production pass):
    live regression showed zero exceptions here even under varied partial-
    typing input; a genuine V2 failure now surfaces as a real error instead
    of silently degrading. See V1_FALLBACK_AUDIT.md."""
    if _search_engine() != "v1":
        from search_v2.extension.suggestions import suggest as v2_suggest
        return v2_suggest(query, size=size), None

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
            goals = _parse_hierarchy_query_param("goal")
            diets = _parse_hierarchy_query_param("diet")
            departments = _parse_hierarchy_query_param("department")
            categories = _parse_hierarchy_query_param("category")
            subcategories = _parse_hierarchy_query_param("subcategory", strip_path=True)

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

            goals, goal_err = _parse_hierarchy_body_param(
                body.get("goal"), field_name="goal"
            )
            if goal_err:
                return _error_response("INVALID_GOAL", goal_err[0], goal_err[1])
            diets, diet_err = _parse_hierarchy_body_param(
                body.get("diet"), field_name="diet"
            )
            if diet_err:
                return _error_response("INVALID_DIET", diet_err[0], diet_err[1])
            departments, dept_err = _parse_hierarchy_body_param(
                body.get("department"), field_name="department"
            )
            if dept_err:
                return _error_response("INVALID_DEPARTMENT", dept_err[0], dept_err[1])
            categories, cat_err = _parse_hierarchy_body_param(
                body.get("category"), field_name="category"
            )
            if cat_err:
                return _error_response("INVALID_CATEGORY", cat_err[0], cat_err[1])
            subcategories, sub_err = _parse_hierarchy_body_param(
                body.get("subcategory"), field_name="subcategory", strip_path=True
            )
            if sub_err:
                return _error_response("INVALID_SUBCATEGORY", sub_err[0], sub_err[1])

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

        goal_meta = _hierarchy_meta_value(goals)
        diet_meta = _hierarchy_meta_value(diets)
        department_meta = _hierarchy_meta_value(departments)
        category_meta = _hierarchy_meta_value(categories)
        subcategory_meta = _hierarchy_meta_value(subcategories)
        hierarchy_multi = (
            len(departments) > 1 or len(categories) > 1 or len(subcategories) > 1
        )

        # At least one selector is required
        if (
            not query
            and not goals
            and not diets
            and not departments
            and not categories
            and not subcategories
            and not raw_filters
        ):
            return _error_response(
                "MISSING_PARAMETER",
                "At least one of 'query', 'goal', 'diet', 'department', 'category', 'subcategory', or 'filters' must be provided",
                400,
            )

        if (goals or diets) and _search_engine() == "v1":
            unsupported = "goal" if goals else "diet"
            return _error_response(
                "UNSUPPORTED_PARAMETER",
                f"'{unsupported}' selector is supported only on Search V2",
                400,
            )

        if (categories or departments) and _search_engine() == "v1":
            unsupported = "department" if departments else "category"
            return _error_response(
                "UNSUPPORTED_PARAMETER",
                f"'{unsupported}' selector is supported only on Search V2",
                400,
            )

        # Resolve sort (with catalogue aliases) and validate
        resolved_sort = _resolve_sort(sort_raw, has_query=bool(query))
        goal_overlays: Dict[str, Any] = {}
        diet_overlays: Dict[str, Any] = {}
        if goals:
            try:
                goal_overlays = resolve_goal_selection(goals)
            except GoalConfigError as exc:
                log.error(
                    "UNIFIED_SEARCH_GOAL_CONFIG_ERROR | goals=%s | code=%s | error=%s",
                    goals,
                    exc.code,
                    exc.message,
                )
                return _error_response(exc.code, exc.message, exc.status_code)
        if diets:
            try:
                diet_overlays = resolve_diet_selection(diets)
            except GoalConfigError as exc:
                log.error(
                    "UNIFIED_SEARCH_DIET_CONFIG_ERROR | diets=%s | code=%s | error=%s",
                    diets,
                    exc.code,
                    exc.message,
                )
                return _error_response(exc.code, exc.message, exc.status_code)
        goal_sort_order = goal_overlays.get("goal_sort_order")
        diet_sort_order = diet_overlays.get("diet_sort_order")
        selector_sort_order = goal_sort_order or diet_sort_order
        selector_sort_by = goal_overlays.get("goal_sort_by") or diet_overlays.get("diet_sort_by")
        should_apply_selector_default_sort = bool(
            (goals or diets)
            and (
                not sort_raw
                or resolved_sort == "relevance"
            )
        )
        if should_apply_selector_default_sort and selector_sort_by:
            resolved_sort = str(selector_sort_by).strip().lower()
        use_selector_sort_order = bool(should_apply_selector_default_sort and selector_sort_order)

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
            f"UNIFIED_SEARCH_REQUEST | query={query} | department={department_meta} | "
            f"category={category_meta} | subcategory={subcategory_meta} | "
            f"goal={goal_meta} | diet={diet_meta} | page={page} | size={size} | sort={resolved_sort} | filters={validated_filters} "
            f"| effective_pincode={effective_pincode}"
        )

        result: Optional[Dict[str, Any]] = None

        # Filters-only requests (no query, no hierarchy selector) have nothing for
        # category_browsing.browse() to browse — they route through
        # v2_search with an empty query string instead, which retrieves
        # purely by filter (see lexical_query_builder.build_query()'s
        # empty-query path and hybrid_search_orchestrator's matching
        # short-circuit). Query-bearing requests always go through v2_search
        # too, regardless of subcategory (subcategory becomes a filter via
        # category_path_prefix below).
        # Multi-value hierarchy also uses the search/filter path so OR-within-
        # level / same-row nested filtering is applied consistently.
        # V2-native unless SEARCH_ENGINE=v1 explicitly. Auto-fallback-to-V1-
        # on-exception was removed (final pre-production pass): live
        # regression across varied queries/filters showed zero exceptions;
        # a genuine V2 failure now surfaces as a real error. See
        # V1_FALLBACK_AUDIT.md.
        filters_only = (
            (bool(validated_filters) or bool(goals) or bool(diets))
            and not departments
            and not subcategories
            and not categories
            and not query
        )
        selector_department = (
            len(departments) == 1 and not query and not hierarchy_multi
        )
        selector_only_category = (
            len(categories) == 1
            and not query
            and not subcategories
            and not departments
            and not hierarchy_multi
        )
        selector_category_with_subcategory = (
            len(categories) == 1
            and len(subcategories) == 1
            and not query
            and not departments
            and not hierarchy_multi
        )
        selector_only_subcategory = (
            len(subcategories) == 1
            and not query
            and not categories
            and not departments
            and not hierarchy_multi
        )

        if _search_engine() != "v1" and (query or filters_only or hierarchy_multi):
            gw_params: Dict[str, Any] = {
                "q": query or "",
                "size": size,
                "offset": page * size,
                "goal": goals or None,
                "diet": diets or None,
                "goal_ids": goal_overlays.get("goal_ids"),
                "goal_filter_clauses": goal_overlays.get("goal_filter_clauses"),
                "goal_must_not_clauses": goal_overlays.get("goal_must_not_clauses"),
                "goal_sort_by": goal_overlays.get("goal_sort_by"),
                "diet_ids": diet_overlays.get("diet_ids"),
                "diet_filter_clauses": diet_overlays.get("diet_filter_clauses"),
                "diet_must_not_clauses": diet_overlays.get("diet_must_not_clauses"),
                "diet_sort_by": diet_overlays.get("diet_sort_by"),
                "department": departments or None,
                "category": categories or None,
                "subcategory": subcategories or None,
            }
            if should_apply_selector_default_sort:
                gw_params["goal_sort_order"] = goal_sort_order
                gw_params["diet_sort_order"] = diet_sort_order
            if sort_raw and not should_apply_selector_default_sort:
                gw_params["sort_by"] = resolved_sort
            elif should_apply_selector_default_sort and selector_sort_by and not use_selector_sort_order:
                gw_params["sort_by"] = resolved_sort
            gw_params.update(_v1_filters_to_gw_params(validated_filters or {}))
            if departments:
                gw_params["department_segment_l1"] = departments
            if categories:
                gw_params["category_segment_l2"] = categories
            if subcategories:
                gw_params["subcategory_segment_l3"] = subcategories
            gw_result = v2_search(gw_params)
            gw_meta = gw_result.get("meta", {}) or {}
            gw_products = gw_result.get("products", [])
            gw_filters = gw_result.get("filters", []) if isinstance(gw_result, dict) else []
            gw_subcategories = gw_result.get("subcategories", []) if isinstance(gw_result, dict) else []
            returned = len(gw_products)
            result = {
                "products": gw_products,
                "filters": gw_filters,
                "subcategories": gw_subcategories,
                "meta": {
                    "total": gw_meta.get("total_hits", returned),
                    "page": page,
                    "size": size,
                    "total_pages": page + (2 if returned == size else 1),
                    "has_next": returned == size,
                    "has_prev": page > 0,
                    "query": query,
                    "goal": goal_meta,
                    "diet": diet_meta,
                    "department": department_meta,
                    "category": category_meta,
                    "subcategory": subcategory_meta,
                    "sort_by": resolved_sort,
                    "filters_applied": validated_filters,
                    "took_ms": gw_meta.get("took_ms", 0),
                    "fuzzy_fallback_used": False,
                    "prefix_fallback_used": False,
                    "phonetic_used": False,
                    "engine": "v2",
                },
            }

        if result is None and _search_engine() != "v1" and selector_department:
            browse_filter_params = _v1_filters_to_gw_params(validated_filters or {})
            if goals:
                browse_filter_params["goal"] = goals
                browse_filter_params["goal_ids"] = goal_overlays.get("goal_ids")
                browse_filter_params["goal_filter_clauses"] = goal_overlays.get("goal_filter_clauses")
                browse_filter_params["goal_must_not_clauses"] = goal_overlays.get("goal_must_not_clauses")
                browse_filter_params["goal_sort_by"] = goal_overlays.get("goal_sort_by")
                if should_apply_selector_default_sort:
                    browse_filter_params["goal_sort_order"] = goal_sort_order
            if diets:
                browse_filter_params["diet"] = diets
                browse_filter_params["diet_ids"] = diet_overlays.get("diet_ids")
                browse_filter_params["diet_filter_clauses"] = diet_overlays.get("diet_filter_clauses")
                browse_filter_params["diet_must_not_clauses"] = diet_overlays.get("diet_must_not_clauses")
                browse_filter_params["diet_sort_by"] = diet_overlays.get("diet_sort_by")
                if should_apply_selector_default_sort:
                    browse_filter_params["diet_sort_order"] = diet_sort_order
            if categories:
                browse_filter_params["category_segment_l2"] = categories
            if subcategories:
                browse_filter_params["subcategory_segment_l3"] = subcategories
            browse_filters = SearchFilters.from_dict(browse_filter_params) if browse_filter_params else None
            browse_result = browse_by_department_segment(
                department_segment_l1=departments[0],
                page=page,
                size=size,
                sort_by=None if use_selector_sort_order else resolved_sort,
                filters=browse_filters,
            )
            browse_meta = browse_result.get("meta", {}) or {}
            result = {
                "products": browse_result.get("products", []),
                "filters": browse_result.get("filters", []),
                "categories": browse_result.get("categories", []),
                "subcategories": browse_result.get("subcategories", []),
                "meta": {
                    **browse_meta,
                    "query": query,
                    "goal": goal_meta,
                    "diet": diet_meta,
                    "department": department_meta,
                    "category": category_meta,
                    "subcategory": subcategory_meta,
                    "filters_applied": validated_filters,
                    "fuzzy_fallback_used": False,
                    "prefix_fallback_used": False,
                    "phonetic_used": False,
                },
            }

        if result is None and _search_engine() != "v1" and (
            selector_only_category or selector_category_with_subcategory or selector_only_subcategory
        ):
            browse_filter_params = _v1_filters_to_gw_params(validated_filters or {})
            if goals:
                browse_filter_params["goal"] = goals
                browse_filter_params["goal_ids"] = goal_overlays.get("goal_ids")
                browse_filter_params["goal_filter_clauses"] = goal_overlays.get("goal_filter_clauses")
                browse_filter_params["goal_must_not_clauses"] = goal_overlays.get("goal_must_not_clauses")
                browse_filter_params["goal_sort_by"] = goal_overlays.get("goal_sort_by")
                if should_apply_selector_default_sort:
                    browse_filter_params["goal_sort_order"] = goal_sort_order
            if diets:
                browse_filter_params["diet"] = diets
                browse_filter_params["diet_ids"] = diet_overlays.get("diet_ids")
                browse_filter_params["diet_filter_clauses"] = diet_overlays.get("diet_filter_clauses")
                browse_filter_params["diet_must_not_clauses"] = diet_overlays.get("diet_must_not_clauses")
                browse_filter_params["diet_sort_by"] = diet_overlays.get("diet_sort_by")
                if should_apply_selector_default_sort:
                    browse_filter_params["diet_sort_order"] = diet_sort_order
            if selector_category_with_subcategory and subcategories:
                browse_filter_params["subcategory_segment_l3"] = subcategories
            browse_filters = SearchFilters.from_dict(browse_filter_params) if browse_filter_params else None
            if selector_only_category or selector_category_with_subcategory:
                browse_result = browse_by_category_segment(
                    category_segment_l2=categories[0],
                    page=page,
                    size=size,
                    sort_by=None if use_selector_sort_order else resolved_sort,
                    filters=browse_filters,
                )
            else:
                browse_result = browse_by_subcategory_segment(
                    subcategory_segment_l3=subcategories[0],
                    page=page,
                    size=size,
                    sort_by=None if use_selector_sort_order else resolved_sort,
                    filters=browse_filters,
                )

            browse_meta = browse_result.get("meta", {}) or {}
            result = {
                "products": browse_result.get("products", []),
                "filters": browse_result.get("filters", []),
                "subcategories": browse_result.get("subcategories", []),
                "meta": {
                    **browse_meta,
                    "query": query,
                    "goal": goal_meta,
                    "diet": diet_meta,
                    "department": department_meta,
                    "category": category_meta,
                    "subcategory": subcategory_meta,
                    "filters_applied": validated_filters,
                    "fuzzy_fallback_used": False,
                    "prefix_fallback_used": False,
                    "phonetic_used": False,
                },
            }
        if result is None:
            # Legacy V1 path
            try:
                fetcher = get_es_fetcher()
            except RuntimeError as exc:
                log.error("UNIFIED_SEARCH_CONFIG_ERROR | error=%s", exc, exc_info=True)
                return _error_response("INTERNAL_ERROR", str(exc), 500)

            result = fetcher.search_products_unified(
                query=query,
                subcategory=subcategory_meta if isinstance(subcategory_meta, str) else (
                    subcategories[0] if subcategories else None
                ),
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
        meta["goal"] = goal_meta
        meta["diet"] = diet_meta

        raw_products = result.get("products", [])
        product_cards: List[Dict[str, Any]] = []
        for raw in raw_products:
            if not raw:
                continue
            try:
                card = transform_to_product_card(raw)
                if card is not None:
                    _enrich_listing_card(card, raw)
                    card_in_stock = _derive_in_stock_from_availability(raw, effective_pincode)
                    card["in_stock"] = card_in_stock
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
            f"UNIFIED_SEARCH_COMPLETE | query={query} | department={department_meta} | "
            f"category={category_meta} | subcategory={subcategory_meta} | "
            f"total={meta.get('total', 0)} | returned={len(product_cards)}"
        )

        dynamic_filters = result.get("filters", []) if isinstance(result, dict) else []
        response_data: Dict[str, Any] = {
            "products": product_cards,
            "filters": dynamic_filters,
        }
        if departments and not query:
            response_data["categories"] = result.get("categories", []) if isinstance(result, dict) else []
        if categories and not query and not departments:
            response_data["subcategories"] = result.get("subcategories", []) if isinstance(result, dict) else []
        return jsonify(_success_response(response_data, meta=meta)), 200

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
