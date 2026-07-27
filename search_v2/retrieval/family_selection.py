"""
search_v2/retrieval/family_selection.py
────────────────────────────────────────
Pick one index document per product family when field collapse cannot be used
(e.g. top_hits inside aggregations).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

ScoreFn = Callable[[Dict[str, Any]], float]


def family_key(doc: Dict[str, Any]) -> str:
    """Stable family identifier: parent_id when present, else the variant id."""
    parent = str(doc.get("parent_id") or "").strip()
    if parent:
        return parent
    return str(doc.get("id") or "").strip()


def family_key_from_card(card: Dict[str, Any]) -> str:
    """Same as family_key for product-card dicts (parent_id / id)."""
    return family_key(card)


def default_flean_score_fn(doc: Dict[str, Any]) -> float:
    return float((doc.get("flean_score") or {}).get("adjusted_score") or 0.0)


def select_one_per_family(
    docs: List[Dict[str, Any]],
    *,
    score_fn: ScoreFn = default_flean_score_fn,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Keep the highest-scoring document per family_key, sorted by score descending."""
    best_by_family: Dict[str, tuple[float, Dict[str, Any]]] = {}
    for doc in docs:
        key = family_key(doc)
        if not key:
            continue
        score = score_fn(doc)
        prev = best_by_family.get(key)
        if prev is None or score > prev[0]:
            best_by_family[key] = (score, doc)

    ranked = sorted(best_by_family.values(), key=lambda item: item[0], reverse=True)
    selected = [doc for _, doc in ranked]
    if limit is not None:
        return selected[: max(0, limit)]
    return selected
