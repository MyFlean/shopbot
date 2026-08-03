"""Native Search V2 category/subcategory browsing (no query text)."""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Dict, List, Optional

from shopping_bot.data_fetchers.dynamic_search_filters import (
    build_dynamic_price_ranges,
    build_facet_aggregations,
    build_price_bounds_aggregation,
    parse_dynamic_filters_from_aggs,
)
from search_v2.config.settings import SETTINGS
from search_v2.extension.product import to_product_card
from search_v2.extension.taxonomy import (
    build_category_terms_aggregation,
    build_subcategory_terms_aggregation,
    parse_categories_from_aggregations,
    parse_subcategories_from_aggregations,
    sync_category_metadata_with_app_config,
    sync_subcategory_metadata_with_app_config,
)
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
from search_v2.retrieval.listing import (
    apply_flat_listing_defaults,
    finalize_listing_cards,
    listing_visibility_filter_clause,
)
from search_v2.retrieval.opensearch_client import OpenSearchClient
from search_v2.retrieval.sorting import build_sort_clauses

_client: Optional[OpenSearchClient] = None
_SUBCATEGORY_SCOPE_GLOBAL_AGG = "subcategory_scope_global"
_SUBCATEGORY_SCOPE_FILTER_AGG = "subcategory_scope_filter"
_CATEGORY_SCOPE_GLOBAL_AGG = "category_scope_global"
_CATEGORY_SCOPE_FILTER_AGG = "category_scope_filter"


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def _build_bool_query(
    filter_clauses: List[Dict[str, Any]],
    should_clauses: List[Dict[str, Any]],
    must_not_clauses: List[Dict[str, Any]],
) -> Dict[str, Any]:
    bool_clause: Dict[str, Any] = {"filter": filter_clauses}
    if should_clauses:
        bool_clause["should"] = should_clauses
    if must_not_clauses:
        bool_clause["must_not"] = must_not_clauses
    return {"bool": bool_clause}


