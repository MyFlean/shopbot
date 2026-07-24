"""
Native Search V2 best-selling-by-category aggregation.

Single ES request: one `filters` aggregation bucket per category path, each
with a `top_hits` sub-aggregation sorted by flean_score.adjusted_score
descending. Category paths are exact keyword-field terms, matching
category_browsing/'s use of category_paths (see that module for why the
legacy .keyword suffix doesn't apply to this index).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from search_v2.config.settings import SETTINGS
from search_v2.extension.product import to_product_card
from search_v2.retrieval.opensearch_client import OpenSearchClient

_client: Optional[OpenSearchClient] = None


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def fetch_candidates_by_category(paths: List[str], fetch_per_category: int) -> Dict[str, List[Dict[str, Any]]]:
    """Returns {path: [raw _source docs...]}, each sorted by flean score descending."""
    if not paths:
        return {}

    aggs = {
        path: {
            "filter": {"term": {"category_paths": path}},
            "aggs": {
                "top": {
                    "top_hits": {
                        "size": fetch_per_category,
                        "sort": [{"flean_score.adjusted_score": {"order": "desc", "missing": "_last"}}],
                    }
                }
            },
        }
        for path in paths
    }
    response = _get_client().search({"size": 0, "track_total_hits": False, "query": {"match_all": {}}, "aggs": aggs})
    buckets = response.get("aggregations") or {}

    results: Dict[str, List[Dict[str, Any]]] = {}
    for path in paths:
        hits = ((buckets.get(path) or {}).get("top") or {}).get("hits", {}).get("hits", [])
        results[path] = [hit.get("_source") or {} for hit in hits]
    return results


def best_selling(
    category_paths: List[str],
    per_category: int = 2,
    total_products: int = 6,
    fetch_buffer: int = 13,
) -> List[Dict[str, Any]]:
    fetch_per_category = per_category + fetch_buffer
    candidates_by_path = fetch_candidates_by_category(category_paths, fetch_per_category)

    selected: List[Dict[str, Any]] = []
    selected_ids: set = set()
    backfill: List[tuple] = []

    for path in category_paths:
        scored = [
            (float((src.get("flean_score") or {}).get("adjusted_score") or 0.0), to_product_card(src))
            for src in candidates_by_path.get(path, [])
            if src.get("id")
        ]
        scored.sort(key=lambda item: item[0], reverse=True)

        category_count = 0
        for score, card in scored:
            product_id = card["id"]
            if product_id in selected_ids:
                continue
            if category_count < per_category:
                selected.append(card)
                selected_ids.add(product_id)
                category_count += 1
            else:
                backfill.append((score, card))

    if len(selected) < total_products:
        backfill.sort(key=lambda item: item[0], reverse=True)
        for _, card in backfill:
            product_id = card["id"]
            if product_id in selected_ids:
                continue
            selected.append(card)
            selected_ids.add(product_id)
            if len(selected) >= total_products:
                break

    selected.sort(key=lambda card: (card.get("flean_score") or 0.0, card.get("flean_percentile") or 0.0), reverse=True)
    return selected[:total_products]
