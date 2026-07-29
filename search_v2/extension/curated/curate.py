"""
Native Search V2 curated/filter-only retrieval.

Structured filters, no query text and no category scope — reuses
search_v2/retrieval/filters.py's SearchFilters/build_filter_clauses(), the
same filter-clause builder the query-driven search path already relies on.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from search_v2.config.settings import SETTINGS
from search_v2.extension.product import to_product_card
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
from search_v2.retrieval.listing import apply_flat_listing_defaults, apply_general_retrieval_rules, listing_visibility_filter_clause
from search_v2.retrieval.opensearch_client import OpenSearchClient
from search_v2.retrieval.sorting import build_sort_clauses, resolve_sort_for_filters

_client: Optional[OpenSearchClient] = None


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def curate(filters: Dict[str, Any], size: int = 4, sort_by: str = "flean_score_desc") -> Dict[str, Any]:
    search_filters = SearchFilters.from_dict(filters or {})
    fc = build_filter_clauses(search_filters)

    bool_clause: Dict[str, Any] = {"must": [{"match_all": {}}]}
    filter_clauses = [listing_visibility_filter_clause()]
    if fc.filter_clauses:
        filter_clauses.extend(fc.filter_clauses)
    bool_clause["filter"] = filter_clauses
    if fc.must_not_clauses:
        bool_clause["must_not"] = fc.must_not_clauses
    if fc.should_clauses:
        bool_clause["should"] = fc.should_clauses

    body: Dict[str, Any] = {"size": size, "query": {"bool": bool_clause}, "track_total_hits": True}
    effective_sort = resolve_sort_for_filters(sort_by, search_filters.goal_diet_ids)
    sort_clauses = build_sort_clauses(effective_sort or sort_by)
    if sort_clauses:
        body["sort"] = sort_clauses
    body = apply_flat_listing_defaults(body)

    response = _get_client().search(body)
    hits = (response.get("hits") or {}).get("hits") or []
    total = ((response.get("hits") or {}).get("total") or {}).get("value", len(hits))

    cards = [to_product_card(hit.get("_source") or {}, rank=i + 1, score=hit.get("_score") or 0.0) for i, hit in enumerate(hits)]
    cards = apply_general_retrieval_rules(cards)
    return {
        "products": cards,
        "section_title": "Curated For You",
        "has_more": total > size,
        "total_in_pool": total,
    }
