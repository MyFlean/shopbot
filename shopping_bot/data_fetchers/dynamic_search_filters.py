"""
Helpers for dynamic search filter aggregations and response mapping.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

# Existing search-screen filter metadata
FILTER_PRICE_ID = "filter_price"
FILTER_FLEAN_SCORE_ID = "filter_flean_score"
FILTER_DIETARY_ID = "filter_preferences"
FILTER_INGREDIENT_ID = "ingredient_preferences"
FILTER_NUTRITION_ID = "filter_nutrition"
FILTER_FLAVOUR_ID = "filter_flavour"
FILTER_DEPARTMENT_ID = "filter_department"
FILTER_CATEGORY_ID = "filter_category"
FILTER_SUBCATEGORY_ID = "filter_subcategory"

# ES keyword subfield for flavour facet aggregation and filter term clauses.
# Mapping: search_terms.filter_terms.flavours is text; terms aggs require .keyword.
FLAVOUR_FILTER_FIELD = "search_terms.filter_terms.flavours.keyword"
# Legacy root flavour keyword — used until search_terms is indexed on all products.
FLAVOUR_FILTER_FIELD_FALLBACK = "flavour.keyword"

_FLEAN_BUCKETS: List[Dict[str, Any]] = [
    {"key": "9_plus", "label_key": "9_plus", "label": "9+ (Excellent)", "value": 9},
    {"key": "8_plus", "label_key": "8_plus", "label": "8+ (Very Good)", "value": 8},
    {"key": "7_plus", "label_key": "7_plus", "label": "7+ (Good)", "value": 7},
]

_DIETARY_LABELS: Dict[str, Dict[str, str]] = {
    "dairy_free": {"id": "pref_dairy_free", "label_key": "dairy_free", "label": "Dairy Free"},
    "gluten_free": {"id": "pref_gluten_free", "label_key": "gluten_free", "label": "Gluten Free"},
    "nut_free": {"id": "pref_nut_free", "label_key": "nut_free", "label": "Nut Free"},
}

_EXCLUDED_FILTER_VALUES = {"pcos_friendly"}

_INGREDIENT_LABELS: Dict[str, Dict[str, str]] = {
    "no_palm_oil": {"id": "pref_no_palm_oil", "label_key": "no_palm_oil", "label": "No Palm Oil"},
    "no_added_sugar": {"id": "pref_no_added_sugar", "label_key": "no_added_sugar", "label": "No Added Sugar"},
    "no_harmful_additives": {
        "id": "pref_no_additives",
        "label_key": "no_harmful_additives",
        "label": "No Harmful Additives",
    },
    "preservative_free": {
        "id": "pref_no_preservatives",
        "label_key": "preservative_free",
        "label": "No Preservatives",
    },
    "no_maida": {"id": "pref_no_maida", "label_key": "no_maida", "label": "No Maida"},
}

_NUTRITION_BUCKETS: List[Dict[str, Any]] = [
    {
        "key": "high_protein",
        "id": "pref_high_protein",
        "labelKey": "high_protein",
        "label": "High Protein",
        "query": {"range": {"stats.protein_percentiles.subcategory_percentile": {"gte": 75}}},
    },
    {
        "key": "low_carb",
        "id": "pref_low_carb",
        "labelKey": "low_carb",
        "label": "Low Carb",
        "query": {"range": {"stats.carbs_penalty_percentiles.subcategory_percentile": {"lte": 50}}},
    },
    {
        "key": "high_fiber",
        "id": "pref_high_fiber",
        "labelKey": "high_fiber",
        "label": "High Fiber",
        "query": {"range": {"stats.fiber_percentiles.subcategory_percentile": {"gte": 75}}},
    },
    {
        "key": "low_sugar",
        "id": "pref_low_sugar",
        "labelKey": "low_sugar",
        "label": "Low Sugar",
        "query": {"range": {"stats.sugar_penalty_percentiles.subcategory_percentile": {"lte": 50}}},
    },
    {
        "key": "low_sodium",
        "id": "pref_low_sodium",
        "labelKey": "low_sodium",
        "label": "Low Sodium",
        "query": {"range": {"stats.sodium_penalty_percentiles.subcategory_percentile": {"lte": 50}}},
    },
    {
        "key": "low_fat",
        "id": "pref_low_fat",
        "labelKey": "low_fat",
        "label": "Low Fat",
        "query": {"range": {"stats.total_fat_penalty_percentiles.subcategory_percentile": {"lte": 50}}},
    },
]

_FLEAN_BADGE_SCRIPT = """
double badge = -1.0;
if (doc.containsKey('flean_score.adjusted_score_label')
    && !doc['flean_score.adjusted_score_label'].empty) {
    def rawBadge = doc['flean_score.adjusted_score_label'].value;
    if (rawBadge instanceof Number) {
        badge = ((Number) rawBadge).doubleValue();
    } else {
        try {
            badge = Double.parseDouble(rawBadge.toString());
        } catch (Exception ignored) {}
    }
}
if (badge < 0.0 && doc.containsKey('flean_score.adjusted_score')
    && !doc['flean_score.adjusted_score'].empty) {
    def rawAdjusted = doc['flean_score.adjusted_score'].value;
    double adjusted = -1.0;
    if (rawAdjusted instanceof Number) {
        adjusted = ((Number) rawAdjusted).doubleValue();
    } else {
        try {
            adjusted = Double.parseDouble(rawAdjusted.toString());
        } catch (Exception ignored) {}
    }
    if (adjusted >= 0.0) {
        badge = adjusted / 10.0;
    }
}
if (badge < 0.0) {
    return false;
}
double roundedBadge = Math.floor(badge + 0.5);
return roundedBadge >= params.min_badge;
"""


def _nice_step(raw_step: float) -> float:
    if raw_step <= 0:
        return 1.0
    exponent = math.floor(math.log10(raw_step))
    fraction = raw_step / (10 ** exponent)
    if fraction <= 1:
        nice_fraction = 1
    elif fraction <= 2:
        nice_fraction = 2
    elif fraction <= 5:
        nice_fraction = 5
    else:
        nice_fraction = 10
    return float(nice_fraction * (10 ** exponent))


def build_dynamic_price_ranges(
    min_price: Optional[float],
    max_price: Optional[float],
    target_buckets: int = 4,
) -> List[Dict[str, Any]]:
    """Return ES range definitions with deterministic keys."""
    if min_price is None or max_price is None:
        return []
    if min_price < 0:
        min_price = 0.0
    if max_price < min_price:
        return []

    if math.isclose(min_price, max_price):
        edge = int(math.floor(min_price))
        key = f"{edge}_{edge}"
        return [{"key": key, "from": float(edge), "to": float(edge + 1)}]

    bucket_count = 4 if target_buckets >= 4 else 3
    span = max_price - min_price
    raw_step = span / bucket_count
    step = max(1.0, _nice_step(raw_step))

    start = math.floor(min_price / step) * step
    end = math.ceil(max_price / step) * step
    if end <= start:
        end = start + step

    edges: List[float] = [start]
    while edges[-1] < end:
        next_edge = edges[-1] + step
        edges.append(next_edge)
        if len(edges) > 12:
            break

    while len(edges) - 1 > 4:
        step *= 2
        start = math.floor(min_price / step) * step
        end = math.ceil(max_price / step) * step
        edges = [start]
        while edges[-1] < end:
            edges.append(edges[-1] + step)
            if len(edges) > 12:
                break

    while len(edges) - 1 < 3:
        new_step = max(1.0, step / 2)
        if new_step == step:
            break
        step = new_step
        start = math.floor(min_price / step) * step
        end = math.ceil(max_price / step) * step
        if end <= start:
            end = start + step
        edges = [start]
        while edges[-1] < end:
            edges.append(edges[-1] + step)
            if len(edges) > 16:
                break
        if len(edges) - 1 >= 3:
            break

    ranges: List[Dict[str, Any]] = []
    for left, right in zip(edges[:-1], edges[1:]):
        left_i = int(round(left))
        right_i = int(round(right))
        if right_i <= left_i:
            continue
        key = f"{left_i}_{right_i - 1}"
        ranges.append({"key": key, "from": float(left_i), "to": float(right_i)})
    return ranges


def _humanize_slug(text: str) -> str:
    cleaned = re.sub(r"[_\-]+", " ", (text or "").strip()).strip()
    if not cleaned:
        return ""
    return " ".join(part.capitalize() for part in cleaned.split())


def build_hierarchy_filter_group(
    *,
    group_id: str,
    title: str,
    title_key: str,
    counts: Optional[Dict[str, int]],
    selected_values: Optional[List[str]] = None,
    label_lookup: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Build a department/category/subcategory facet group for data.filters."""
    counts = counts or {}
    selected = {
        str(value or "").strip().lower()
        for value in (selected_values or [])
        if str(value or "").strip()
    }
    labels = label_lookup or {}
    items: List[Dict[str, Any]] = []
    for key, count in counts.items():
        normalized = str(key or "").strip().lower()
        try:
            doc_count = int(count or 0)
        except (TypeError, ValueError):
            doc_count = 0
        if not normalized or doc_count <= 0:
            continue
        label = str(labels.get(normalized) or "").strip() or _humanize_slug(normalized)
        items.append(
            {
                "id": f"{title_key}_{normalized}",
                "labelKey": normalized,
                "label": label,
                "value": normalized,
                "count": doc_count,
                "isPreSelected": normalized in selected,
            }
        )
    if not items:
        return None
    items.sort(key=lambda item: str(item.get("label") or ""))
    return {
        "id": group_id,
        "title": title,
        "titleKey": title_key,
        "items": items,
    }


