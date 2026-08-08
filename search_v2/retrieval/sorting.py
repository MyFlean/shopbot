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

from search_v2.extension.shop_by_goal.constants import DERIVED_FIELD_SCRIPTS

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
    "fiber": [
        {
            "stats.fiber_percentiles.subcategory_percentile": {
                "order": "desc",
                "missing": "_last",
            }
        },
        {"_score": "desc"},
    ],
    "fat": [
        {
            "stats.total_fat_penalty_percentiles.subcategory_percentile": {
                "order": "asc",
                "missing": "_last",
            }
        },
        {"_score": "desc"},
    ],
}

# NOTE: "protein_desc"/"fiber_desc"/"fat_asc"/"flean_score_desc" are the
# ACTUAL sort_by values the public API accepts (see
# shopping_bot/routes/product_api.py's VALID_SORT_OPTIONS) — kept in sync
# with hybrid_search_orchestrator.py's _apply_post_fusion_sort aliases.
_ALIASES: Dict[str, str] = {
    "price": "price_asc",
    "price_low_to_high": "price_asc",
    "price_high_to_low": "price_desc",
    "highest_flean": "quality",
    "highest_protein": "protein",
    "lowest_sugar": "low_sugar",
    "newest": "relevance",
    "protein_desc": "protein",
    "fiber_desc": "fiber",
    "fibre_desc": "fiber",
    "fat_asc": "fat",
    "flean_score_desc": "quality",
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


def _derived_sort_script(field_name: str) -> str:
    base_script = DERIVED_FIELD_SCRIPTS.get(field_name)
    if not base_script:
        raise ValueError(f"Unsupported derived sort field '{field_name}'")
    return f"{base_script} return metric;"


def build_sort_clauses_from_order(
    sort_order: Optional[List[Dict[str, str]]],
) -> Optional[List[Dict[str, Any]]]:
    """Build dynamic multi-key OpenSearch sort clauses from goal sort_order."""
    if not sort_order:
        return None
    clauses: List[Dict[str, Any]] = []
    for entry in sort_order:
        field_name = str((entry or {}).get("field") or "").strip()
        order = str((entry or {}).get("order") or "").strip().lower()
        if not field_name or order not in {"asc", "desc"}:
            continue
        if field_name.startswith("derived."):
            clauses.append(
                {
                    "_script": {
                        "type": "number",
                        "order": order,
                        "script": {
                            "lang": "painless",
                            "source": _derived_sort_script(field_name),
                        },
                    }
                }
            )
        elif field_name == "_score":
            clauses.append({"_score": order})
        else:
            clauses.append({field_name: {"order": order, "missing": "_last"}})
    if not clauses:
        return None
    has_score_tiebreaker = any(isinstance(clause, dict) and "_score" in clause for clause in clauses)
    if not has_score_tiebreaker:
        clauses.append({"_score": "desc"})
    return clauses
