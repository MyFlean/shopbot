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
from search_v2.retrieval.listing import apply_flat_listing_defaults, apply_general_retrieval_rules, listing_visibility_filter_clause
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


def _has_palm_oil_ingredient(source: Dict[str, Any]) -> bool:
    """
    Mirror PDP palm-oil inference rule using source ingredient tags.
    """
    category_data = source.get("category_data")
    if not isinstance(category_data, dict):
        return False
    tags = category_data.get("tags")
    if not isinstance(tags, dict):
        return False
    ingredient_tags = tags.get("ingredient_tags")
    if not isinstance(ingredient_tags, list):
        return False
    normalized_tags = {
        str(tag or "").strip().lower()
        for tag in ingredient_tags
        if str(tag or "").strip()
    }
    return "no_palm_oil" not in normalized_tags


def similar_products(product_id: str, limit: int = 5, candidate_size: Optional[int] = None) -> Dict[str, Any]:
    source = _fetch_by_id(product_id)
    if not source:
        return {
            "source_product": None,
            "alternatives": [],
            "alt_cta_meta_by_id": {},
            "total_in_subcategory": 0,
        }

    category_paths = source.get("category_paths") or []
    subcat = max(category_paths, key=lambda p: len(str(p or ""))) if category_paths else None
    if not subcat:
        return {
            "source_product": to_product_card(source),
            "alternatives": [],
            "alt_cta_meta_by_id": {},
            "total_in_subcategory": 0,
        }

    effective_size = max(int(candidate_size or limit), int(limit))
    body = apply_flat_listing_defaults(
        {
            "size": effective_size,
            "track_total_hits": True,
            "query": {
                "bool": {
                    "filter": [
                        listing_visibility_filter_clause(),
                        {"term": {"category_paths": subcat}},
                    ],
                    "must_not": [{"term": {"id": product_id}}],
                }
            },
            "sort": [{"stats.adjusted_score_percentiles.subcategory_percentile": {"order": "desc", "missing": "_last"}}],
        }
    )
    response = _get_client().search(body)
    hits = (response.get("hits") or {}).get("hits") or []
    total = ((response.get("hits") or {}).get("total") or {}).get("value", len(hits))

    alt_cta_meta_by_id: Dict[str, Dict[str, Any]] = {}
    cards: list[Dict[str, Any]] = []
    for hit in hits:
        raw = hit.get("_source") or {}
        card = to_product_card(raw)
        cards.append(card)
        alt_id = str(card.get("id") or "").strip()
        if not alt_id:
            continue
        alt_cta_meta_by_id[alt_id] = {
            "visibility": raw.get("visibility", card.get("visibility", "visible")),
            "has_palm_oil": _has_palm_oil_ingredient(raw),
        }

    alternatives = apply_general_retrieval_rules(
        cards
    )
    return {
        "source_product": to_product_card(source),
        "alternatives": alternatives,
        "alt_cta_meta_by_id": alt_cta_meta_by_id,
        "subcategory": subcat,
        "total_in_subcategory": total,
    }
