"""
Native Search V2 PDP document fetch.

Retrieval only — card/PDP shaping lives in shopping_bot.product_transforms.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from search_v2.config.settings import SETTINGS
from search_v2.retrieval.opensearch_client import OpenSearchClient

_client: Optional[OpenSearchClient] = None


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def fetch_product(product_id: str) -> Optional[Dict[str, Any]]:
    response = _get_client().search({"size": 1, "query": {"term": {"id": product_id}}})
    hits = (response.get("hits") or {}).get("hits") or []
    return hits[0].get("_source") if hits else None


def fetch_products_batch(product_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """One `terms` query for every id, not N single-id fetches.
    Returns {id: _source} only for ids that were actually found."""
    if not product_ids:
        return {}
    response = _get_client().search({
        "size": len(product_ids),
        "query": {"terms": {"id": product_ids}},
    })
    hits = (response.get("hits") or {}).get("hits") or []
    return {
        src.get("id"): src
        for src in (hit.get("_source") for hit in hits)
        if src and src.get("id")
    }
