"""
search_v2/retrieval/listing.py
────────────────────────────────
Shared customer-facing product listing defaults for flat OpenSearch queries.

Used by category browse, curated, similar products, query search, and the
lexical/semantic/hybrid query builders (all import LISTING_COLLAPSE from here).

``apply_general_retrieval_rules()`` is the single implementation of the Fleen
Score < 6.0/10 demotion rule used by every retrieval endpoint.

Aggregation-based listings (best selling, flean picks) cannot use field
collapse on top_hits; they use family_selection.select_one_per_family() instead.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, TypeVar

T = TypeVar("T")

FLEAN_SCORE_DEMOTION_THRESHOLD = 6.0  # on 0–10 scale

LISTING_COLLAPSE_FIELD = "parent_id"
LISTING_COLLAPSE: Dict[str, str] = {"field": LISTING_COLLAPSE_FIELD}

LISTING_SOURCE_EXCLUDES: List[str] = [
    "text_vector",
    "text_vector_source",
    "vernacular_synonyms",
]

LISTING_VISIBILITY_FILTER: Dict[str, Any] = {
    "terms": {"visibility": ["visible", "soft"]},
}


def listing_visibility_filter_clause() -> Dict[str, Any]:
    """Filter clause: only customer-visible products."""
    return deepcopy(LISTING_VISIBILITY_FILTER)


def apply_flat_listing_defaults(
    body: Dict[str, Any],
    *,
    collapse: bool = True,
    source_excludes: bool = True,
) -> Dict[str, Any]:
    """Apply shared listing behaviour to a flat _search body (same request, no extra hop).

    - collapse on parent_id (one hit per product family)
    - trim heavy vector/synonym fields from _source when excludes are enabled
    """
    out = deepcopy(body)
    if collapse:
        out["collapse"] = deepcopy(LISTING_COLLAPSE)
    if source_excludes:
        source = out.get("_source")
        if source is None:
            out["_source"] = {"excludes": list(LISTING_SOURCE_EXCLUDES)}
        elif isinstance(source, dict):
            excludes = list(source.get("excludes") or [])
            for field in LISTING_SOURCE_EXCLUDES:
                if field not in excludes:
                    excludes.append(field)
            source["excludes"] = excludes
        elif source is True:
            out["_source"] = {"excludes": list(LISTING_SOURCE_EXCLUDES)}
    return out


def flean_score_on_10_scale(raw: Any) -> Optional[float]:
    """Normalize stored adjusted_score (0–100 or 0–10) to a 0–10 value."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 10.0:
        return value / 10.0
    return value


def flean_score_from_source(source: Dict[str, Any]) -> Any:
    """Raw adjusted_score from an OpenSearch _source document."""
    return (source.get("flean_score") or {}).get("adjusted_score")


def _card_flean_score(card: Dict[str, Any]) -> Any:
    return card.get("flean_score")


def apply_general_retrieval_rules(
    items: List[T],
    *,
    score_getter: Callable[[T], Any] = _card_flean_score,
    threshold: float = FLEAN_SCORE_DEMOTION_THRESHOLD,
) -> List[T]:
    """General retrieval rule for all customer-facing endpoints (search included).

    Products with Fleen Score < 6.0/10 are demoted below those >= 6.0/10.
    Nothing is filtered out. Relative order within each tier is preserved.
    If every item is below threshold, the list is returned unchanged.
    """
    high: List[T] = []
    low: List[T] = []
    for item in items:
        score = flean_score_on_10_scale(score_getter(item))
        if score is not None and score >= threshold:
            high.append(item)
        else:
            low.append(item)
    if not high:
        return items
    return high + low


def demote_below_flean_threshold(
    items: List[T],
    *,
    score_getter: Callable[[T], Any] = _card_flean_score,
    threshold: float = FLEAN_SCORE_DEMOTION_THRESHOLD,
) -> List[T]:
    """Alias for apply_general_retrieval_rules (kept for callers/tests)."""
    return apply_general_retrieval_rules(
        items, score_getter=score_getter, threshold=threshold
    )


def finalize_listing_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply post-ranking listing rules: general retrieval tier, then lab-tested pin."""
    return pin_lab_tested_to_top(apply_general_retrieval_rules(cards))


def pin_lab_tested_to_top(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Move lab-tested cards to the front; preserve relative order within each group.

    Operates on the final card list only — no score changes, no extra queries.
    """
    promoted, rest = [], []
    for card in cards:
        if card.get("has_lab_report"):
            promoted.append(card)
        else:
            rest.append(card)
    return promoted + rest if promoted else cards
