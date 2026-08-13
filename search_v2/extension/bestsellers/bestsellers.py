"""
Native Search V2 best-selling-by-category aggregation.

Single ES request: one `filters` aggregation bucket per category path, each
with a `top_hits` sub-aggregation sorted by flean_score.adjusted_score
descending. Category paths are exact keyword-field terms, matching
category_browsing/'s use of category_paths (see that module for why the
legacy .keyword suffix doesn't apply to this index).

Product-family dedup: top_hits cannot use field collapse, so raw hits are
passed through family_selection.select_one_per_family() before card selection.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from search_v2.config.settings import SETTINGS
from search_v2.extension.product import to_product_card
from search_v2.retrieval.family_selection import (
    default_flean_score_fn,
    family_key_from_card,
    select_one_per_family,
)
from search_v2.retrieval.listing import (
    apply_flat_listing_defaults,
    listing_visibility_filter_clause,
    finalize_listing_cards,
    preferred_listing_source_from_hit,
)
from search_v2.retrieval.opensearch_client import OpenSearchClient

_client: Optional[OpenSearchClient] = None
_LAB_TESTED_FETCH = 5


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def fetch_candidates_by_category(paths: List[str], fetch_per_category: int) -> Dict[str, List[Dict[str, Any]]]:
    """Returns {path: [raw _source docs...]}, one per product family, by flean score."""
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
        sources = [hit.get("_source") or {} for hit in hits]
        results[path] = select_one_per_family(sources, score_fn=default_flean_score_fn)
    return results


def fetch_lab_tested_candidates(
    limit: int = _LAB_TESTED_FETCH,
    category_paths: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Top lab-tested products by flean score (flat listing query with family collapse).

    When ``category_paths`` is non-empty, results are restricted to those paths.
    """
    if limit <= 0:
        return []

    filters: List[Dict[str, Any]] = [
        listing_visibility_filter_clause(),
        {"exists": {"field": "category_data.lab_reports.url"}},
    ]
    if category_paths:
        filters.append({"terms": {"category_paths": list(category_paths)}})

    body = apply_flat_listing_defaults(
        {
            "size": limit,
            "track_total_hits": False,
            "query": {
                "bool": {
                    "filter": filters
                }
            },
            "sort": [{"flean_score.adjusted_score": {"order": "desc", "missing": "_last"}}],
        }
    )
    response = _get_client().search(body)
    hits = response.get("hits", {}).get("hits", [])
    return [preferred_listing_source_from_hit(hit) for hit in hits if hit.get("_source")]


def best_selling(
    category_paths: List[str],
    per_category: int = 2,
    total_products: int = 6,
    fetch_buffer: int = 13,
) -> List[Dict[str, Any]]:
    fetch_per_category = per_category + fetch_buffer
    lab_cards = [
        to_product_card(src)
        for src in fetch_lab_tested_candidates(category_paths=category_paths)
        if src.get("id")
    ]
    candidates_by_path = fetch_candidates_by_category(category_paths, fetch_per_category)

    selected: List[Dict[str, Any]] = []
    selected_families: set = set()
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
            family = family_key_from_card(card)
            if not family or family in selected_families:
                continue
            if category_count < per_category:
                selected.append(card)
                selected_families.add(family)
                category_count += 1
            else:
                backfill.append((score, card))

    if len(selected) < total_products:
        backfill.sort(key=lambda item: item[0], reverse=True)
        for _, card in backfill:
            family = family_key_from_card(card)
            if not family or family in selected_families:
                continue
            selected.append(card)
            selected_families.add(family)
            if len(selected) >= total_products:
                break

    merged: List[Dict[str, Any]] = []
    merged_families: set = set()
    for card in lab_cards + selected:
        family = family_key_from_card(card)
        if not family or family in merged_families:
            continue
        merged.append(card)
        merged_families.add(family)

    return finalize_listing_cards(merged)[:total_products]
