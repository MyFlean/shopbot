"""
Native Search V2 "similar products" — backs both Alternatives and
Recommended, which share identical logic in V1: same subcategory (most
specific category_paths entry), sorted by flean percentile descending,
excluding the source product itself.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from search_v2.config.settings import SETTINGS
from search_v2.extension.product import to_product_card
from search_v2.retrieval.opensearch_client import OpenSearchClient

_client: Optional[OpenSearchClient] = None


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def _fetch_by_id(product_id: str) -> Optional[Dict[str, Any]]:
    response = _get_client().search({"size": 1, "query": {"term": {"id": product_id}}})
    hits = (response.get("hits") or {}).get("hits") or []
    return hits[0].get("_source") if hits else None


def similar_products(product_id: str, limit: int = 5) -> Dict[str, Any]:
    source = _fetch_by_id(product_id)
    if not source:
        return {"source_product": None, "alternatives": [], "total_in_subcategory": 0}

    category_paths = source.get("category_paths") or []
    subcat = max(category_paths, key=lambda p: len(str(p or ""))) if category_paths else None
    if not subcat:
        return {"source_product": to_product_card(source), "alternatives": [], "total_in_subcategory": 0}

    body = {
        "size": limit,
        "track_total_hits": True,
        "query": {
            "bool": {
                "filter": [{"term": {"category_paths": subcat}}],
                "must_not": [{"term": {"id": product_id}}],
            }
        },
        "sort": [{"stats.adjusted_score_percentiles.subcategory_percentile": {"order": "desc", "missing": "_last"}}],
    }
    response = _get_client().search(body)
    hits = (response.get("hits") or {}).get("hits") or []
    total = ((response.get("hits") or {}).get("total") or {}).get("value", len(hits))

    return {
        "source_product": to_product_card(source),
        "alternatives": [to_product_card(hit.get("_source") or {}) for hit in hits],
        "subcategory": subcat,
        "total_in_subcategory": total,
    }