def _browse_by_filters(
    selector_filters: SearchFilters,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
    include_subcategories: bool = False,
    category_segment_l2: Optional[str] = None,
    include_categories: bool = False,
    department_segment_l1: Optional[str] = None,
) -> Dict[str, Any]:
    t0 = time.monotonic()
    safe_size = max(1, min(int(size or 20), 100))
    offset = max(0, page) * max(1, safe_size)
    selector_clauses = build_filter_clauses(selector_filters)
    filter_clauses = list(selector_clauses.filter_clauses) + [listing_visibility_filter_clause()]
    must_not_clauses: List[Dict[str, Any]] = []
    should_extras: List[Dict[str, Any]] = []

    must_not_clauses.extend(selector_clauses.must_not_clauses)
    should_extras.extend(selector_clauses.should_clauses)

    if filters is not None:
        fc = build_filter_clauses(filters)
        filter_clauses.extend(fc.filter_clauses)
        must_not_clauses.extend(fc.must_not_clauses)
        should_extras.extend(fc.should_clauses)

    query = _build_bool_query(
        filter_clauses=filter_clauses,
        should_clauses=should_extras,
        must_not_clauses=must_not_clauses,
    )

    body: Dict[str, Any] = {
        "size": safe_size,
        "from": offset,
        "query": query,
        "track_total_hits": True,
    }
    sort_clauses = build_sort_clauses(sort_by or "flean_score_desc")
    if sort_clauses:
        body["sort"] = sort_clauses
    body = apply_flat_listing_defaults(body)

    client = _get_client()
    response = client.search(body)
    hits = (response.get("hits") or {}).get("hits") or []
    total = ((response.get("hits") or {}).get("total") or {}).get("value", len(hits))

    products = [
        to_product_card(hit.get("_source") or {}, rank=i + 1, score=hit.get("_score") or 0.0)
        for i, hit in enumerate(hits)
    ]
    products = finalize_listing_cards(products)
    for i, product in enumerate(products, 1):
        product["rank"] = i

    bounds_response = client.search({
        "size": 0, "track_total_hits": False, "query": query,
        "aggs": build_price_bounds_aggregation(),
    })
    bounds_aggs = bounds_response.get("aggregations") or {}
    price_min = (bounds_aggs.get("price_min") or {}).get("value")
    price_max = (bounds_aggs.get("price_max") or {}).get("value")
    price_ranges = build_dynamic_price_ranges(price_min, price_max, target_buckets=4)

    facets_aggs: Dict[str, Any] = build_facet_aggregations(price_ranges=price_ranges)
    if include_subcategories and category_segment_l2:
        # Dynamic filter facets should stay narrowed by all active filters,
        # but subcategory listing must remain category-scoped even when a
        # specific subcategory is selected.
        facets_aggs.update(build_subcategory_terms_aggregation(category_segment_l2))

        relaxed_selector_filters = selector_filters
        relaxed_filters = filters
        if relaxed_filters and relaxed_filters.subcategory_segment_l3:
            relaxed_filters = replace(relaxed_filters, subcategory_segment_l3=None)

        relaxed_selector_clauses = build_filter_clauses(relaxed_selector_filters)
        relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [listing_visibility_filter_clause()]
        relaxed_must_not_clauses: List[Dict[str, Any]] = list(relaxed_selector_clauses.must_not_clauses)
        relaxed_should_clauses: List[Dict[str, Any]] = list(relaxed_selector_clauses.should_clauses)

        if relaxed_filters is not None:
            relaxed_fc = build_filter_clauses(relaxed_filters)
            relaxed_filter_clauses.extend(relaxed_fc.filter_clauses)
            relaxed_must_not_clauses.extend(relaxed_fc.must_not_clauses)
            relaxed_should_clauses.extend(relaxed_fc.should_clauses)

        relaxed_subcategory_query = _build_bool_query(
            filter_clauses=relaxed_filter_clauses,
            should_clauses=relaxed_should_clauses,
            must_not_clauses=relaxed_must_not_clauses,
        )
        facets_aggs[_SUBCATEGORY_SCOPE_GLOBAL_AGG] = {
            "global": {},
            "aggs": {
                _SUBCATEGORY_SCOPE_FILTER_AGG: {
                    "filter": relaxed_subcategory_query,
                    "aggs": build_subcategory_terms_aggregation(category_segment_l2),
                }
            },
        }
    if include_categories and department_segment_l1:
        # Category listing remains department-scoped even when a specific
        # category/subcategory filter is selected on the products query.
        facets_aggs.update(build_category_terms_aggregation(department_segment_l1))

        relaxed_filters = filters
        if relaxed_filters is not None:
            relaxed_filters = replace(
                relaxed_filters,
                category_segment_l2=None,
                subcategory_segment_l3=None,
            )

        relaxed_selector_clauses = build_filter_clauses(selector_filters)
        relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [listing_visibility_filter_clause()]
        relaxed_must_not_clauses = list(relaxed_selector_clauses.must_not_clauses)
        relaxed_should_clauses = list(relaxed_selector_clauses.should_clauses)

        if relaxed_filters is not None:
            relaxed_fc = build_filter_clauses(relaxed_filters)
            relaxed_filter_clauses.extend(relaxed_fc.filter_clauses)
            relaxed_must_not_clauses.extend(relaxed_fc.must_not_clauses)
            relaxed_should_clauses.extend(relaxed_fc.should_clauses)

        relaxed_category_query = _build_bool_query(
            filter_clauses=relaxed_filter_clauses,
            should_clauses=relaxed_should_clauses,
            must_not_clauses=relaxed_must_not_clauses,
        )
        facets_aggs[_CATEGORY_SCOPE_GLOBAL_AGG] = {
            "global": {},
            "aggs": {
                _CATEGORY_SCOPE_FILTER_AGG: {
                    "filter": relaxed_category_query,
                    "aggs": build_category_terms_aggregation(department_segment_l1),
                }
            },
        }
    facets_response = client.search({
        "size": 0, "track_total_hits": False, "query": query,
        "aggs": facets_aggs,
    })
    facets_aggs_out = facets_response.get("aggregations") or {}
    dynamic_filters = parse_dynamic_filters_from_aggs(facets_aggs_out)
    subcategories: List[Dict[str, str]] = []
    if include_subcategories and category_segment_l2:
        subcategory_source_aggs = facets_aggs_out
        scoped_subcategory_aggs = (
            (facets_aggs_out.get(_SUBCATEGORY_SCOPE_GLOBAL_AGG) or {})
            .get(_SUBCATEGORY_SCOPE_FILTER_AGG)
        )
        if isinstance(scoped_subcategory_aggs, dict):
            subcategory_source_aggs = scoped_subcategory_aggs
        subcategory_ids = parse_subcategories_from_aggregations(subcategory_source_aggs, category_segment_l2)
        subcategories = sync_subcategory_metadata_with_app_config(
            category=category_segment_l2,
            es_subcategory_ids=subcategory_ids,
        )
    categories: List[Dict[str, str]] = []
    if include_categories and department_segment_l1:
        category_source_aggs = facets_aggs_out
        scoped_category_aggs = (
            (facets_aggs_out.get(_CATEGORY_SCOPE_GLOBAL_AGG) or {})
            .get(_CATEGORY_SCOPE_FILTER_AGG)
        )
        if isinstance(scoped_category_aggs, dict):
            category_source_aggs = scoped_category_aggs
        category_ids = parse_categories_from_aggregations(
            category_source_aggs, department_segment_l1
        )
        categories = sync_category_metadata_with_app_config(
            department=department_segment_l1,
            es_category_ids=category_ids,
        )

    took_ms = round((time.monotonic() - t0) * 1000)
    result: Dict[str, Any] = {
        "products": products,
        "filters": dynamic_filters,
        "subcategories": subcategories if include_subcategories else [],
        "meta": {
            "total": total,
            "page": page,
            "size": safe_size,
            "total_pages": (total + safe_size - 1) // safe_size if safe_size else 0,
            "has_next": offset + len(products) < total,
            "has_prev": page > 0,
            "sort_by": sort_by or "flean_score_desc",
            "department_segment_l1": department_segment_l1,
            "category_segment_l2": category_segment_l2,
            "engine": "v2",
            "took_ms": took_ms,
        },
    }
    if include_categories:
        result["categories"] = categories
    return result


