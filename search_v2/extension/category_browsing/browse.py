"""
Native Search V2 category/subcategory browsing (no query text).

Filter-only retrieval against category_paths (exact-match keyword field,
already carries the full breadcrumb of ancestor paths per document — see
SEARCH_V2_ARCHITECTURE_STUDY.md for why the legacy fetcher's
category_paths.keyword reference silently matched nothing). No lexical or
semantic scoring is involved; ordering is a pure sort clause.
"""
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
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
from search_v2.retrieval.listing import apply_flat_listing_defaults, finalize_listing_cards, listing_visibility_filter_clause
from search_v2.retrieval.opensearch_client import OpenSearchClient
from search_v2.retrieval.sorting import build_sort_clauses, resolve_sort_for_filters

_client: Optional[OpenSearchClient] = None


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def browse(
    category_path: str,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
) -> Dict[str, Any]:
    t0 = time.monotonic()
    offset = max(0, page) * max(1, size)
    category_filter = {"term": {"category_paths": category_path}}
    filter_clauses = [category_filter, listing_visibility_filter_clause()]
    must_not_clauses: List[Dict[str, Any]] = []
    should_extras: List[Dict[str, Any]] = []
    if filters is not None:
        fc = build_filter_clauses(filters)
        # category_path (the resolved subcategory arg) is authoritative for
        # "which category" — any category_paths clause from `filters` itself
        # would only narrow further, which browse() callers don't need.
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
        "size": size,
        "from": offset,
        "query": query,
        "track_total_hits": True,
    }
    effective_sort = resolve_sort_for_filters(sort_by, filters.goal_diet_ids if filters else None)
    sort_clauses = build_sort_clauses(effective_sort or "flean_score_desc")
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

    facets_response = client.search({
        "size": 0, "track_total_hits": False, "query": query,
        "aggs": build_facet_aggregations(price_ranges=price_ranges),
    })
    dynamic_filters = parse_dynamic_filters_from_aggs(facets_response.get("aggregations") or {})

    took_ms = round((time.monotonic() - t0) * 1000)
    return {
        "products": products,
        "filters": dynamic_filters,
        "meta": {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size if size else 0,
            "has_next": offset + len(products) < total,
            "has_prev": page > 0,
            "sort_by": effective_sort or "flean_score_desc",
            "category_path": category_path,
            "engine": "v2",
            "took_ms": took_ms,
        },
    }
