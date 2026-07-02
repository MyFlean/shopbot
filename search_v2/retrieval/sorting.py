"""
search_v2/retrieval/sorting.py
────────────────────────────────
Sort clause builder for Search V2.

All sort specifications live here. Query builders call build_sort_clauses()
and include the result when a sort is requested. No sort (sort_by=None or
"relevance") returns None — OpenSearch defaults to _score ordering.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── Sort specifications ────────────────────────────────────────────────────────
# _score tiebreaker ensures equal-valued range items surface by relevance first.
SORT_SPECS: Dict[str, List[Dict[str, Any]]] = {
    "relevance": [],
    "price_asc": [
        {"price": {"order": "asc", "missing": "_last"}},
        {"_score": "desc"},
    ],
    "price_desc": [
        {"price": {"order": "desc", "missing": "_last"}},
        {"_score": "desc"},
    ],
    "quality": [
        {
            "stats.adjusted_score_percentiles.subcategory_percentile": {
                "order": "desc",
                "missing": "_last",
            }
        },
        {"_score": "desc"},
    ],
    "protein": [
        {
            "stats.protein_percentiles.subcategory_percentile": {
                "order": "desc",
                "missing": "_last",
            }
        },
        {"_score": "desc"},
    ],
    "low_sugar": [
        {
            "stats.sugar_penalty_percentiles.subcategory_percentile": {
                "order": "asc",
                "missing": "_last",
            }
        },
        {"_score": "desc"},
    ],
    "flean_score": [
        {
            "flean_score.adjusted_score": {
                "order": "desc",
                "missing": "_last",
            }
        },
        {"_score": "desc"},
    ],
}

_ALIASES: Dict[str, str] = {
    "price": "price_asc",
    "price_low_to_high": "price_asc",
    "price_high_to_low": "price_desc",
    "highest_flean": "quality",
    "highest_protein": "protein",
    "lowest_sugar": "low_sugar",
    "newest": "relevance",
}


def build_sort_clauses(sort_by: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """
    Return OpenSearch sort clauses for the given sort key, or None for
    relevance-based ordering (OpenSearch's default when sort is absent).
    """
    if not sort_by:
        return None
    key = str(sort_by).lower().strip()
    key = _ALIASES.get(key, key)
    clauses = SORT_SPECS.get(key)
    if not clauses:
        return None
    return clauses
