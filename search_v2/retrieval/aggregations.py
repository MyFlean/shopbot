"""
search_v2/retrieval/aggregations.py
──────────────────────────────────────
Brand aggregation and suggestion for Search V2.

Provides the brand-suggestion capability that V1 implemented inside
ElasticsearchProductsFetcher.suggest_brand(). The result lets the gateway
and ShopBot canonicalize user-supplied brand strings against what is actually
in the index.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_brand_suggest_query(
    hint: str,
    category_group: Optional[str] = None,
    size: int = 5,
) -> Dict[str, Any]:
    """
    Build a request body for brand-aggregation search.

    Matches documents whose brand field contains the hint (exact, prefix,
    contains) and aggregates the top-N unique brand values by document count.
    Returns size=0 (no hits, only aggregation).
    """
    hint = (hint or "").strip().strip("'\" ")
    if not hint:
        raise ValueError("hint must be a non-empty string")

    hint_lower = hint.lower()
    should_terms: List[Dict[str, Any]] = [
        {"term": {"brand.exact_normalized": hint_lower}},
        {"wildcard": {"brand.exact_normalized": {"value": f"{hint_lower}*"}}},
        {"wildcard": {"brand.exact_normalized": {"value": f"*{hint_lower}*"}}},
    ]

    filters: List[Dict[str, Any]] = []
    if category_group:
        filters.append({"term": {"category_group": category_group.strip()}})

    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": filters,
                "should": should_terms,
                "minimum_should_match": 1,
            }
        },
        "aggs": {
            "brand_suggest": {
                "terms": {"field": "brand.exact_normalized", "size": size}
            }
        },
    }


def parse_brand_suggest_response(response: Dict[str, Any]) -> Optional[str]:
    """Extract the top brand from an aggregation response, or None."""
    try:
        buckets = (
            response.get("aggregations", {})
            .get("brand_suggest", {})
            .get("buckets", [])
        )
        if buckets:
            return str(buckets[0].get("key", "")).strip() or None
    except (AttributeError, IndexError, TypeError):
        pass
    return None


def parse_brand_suggest_all(response: Dict[str, Any]) -> List[str]:
    """Extract all brand suggestions from an aggregation response."""
    try:
        buckets = (
            response.get("aggregations", {})
            .get("brand_suggest", {})
            .get("buckets", [])
        )
        return [str(b.get("key", "")).strip() for b in buckets if b.get("key")]
    except (AttributeError, TypeError):
        return []


def build_category_agg_query(
    category_group: Optional[str] = None,
    size: int = 50,
) -> Dict[str, Any]:
    """Build a request body that returns the top leaf_category values."""
    filters: List[Dict[str, Any]] = []
    if category_group:
        filters.append({"term": {"category_group": category_group}})

    return {
        "size": 0,
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "aggs": {
            "leaf_categories": {
                "terms": {"field": "leaf_category", "size": size}
            }
        },
    }