def browse_by_department_segment(
    department_segment_l1: str,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
) -> Dict[str, Any]:
    selector_filters = SearchFilters(
        department_segment_l1=(department_segment_l1 or "").strip().lower()
    )
    return _browse_by_filters(
        selector_filters=selector_filters,
        page=page,
        size=size,
        sort_by=sort_by,
        filters=filters,
        include_categories=True,
        department_segment_l1=selector_filters.department_segment_l1,
    )


def browse_by_category_segment(
    category_segment_l2: str,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
) -> Dict[str, Any]:
    selector_filters = SearchFilters(category_segment_l2=(category_segment_l2 or "").strip().lower())
    return _browse_by_filters(
        selector_filters=selector_filters,
        page=page,
        size=size,
        sort_by=sort_by,
        filters=filters,
        include_subcategories=True,
        category_segment_l2=selector_filters.category_segment_l2,
    )


def browse_by_subcategory_segment(
    subcategory_segment_l3: str,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
) -> Dict[str, Any]:
    normalized_subcategory = (subcategory_segment_l3 or "").strip().lower()
    if "/" in normalized_subcategory:
        normalized_subcategory = normalized_subcategory.rsplit("/", 1)[-1]
    selector_filters = SearchFilters(subcategory_segment_l3=normalized_subcategory)
    return _browse_by_filters(
        selector_filters=selector_filters,
        page=page,
        size=size,
        sort_by=sort_by,
        filters=filters,
        include_subcategories=False,
    )


def browse(
    category_path: str,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
) -> Dict[str, Any]:
    """Backward-compatible wrapper: treats input as segment-3 selector token."""
    return browse_by_subcategory_segment(
        subcategory_segment_l3=category_path,
        page=page,
        size=size,
        sort_by=sort_by,
        filters=filters,
    )
