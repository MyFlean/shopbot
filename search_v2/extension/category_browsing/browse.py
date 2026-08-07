"""Native Search V2 category/subcategory browsing (no query text)."""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shopping_bot.data_fetchers.dynamic_search_filters import (
    FILTER_CATEGORY_ID,
    FILTER_DEPARTMENT_ID,
    FILTER_SUBCATEGORY_ID,
    build_dynamic_price_ranges,
    build_facet_aggregations,
    build_hierarchy_filter_group,
    build_price_bounds_aggregation,
    parse_dynamic_filters_from_aggs,
)
from search_v2.config.settings import SETTINGS
from search_v2.extension.product import to_product_card
from search_v2.extension.taxonomy import (
    build_category_terms_aggregation,
    build_department_subcategory_terms_aggregation,
    build_department_terms_aggregation,
    build_subcategory_terms_aggregation,
    parse_categories_from_aggregations,
    parse_category_counts_from_aggregations,
    parse_department_counts_from_aggregations,
    parse_department_subcategories_from_aggregations,
    parse_subcategories_from_aggregations,
    parse_subcategory_counts_from_aggregations,
    sync_category_metadata_with_app_config,
    sync_department_subcategory_metadata_with_app_config,
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
_DEPARTMENT_SCOPE_GLOBAL_AGG = "department_scope_global"
_DEPARTMENT_SCOPE_FILTER_AGG = "department_scope_filter"
_DEPARTMENT_SUBCATEGORY_SCOPE_GLOBAL_AGG = "department_subcategory_scope_global"
_DEPARTMENT_SUBCATEGORY_SCOPE_FILTER_AGG = "department_subcategory_scope_filter"


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


def _merge_hierarchy_lists(
    primary: Optional[Sequence[str]],
    secondary: Optional[Sequence[str]],
) -> Optional[List[str]]:
    out: List[str] = []
    seen: set[str] = set()
    for values in (primary, secondary):
        for value in values or []:
            normalized = str(value or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
    return out or None


def _coalesce_hierarchy_filters(
    selector_filters: SearchFilters,
    filters: Optional[SearchFilters],
) -> Tuple[SearchFilters, Optional[SearchFilters]]:
    """Merge hierarchy onto selector so one nested bool.filter is emitted."""
    departments = _merge_hierarchy_lists(
        selector_filters.department_segment_l1,
        filters.department_segment_l1 if filters else None,
    )
    categories = _merge_hierarchy_lists(
        selector_filters.category_segment_l2,
        filters.category_segment_l2 if filters else None,
    )
    subcategories = _merge_hierarchy_lists(
        selector_filters.subcategory_segment_l3,
        filters.subcategory_segment_l3 if filters else None,
    )
    selector_filters = replace(
        selector_filters,
        department_segment_l1=departments,
        category_segment_l2=categories,
        subcategory_segment_l3=subcategories,
    )
    if filters is None:
        return selector_filters, None
    return selector_filters, replace(
        filters,
        department_segment_l1=None,
        category_segment_l2=None,
        subcategory_segment_l3=None,
    )


def _label_lookup_from_metadata(entries: List[Dict[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("id") or "").strip().lower()
        name = str(entry.get("name") or "").strip()
        if key and name:
            out[key] = name
    return out


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
    include_department_grouped_subcategories: bool = False,
) -> Dict[str, Any]:
    t0 = time.monotonic()
    safe_size = max(1, min(int(size or 20), 100))
    offset = max(0, page) * max(1, safe_size)

    selector_filters, filters = _coalesce_hierarchy_filters(selector_filters, filters)
    active_departments = list(selector_filters.department_segment_l1 or [])
    active_categories = list(selector_filters.category_segment_l2 or [])
    active_subcategories = list(selector_filters.subcategory_segment_l3 or [])

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

    # Department facet (always): dual-agg when a department is already selected.
    facets_aggs.update(build_department_terms_aggregation())
    if active_departments:
        relaxed_department_selector = replace(
            selector_filters,
            department_segment_l1=None,
            category_segment_l2=None,
            subcategory_segment_l3=None,
        )
        relaxed_department_clauses = build_filter_clauses(relaxed_department_selector)
        relaxed_department_filter_clauses = list(relaxed_department_clauses.filter_clauses) + [
            listing_visibility_filter_clause()
        ]
        relaxed_department_must_not = list(relaxed_department_clauses.must_not_clauses)
        relaxed_department_should = list(relaxed_department_clauses.should_clauses)
        if filters is not None:
            relaxed_fc = build_filter_clauses(filters)
            relaxed_department_filter_clauses.extend(relaxed_fc.filter_clauses)
            relaxed_department_must_not.extend(relaxed_fc.must_not_clauses)
            relaxed_department_should.extend(relaxed_fc.should_clauses)
        facets_aggs[_DEPARTMENT_SCOPE_GLOBAL_AGG] = {
            "global": {},
            "aggs": {
                _DEPARTMENT_SCOPE_FILTER_AGG: {
                    "filter": _build_bool_query(
                        filter_clauses=relaxed_department_filter_clauses,
                        should_clauses=relaxed_department_should,
                        must_not_clauses=relaxed_department_must_not,
                    ),
                    "aggs": build_department_terms_aggregation(),
                }
            },
        }

    list_category_scope = category_segment_l2 or (
        active_categories[0] if len(active_categories) == 1 else None
    )
    if include_subcategories and list_category_scope:
        # Dynamic filter facets stay narrowed by all active filters,
        # but subcategory listing remains category-scoped even when a
        # specific subcategory is selected.
        facets_aggs.update(build_subcategory_terms_aggregation(list_category_scope))

        relaxed_selector_filters = replace(
            selector_filters, subcategory_segment_l3=None
        )
        relaxed_filters = filters
        if relaxed_filters and relaxed_filters.subcategory_segment_l3:
            relaxed_filters = replace(relaxed_filters, subcategory_segment_l3=None)

        relaxed_selector_clauses = build_filter_clauses(relaxed_selector_filters)
        relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [
            listing_visibility_filter_clause()
        ]
        relaxed_must_not_clauses: List[Dict[str, Any]] = list(
            relaxed_selector_clauses.must_not_clauses
        )
        relaxed_should_clauses: List[Dict[str, Any]] = list(
            relaxed_selector_clauses.should_clauses
        )

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
                    "aggs": build_subcategory_terms_aggregation(list_category_scope),
                }
            },
        }
    elif active_categories:
        # Subcategory facet for filter UX when categories are selected.
        facets_aggs.update(build_subcategory_terms_aggregation(active_categories))
        if active_subcategories:
            relaxed_selector_filters = replace(
                selector_filters, subcategory_segment_l3=None
            )
            relaxed_selector_clauses = build_filter_clauses(relaxed_selector_filters)
            relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [
                listing_visibility_filter_clause()
            ]
            relaxed_must_not_clauses = list(relaxed_selector_clauses.must_not_clauses)
            relaxed_should_clauses = list(relaxed_selector_clauses.should_clauses)
            if filters is not None:
                relaxed_fc = build_filter_clauses(filters)
                relaxed_filter_clauses.extend(relaxed_fc.filter_clauses)
                relaxed_must_not_clauses.extend(relaxed_fc.must_not_clauses)
                relaxed_should_clauses.extend(relaxed_fc.should_clauses)
            facets_aggs[_SUBCATEGORY_SCOPE_GLOBAL_AGG] = {
                "global": {},
                "aggs": {
                    _SUBCATEGORY_SCOPE_FILTER_AGG: {
                        "filter": _build_bool_query(
                            filter_clauses=relaxed_filter_clauses,
                            should_clauses=relaxed_should_clauses,
                            must_not_clauses=relaxed_must_not_clauses,
                        ),
                        "aggs": build_subcategory_terms_aggregation(active_categories),
                    }
                },
            }

    list_department_scope = department_segment_l1 or (
        active_departments[0] if len(active_departments) == 1 else None
    )
    if include_categories and list_department_scope:
        # Category listing remains department-scoped even when a specific
        # category/subcategory filter is selected on the products query.
        facets_aggs.update(build_category_terms_aggregation(list_department_scope))

        relaxed_selector_filters = replace(
            selector_filters,
            category_segment_l2=None,
            subcategory_segment_l3=None,
        )
        relaxed_filters = filters
        if relaxed_filters is not None:
            relaxed_filters = replace(
                relaxed_filters,
                category_segment_l2=None,
                subcategory_segment_l3=None,
            )

        relaxed_selector_clauses = build_filter_clauses(relaxed_selector_filters)
        relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [
            listing_visibility_filter_clause()
        ]
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
                    "aggs": build_category_terms_aggregation(list_department_scope),
                }
            },
        }
    elif active_departments:
        facets_aggs.update(build_category_terms_aggregation(active_departments))
        if active_categories or active_subcategories:
            relaxed_selector_filters = replace(
                selector_filters,
                category_segment_l2=None,
                subcategory_segment_l3=None,
            )
            relaxed_selector_clauses = build_filter_clauses(relaxed_selector_filters)
            relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [
                listing_visibility_filter_clause()
            ]
            relaxed_must_not_clauses = list(relaxed_selector_clauses.must_not_clauses)
            relaxed_should_clauses = list(relaxed_selector_clauses.should_clauses)
            if filters is not None:
                relaxed_fc = build_filter_clauses(filters)
                relaxed_filter_clauses.extend(relaxed_fc.filter_clauses)
                relaxed_must_not_clauses.extend(relaxed_fc.must_not_clauses)
                relaxed_should_clauses.extend(relaxed_fc.should_clauses)
            facets_aggs[_CATEGORY_SCOPE_GLOBAL_AGG] = {
                "global": {},
                "aggs": {
                    _CATEGORY_SCOPE_FILTER_AGG: {
                        "filter": _build_bool_query(
                            filter_clauses=relaxed_filter_clauses,
                            should_clauses=relaxed_should_clauses,
                            must_not_clauses=relaxed_must_not_clauses,
                        ),
                        "aggs": build_category_terms_aggregation(active_departments),
                    }
                },
            }

    if include_department_grouped_subcategories and list_department_scope:
        facets_aggs.update(build_department_subcategory_terms_aggregation(list_department_scope))

        relaxed_filters = filters
        if relaxed_filters is not None:
            relaxed_filters = replace(
                relaxed_filters,
                category_segment_l2=None,
                subcategory_segment_l3=None,
            )

        relaxed_selector_clauses = build_filter_clauses(selector_filters)
        relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [
            listing_visibility_filter_clause()
        ]
        relaxed_must_not_clauses = list(relaxed_selector_clauses.must_not_clauses)
        relaxed_should_clauses = list(relaxed_selector_clauses.should_clauses)

        if relaxed_filters is not None:
            relaxed_fc = build_filter_clauses(relaxed_filters)
            relaxed_filter_clauses.extend(relaxed_fc.filter_clauses)
            relaxed_must_not_clauses.extend(relaxed_fc.must_not_clauses)
            relaxed_should_clauses.extend(relaxed_fc.should_clauses)

        relaxed_department_subcategory_query = _build_bool_query(
            filter_clauses=relaxed_filter_clauses,
            should_clauses=relaxed_should_clauses,
            must_not_clauses=relaxed_must_not_clauses,
        )
        facets_aggs[_DEPARTMENT_SUBCATEGORY_SCOPE_GLOBAL_AGG] = {
            "global": {},
            "aggs": {
                _DEPARTMENT_SUBCATEGORY_SCOPE_FILTER_AGG: {
                    "filter": relaxed_department_subcategory_query,
                    "aggs": build_department_subcategory_terms_aggregation(
                        list_department_scope
                    ),
                }
            },
        }

    facets_response = client.search({
        "size": 0, "track_total_hits": False, "query": query,
        "aggs": facets_aggs,
    })
    facets_aggs_out = facets_response.get("aggregations") or {}
    dynamic_filters = parse_dynamic_filters_from_aggs(facets_aggs_out)

    department_source_aggs = facets_aggs_out
    scoped_department_aggs = (
        (facets_aggs_out.get(_DEPARTMENT_SCOPE_GLOBAL_AGG) or {})
        .get(_DEPARTMENT_SCOPE_FILTER_AGG)
    )
    if isinstance(scoped_department_aggs, dict):
        department_source_aggs = scoped_department_aggs
    department_counts = parse_department_counts_from_aggregations(department_source_aggs)
    department_group = build_hierarchy_filter_group(
        group_id=FILTER_DEPARTMENT_ID,
        title="Department",
        title_key="department",
        counts=department_counts,
        selected_values=active_departments,
    )
    if department_group:
        dynamic_filters.append(department_group)

    subcategories: List[Dict[str, str]] = []
    subcategory_counts: Dict[str, int] = {}
    subcategory_label_lookup: Dict[str, str] = {}
    if include_subcategories and list_category_scope:
        subcategory_source_aggs = facets_aggs_out
        scoped_subcategory_aggs = (
            (facets_aggs_out.get(_SUBCATEGORY_SCOPE_GLOBAL_AGG) or {})
            .get(_SUBCATEGORY_SCOPE_FILTER_AGG)
        )
        if isinstance(scoped_subcategory_aggs, dict):
            subcategory_source_aggs = scoped_subcategory_aggs
        subcategory_ids = parse_subcategories_from_aggregations(
            subcategory_source_aggs, list_category_scope
        )
        subcategory_counts = parse_subcategory_counts_from_aggregations(
            subcategory_source_aggs, list_category_scope
        )
        subcategories = sync_subcategory_metadata_with_app_config(
            category=list_category_scope,
            es_subcategory_ids=subcategory_ids,
        )
        subcategory_label_lookup = _label_lookup_from_metadata(subcategories)
    elif active_categories:
        subcategory_source_aggs = facets_aggs_out
        scoped_subcategory_aggs = (
            (facets_aggs_out.get(_SUBCATEGORY_SCOPE_GLOBAL_AGG) or {})
            .get(_SUBCATEGORY_SCOPE_FILTER_AGG)
        )
        if isinstance(scoped_subcategory_aggs, dict):
            subcategory_source_aggs = scoped_subcategory_aggs
        subcategory_counts = parse_subcategory_counts_from_aggregations(
            subcategory_source_aggs, active_categories
        )

    # Subcategory-only browse has no category selector, so subcategory
    # sibling facets are not built above. Infer parent category(ies) from
    # the filtered result set, then fetch siblings with subcategory relaxed.
    inferred_categories_for_subcategories: List[str] = []
    if (
        not subcategory_counts
        and active_subcategories
        and not active_categories
    ):
        parent_depts = list(department_counts.keys()) if department_counts else []
        if parent_depts:
            parent_category_response = client.search({
                "size": 0,
                "track_total_hits": False,
                "query": query,
                "aggs": build_category_terms_aggregation(parent_depts),
            })
            inferred_categories_for_subcategories = list(
                parse_category_counts_from_aggregations(
                    parent_category_response.get("aggregations") or {},
                    parent_depts,
                ).keys()
            )
        if inferred_categories_for_subcategories:
            relaxed_selector_filters = replace(
                selector_filters, subcategory_segment_l3=None
            )
            relaxed_selector_clauses = build_filter_clauses(relaxed_selector_filters)
            relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [
                listing_visibility_filter_clause()
            ]
            relaxed_must_not_clauses = list(relaxed_selector_clauses.must_not_clauses)
            relaxed_should_clauses = list(relaxed_selector_clauses.should_clauses)
            if filters is not None:
                relaxed_fc = build_filter_clauses(filters)
                relaxed_filter_clauses.extend(relaxed_fc.filter_clauses)
                relaxed_must_not_clauses.extend(relaxed_fc.must_not_clauses)
                relaxed_should_clauses.extend(relaxed_fc.should_clauses)
            sibling_subcategory_response = client.search({
                "size": 0,
                "track_total_hits": False,
                "query": _build_bool_query(
                    filter_clauses=relaxed_filter_clauses,
                    should_clauses=relaxed_should_clauses,
                    must_not_clauses=relaxed_must_not_clauses,
                ),
                "aggs": build_subcategory_terms_aggregation(
                    inferred_categories_for_subcategories
                ),
            })
            sibling_subcategory_aggs = sibling_subcategory_response.get("aggregations") or {}
            subcategory_counts = parse_subcategory_counts_from_aggregations(
                sibling_subcategory_aggs, inferred_categories_for_subcategories
            )
            if len(inferred_categories_for_subcategories) == 1:
                subcategory_ids = parse_subcategories_from_aggregations(
                    sibling_subcategory_aggs, inferred_categories_for_subcategories[0]
                )
                subcategories = sync_subcategory_metadata_with_app_config(
                    category=inferred_categories_for_subcategories[0],
                    es_subcategory_ids=subcategory_ids,
                )
                subcategory_label_lookup = _label_lookup_from_metadata(subcategories)

    if subcategory_counts:
        subcategory_group = build_hierarchy_filter_group(
            group_id=FILTER_SUBCATEGORY_ID,
            title="Product Types",
            title_key="subcategory",
            counts=subcategory_counts,
            selected_values=active_subcategories,
            label_lookup=subcategory_label_lookup,
        )
        if subcategory_group:
            dynamic_filters.append(subcategory_group)

    categories: List[Dict[str, str]] = []
    category_counts: Dict[str, int] = {}
    category_label_lookup: Dict[str, str] = {}
    if include_categories and list_department_scope:
        category_source_aggs = facets_aggs_out
        scoped_category_aggs = (
            (facets_aggs_out.get(_CATEGORY_SCOPE_GLOBAL_AGG) or {})
            .get(_CATEGORY_SCOPE_FILTER_AGG)
        )
        if isinstance(scoped_category_aggs, dict):
            category_source_aggs = scoped_category_aggs
        category_ids = parse_categories_from_aggregations(
            category_source_aggs, list_department_scope
        )
        category_counts = parse_category_counts_from_aggregations(
            category_source_aggs, list_department_scope
        )
        categories = sync_category_metadata_with_app_config(
            department=list_department_scope,
            es_category_ids=category_ids,
        )
        category_label_lookup = _label_lookup_from_metadata(categories)
    elif active_departments:
        category_source_aggs = facets_aggs_out
        scoped_category_aggs = (
            (facets_aggs_out.get(_CATEGORY_SCOPE_GLOBAL_AGG) or {})
            .get(_CATEGORY_SCOPE_FILTER_AGG)
        )
        if isinstance(scoped_category_aggs, dict):
            category_source_aggs = scoped_category_aggs
        category_counts = parse_category_counts_from_aggregations(
            category_source_aggs, active_departments
        )

    # Category/subcategory-only browse has no department selector, so category
    # sibling facets are not built above. Infer department(s) from the
    # result-set department facet and fetch sibling categories under them.
    inferred_departments_for_categories: List[str] = []
    if (
        not category_counts
        and (active_categories or active_subcategories)
        and not active_departments
        and department_counts
    ):
        inferred_departments_for_categories = list(department_counts.keys())
        relaxed_selector_filters = replace(
            selector_filters,
            category_segment_l2=None,
            subcategory_segment_l3=None,
        )
        relaxed_selector_clauses = build_filter_clauses(relaxed_selector_filters)
        relaxed_filter_clauses = list(relaxed_selector_clauses.filter_clauses) + [
            listing_visibility_filter_clause()
        ]
        relaxed_must_not_clauses = list(relaxed_selector_clauses.must_not_clauses)
        relaxed_should_clauses = list(relaxed_selector_clauses.should_clauses)
        if filters is not None:
            relaxed_fc = build_filter_clauses(filters)
            relaxed_filter_clauses.extend(relaxed_fc.filter_clauses)
            relaxed_must_not_clauses.extend(relaxed_fc.must_not_clauses)
            relaxed_should_clauses.extend(relaxed_fc.should_clauses)
        sibling_category_response = client.search({
            "size": 0,
            "track_total_hits": False,
            "query": _build_bool_query(
                filter_clauses=relaxed_filter_clauses,
                should_clauses=relaxed_should_clauses,
                must_not_clauses=relaxed_must_not_clauses,
            ),
            "aggs": build_category_terms_aggregation(inferred_departments_for_categories),
        })
        sibling_category_aggs = sibling_category_response.get("aggregations") or {}
        category_counts = parse_category_counts_from_aggregations(
            sibling_category_aggs, inferred_departments_for_categories
        )
        if len(inferred_departments_for_categories) == 1:
            category_ids = parse_categories_from_aggregations(
                sibling_category_aggs, inferred_departments_for_categories[0]
            )
            categories = sync_category_metadata_with_app_config(
                department=inferred_departments_for_categories[0],
                es_category_ids=category_ids,
            )
            category_label_lookup = _label_lookup_from_metadata(categories)

    selected_category_values = active_categories or inferred_categories_for_subcategories
    if category_counts:
        category_group = build_hierarchy_filter_group(
            group_id=FILTER_CATEGORY_ID,
            title="Category",
            title_key="category",
            counts=category_counts,
            selected_values=selected_category_values,
            label_lookup=category_label_lookup,
        )
        if category_group:
            dynamic_filters.append(category_group)

    grouped_subcategories: List[Dict[str, Any]] = []
    if include_department_grouped_subcategories and list_department_scope:
        department_subcategory_source_aggs = facets_aggs_out
        scoped_department_subcategory_aggs = (
            (facets_aggs_out.get(_DEPARTMENT_SUBCATEGORY_SCOPE_GLOBAL_AGG) or {})
            .get(_DEPARTMENT_SUBCATEGORY_SCOPE_FILTER_AGG)
        )
        if isinstance(scoped_department_subcategory_aggs, dict):
            department_subcategory_source_aggs = scoped_department_subcategory_aggs
        subcategory_ids_by_category = parse_department_subcategories_from_aggregations(
            department_subcategory_source_aggs,
            list_department_scope,
        )
        grouped_subcategories = sync_department_subcategory_metadata_with_app_config(
            department=list_department_scope,
            es_subcategory_ids_by_category=subcategory_ids_by_category,
        )
    department_flat_subcategories: List[Dict[str, str]] = []
    if include_department_grouped_subcategories:
        seen_subcategory_ids: set[str] = set()
        for grouped_entry in grouped_subcategories:
            if not isinstance(grouped_entry, dict):
                continue
            items = grouped_entry.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw_id = str(item.get("id") or "").strip()
                normalized_id = raw_id.lower()
                if not raw_id or normalized_id in seen_subcategory_ids:
                    continue
                department_flat_subcategories.append(
                    {
                        "id": raw_id,
                        "image": str(item.get("image") or "").strip(),
                        "name": str(item.get("name") or "").strip(),
                    }
                )
                seen_subcategory_ids.add(normalized_id)

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
    if include_department_grouped_subcategories:
        result["subcategories"] = department_flat_subcategories
    return result


