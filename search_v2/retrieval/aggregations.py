"""
search_v2/retrieval/aggregations.py
──────────────────────────────────────
Brand aggregation and suggestion for Search V2.

Provides brand-suggestion aggregation so the gateway and ShopBot can
canonicalize user-supplied brand strings against what is actually in the index.

MAPPING DRIFT, found and worked around during the vision_flow.py migration:
this file originally targeted `brand.exact_normalized`, a keyword sub-field
`search/search_v2/indexing/mapping_builder.py` DOES define — but the
currently-running local index (`products-search-v2`) predates that mapping
change and has no such sub-field, so every query built against it matched
zero documents (silently — no error, just an aggregation with empty
buckets). The same likely applies to `name.exact_normalized` (used for
exact-match relevance boosting in lexical_query_builder.py) if production's
index was built from the same older mapping. Recommend verifying production's
actual field list (`GET <index>/_mapping`) and, if these sub-fields are
missing there too, re-applying the current mapping + reindexing — see
PRODUCTION_READINESS.md. Until then, this file uses `brand_phonetic.keyword`
(confirmed present, holds the same raw brand string) as a working
equivalent — it is not phonetically fuzzed at the `.keyword` sub-field level,
only the base `brand_phonetic` field is, so exact/prefix/contains matching
here behaves identically to what `brand.exact_normalized` would have.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_BRAND_FIELD = "brand_phonetic.keyword"


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
        {"term": {_BRAND_FIELD: hint_lower}},
        {"wildcard": {_BRAND_FIELD: {"value": f"{hint_lower}*", "case_insensitive": True}}},
        {"wildcard": {_BRAND_FIELD: {"value": f"*{hint_lower}*", "case_insensitive": True}}},
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
                "terms": {"field": _BRAND_FIELD, "size": size}
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
