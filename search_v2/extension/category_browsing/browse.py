"""Native Search V2 category/subcategory browsing (no query text)."""
from __future__ import annotations

import time
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
    build_subcategory_terms_aggregation,
    parse_subcategories_from_aggregations,
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


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def _browse_by_filters(
    selector_filters: SearchFilters,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
    include_subcategories: bool = False,
    category_segment_l2: Optional[str] = None,
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

    bool_clause: Dict[str, Any] = {"filter": filter_clauses}
    if should_extras:
        bool_clause["should"] = should_extras
    if must_not_clauses:
        bool_clause["must_not"] = must_not_clauses
    query = {"bool": bool_clause}

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
        facets_aggs.update(build_subcategory_terms_aggregation(category_segment_l2))
    facets_response = client.search({
        "size": 0, "track_total_hits": False, "query": query,
        "aggs": facets_aggs,
    })
    facets_aggs_out = facets_response.get("aggregations") or {}
    dynamic_filters = parse_dynamic_filters_from_aggs(facets_aggs_out)
    subcategories: List[Dict[str, str]] = []
    if include_subcategories and category_segment_l2:
        subcategory_ids = parse_subcategories_from_aggregations(facets_aggs_out, category_segment_l2)
        subcategories = sync_subcategory_metadata_with_app_config(
            category=category_segment_l2,
            es_subcategory_ids=subcategory_ids,
        )

    took_ms = round((time.monotonic() - t0) * 1000)
    return {
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
            "category_segment_l2": category_segment_l2,
            "engine": "v2",
            "took_ms": took_ms,
        },
    }


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
