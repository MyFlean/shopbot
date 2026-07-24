"""
Native Search V2 Flean Picks — curated, per-subcategory-collection retrieval
with 3-tier macro-filter relaxation.

One `filters` aggregation request per tier (matching bestsellers/'s
pattern), each bucket keyed by collection, filtered by that collection's
category paths plus the tier's personalization filters. Stops issuing
further tiers once every collection has enough results.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from search_v2.config.settings import SETTINGS
from search_v2.extension.product import to_product_card
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
from search_v2.retrieval.opensearch_client import OpenSearchClient

_client: Optional[OpenSearchClient] = None


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def _collection_query(tier_filters: Optional[Dict[str, Any]], es_paths: List[str], exclude_ids: set) -> Dict[str, Any]:
    merged = {**(tier_filters or {}), "category_paths": es_paths}
    fc = build_filter_clauses(SearchFilters.from_dict(merged))
    bool_clause: Dict[str, Any] = {"must": [{"match_all": {}}]}
    if fc.filter_clauses:
        bool_clause["filter"] = fc.filter_clauses
    must_not = list(fc.must_not_clauses)
    if exclude_ids:
        must_not.append({"terms": {"id": list(exclude_ids)}})
    if must_not:
        bool_clause["must_not"] = must_not
    if fc.should_clauses:
        bool_clause["should"] = fc.should_clauses
    return {"bool": bool_clause}


def flean_picks(
    categories: Dict[str, Dict[str, Any]],
    tiers: List[tuple],
    needed: int,
    fetch_per: int,
) -> Dict[str, List[Dict[str, Any]]]:
    """Returns {collection_key: [product cards...]}, each up to `needed` items."""
    collected: Dict[str, List[Dict[str, Any]]] = {k: [] for k in categories}
    collected_ids: set = set()
    client = _get_client()

    for _tier_name, tier_filters in tiers:
        short_keys = [k for k in categories if len(collected[k]) < needed]
        if not short_keys:
            break

        aggs = {
            key: {
                "filter": _collection_query(tier_filters, categories[key]["es_paths"], collected_ids),
                "aggs": {
                    "top": {
                        "top_hits": {
                            "size": fetch_per,
                            "sort": [{"flean_score.adjusted_score": {"order": "desc", "missing": "_last"}}],
                        }
                    }
                },
            }
            for key in short_keys
        }
        response = client.search({"size": 0, "track_total_hits": False, "query": {"match_all": {}}, "aggs": aggs})
        buckets = response.get("aggregations") or {}

        for key in short_keys:
            hits = ((buckets.get(key) or {}).get("top") or {}).get("hits", {}).get("hits", [])
            for hit in hits:
                if len(collected[key]) >= needed:
                    break
                src = hit.get("_source") or {}
                product_id = src.get("id")
                if not product_id or product_id in collected_ids:
                    continue
                collected_ids.add(product_id)
                collected[key].append(to_product_card(src))

    return collected