def browse_by_department_segment(
    department_segment_l1: str,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
) -> Dict[str, Any]:
    normalized = (department_segment_l1 or "").strip().lower()
    selected_categories = list((filters.category_segment_l2 if filters else None) or [])
    selected_subcategories = list(
        (filters.subcategory_segment_l3 if filters else None) or []
    )
    selector_filters = SearchFilters(
        department_segment_l1=[normalized] if normalized else None
    )
    include_categories = not selected_categories and not selected_subcategories
    include_subcategories = bool(selected_categories) and not selected_subcategories
    include_department_grouped_subcategories = (
        not selected_categories and not selected_subcategories
    )
    return _browse_by_filters(
        selector_filters=selector_filters,
        page=page,
        size=size,
        sort_by=sort_by,
        filters=filters,
        include_subcategories=include_subcategories,
        category_segment_l2=(
            selected_categories[0] if len(selected_categories) == 1 else None
        ),
        include_categories=include_categories,
        department_segment_l1=normalized or None,
        include_department_grouped_subcategories=include_department_grouped_subcategories,
    )


def browse_by_category_segment(
    category_segment_l2: str,
    page: int = 0,
    size: int = 20,
    sort_by: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
) -> Dict[str, Any]:
    normalized = (category_segment_l2 or "").strip().lower()
    selector_filters = SearchFilters(
        category_segment_l2=[normalized] if normalized else None
    )
    return _browse_by_filters(
        selector_filters=selector_filters,
        page=page,
        size=size,
        sort_by=sort_by,
        filters=filters,
        include_subcategories=True,
        category_segment_l2=normalized or None,
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
    selector_filters = SearchFilters(
        subcategory_segment_l3=[normalized_subcategory] if normalized_subcategory else None
    )
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