def _build_price_item(bucket: Dict[str, Any]) -> Dict[str, Any]:
    key = str(bucket.get("key", "")).strip()
    left, right = 0, 0
    if "_" in key:
        parts = key.split("_", 1)
        try:
            left = int(float(parts[0]))
            right = int(float(parts[1]))
        except (TypeError, ValueError):
            left, right = 0, 0
    label_key = key
    if left == 0:
        label = f"Below Rs {right + 1}"
        value = f"0-{right + 1}"
        item_id = f"price_below_{right + 1}"
    else:
        label = f"Rs {left} - Rs {right + 1}"
        value = f"{left}-{right + 1}"
        item_id = f"price_{left}_{right + 1}"
    return {
        "id": item_id,
        "labelKey": label_key,
        "label": label,
        "value": value,
        "count": int(bucket.get("doc_count", 0) or 0),
        "isPreSelected": False,
    }


def build_facet_aggregations(price_ranges: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build aggregation map for supported dynamic filters."""
    aggs: Dict[str, Any] = {
        "dietary_preferences": {
            "terms": {
                "field": "category_data.tags.dietary_tags.keyword",
                "size": 30,
                "min_doc_count": 1,
            }
        },
        "ingredient_preferences": {
            "terms": {
                "field": "category_data.tags.ingredient_tags.keyword",
                "size": 30,
                "min_doc_count": 1,
            }
        },
        "flavour_options": {
            "terms": {
                "field": FLAVOUR_FILTER_FIELD,
                "size": 30,
                "min_doc_count": 1,
            }
        },
        "flavour_options_legacy": {
            "terms": {
                "field": FLAVOUR_FILTER_FIELD_FALLBACK,
                "size": 30,
                "min_doc_count": 1,
            }
        },
    }
    if price_ranges:
        aggs["price_ranges"] = {"range": {"field": "price", "keyed": True, "ranges": price_ranges}}
    return aggs


def build_price_bounds_aggregation() -> Dict[str, Any]:
    return {
        "price_min": {"min": {"field": "price"}},
        "price_max": {"max": {"field": "price"}},
    }


def parse_dynamic_filters_from_aggs(aggregations: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    aggregations = aggregations or {}
    groups: List[Dict[str, Any]] = []

    price_items: List[Dict[str, Any]] = []
    price_buckets = ((aggregations.get("price_ranges") or {}).get("buckets") or {})
    if isinstance(price_buckets, dict):
        for bucket_key, bucket in price_buckets.items():
            if not isinstance(bucket, dict):
                continue
            if int(bucket.get("doc_count", 0) or 0) <= 0:
                continue
            bucket_with_key = dict(bucket)
            if not bucket_with_key.get("key"):
                bucket_with_key["key"] = str(bucket_key)
            price_items.append(_build_price_item(bucket_with_key))
    if price_items:
        groups.append(
            {
                "id": FILTER_PRICE_ID,
                "title": "Price",
                "titleKey": "price_range",
                "items": price_items,
            }
        )

    dietary_items: List[Dict[str, Any]] = []
    for bucket in ((aggregations.get("dietary_preferences") or {}).get("buckets") or []):
        if not isinstance(bucket, dict):
            continue
        key = str(bucket.get("key", "")).strip()
        count = int(bucket.get("doc_count", 0) or 0)
        if not key or count <= 0 or key in _EXCLUDED_FILTER_VALUES:
            continue
        metadata = _DIETARY_LABELS.get(key, {})
        dietary_items.append(
            {
                "id": metadata.get("id", f"pref_{key}"),
                "labelKey": metadata.get("label_key", key),
                "label": metadata.get("label", _humanize_slug(key)),
                "value": key,
                "count": count,
                "isPreSelected": False,
            }
        )
    ingredient_items: List[Dict[str, Any]] = []
    for bucket in ((aggregations.get("ingredient_preferences") or {}).get("buckets") or []):
        if not isinstance(bucket, dict):
            continue
        key = str(bucket.get("key", "")).strip()
        count = int(bucket.get("doc_count", 0) or 0)
        if not key or count <= 0 or key in _EXCLUDED_FILTER_VALUES:
            continue
        metadata = _INGREDIENT_LABELS.get(key, {})
        ingredient_items.append(
            {
                "id": metadata.get("id", f"pref_{key}"),
                "labelKey": metadata.get("label_key", key),
                "label": metadata.get("label", _humanize_slug(key)),
                "value": key,
                "count": count,
                "isPreSelected": False,
            }
        )
    if ingredient_items:
        groups.append(
            {
                "id": FILTER_INGREDIENT_ID,
                "title": "Ingredient Preferences",
                "titleKey": "ingredient_preferences",
                "items": ingredient_items,
            }
        )

    if dietary_items:
        groups.append(
            {
                "id": FILTER_DIETARY_ID,
                "title": "Dietary Preferences",
                "titleKey": "dietary_preferences",
                "items": dietary_items,
            }
        )

    flavour_items: List[Dict[str, Any]] = []
    flavour_buckets_raw = ((aggregations.get("flavour_options") or {}).get("buckets") or [])
    if not flavour_buckets_raw:
        flavour_buckets_raw = ((aggregations.get("flavour_options_legacy") or {}).get("buckets") or [])
    for bucket in flavour_buckets_raw:
        if not isinstance(bucket, dict):
            continue
        key = str(bucket.get("key", "")).strip()
        count = int(bucket.get("doc_count", 0) or 0)
        if not key or count <= 0:
            continue
        key_norm = key.lower()
        flavour_items.append(
            {
                "id": f"flavour_{key_norm.replace(' ', '_')}",
                "labelKey": key_norm.replace(" ", "_"),
                "label": _humanize_slug(key_norm),
                "value": key_norm,
                "count": count,
                "isPreSelected": False,
            }
        )
    if flavour_items:
        groups.append(
            {
                "id": FILTER_FLAVOUR_ID,
                "title": "Flavour",
                "titleKey": "flavour",
                "items": flavour_items,
            }
        )

    return groups
