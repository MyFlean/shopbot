# shopping_bot/product_transforms.py
"""
Product card and PDP transforms shared by Search V2 routes and HTTP product APIs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple
import re

from .utils.pdp_tag_labels import label_for_tag_id
from .utils.cards_config import (
    CARD_STATS_REGISTRY,
    SCORE_CARD_BUILD_ORDER,
    allowed_score_keys_from_config,
    apply_order_from_config,
    get_subcategory_cards_config_for_path,
    score_key_meta_from_config,
)


HIDDEN_VISIBILITY = {"hardstop"}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

def _clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    s = TAG_RE.sub("", s)
    s = WS_RE.sub(" ", s).strip()
    return s

def _extract_protein(src: Dict[str, Any]) -> Optional[float]:
    try:
        v = (
            src.get("category_data", {})
            .get("nutritional", {}) 
            .get("nutri_breakdown", {})
            .get("protein_g")
        )
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None

def _get_best_image(hero: Dict[str, Any]) -> Optional[str]:
    if not isinstance(hero, dict):
        return None
    # Try standard resolutions first
    for size in ["640", "750", "828", "1080", "256", "384"]:
        if hero.get(size):
            return hero[size]
    # Fallback to any available image
    for v in hero.values():
        if isinstance(v, str) and v.strip():
            return v
    return None

def _extract_highlight(hit: Dict[str, Any]) -> Optional[str]:
    hl = hit.get("highlight", {})
    for field in ["name", "package_claims.dietary_labels", "ingredients.raw_text"]:
        if field in hl and hl[field]:
            return _clean_text(hl[field][0])
    return None


# ============================================================================
# Shared Product Transformers (used by ALL Flutter APIs)
# ============================================================================

def _extract_nutrition_from_source(src: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Extract nutritional values from raw ES _source, handling field name variants."""
    nutritional_data = src.get("category_data", {}).get("nutritional", {})
    nutrition = nutritional_data.get("nutri_breakdown_updated", {}) or nutritional_data.get("nutri_breakdown", {})

    def _safe_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    return {
        "protein_g": _safe_float(nutrition.get("protein g") or nutrition.get("protein_g")),
        "carbs_g": _safe_float(nutrition.get("carbohydrates g") or nutrition.get("carbs g") or nutrition.get("carbs_g")),
        "fat_g": _safe_float(nutrition.get("total fat g") or nutrition.get("fat g") or nutrition.get("fat_g")),
        "fiber_g": _safe_float(nutrition.get("fiber g") or nutrition.get("fiber_g")),
        "calories": _safe_float(nutrition.get("energy kcal") or nutrition.get("energy_kcal")),
        "sugar_g": _safe_float(nutrition.get("total sugar g") or nutrition.get("sugar g") or nutrition.get("sugar_g")),
        "saturated_fat_g": _safe_float(nutrition.get("saturated fat g") or nutrition.get("saturated_fat_g")),
        "sodium_mg": _safe_float(nutrition.get("sodium mg") or nutrition.get("sodium_mg")),
        "cholesterol_mg": _safe_float(nutrition.get("cholesterol mg") or nutrition.get("cholesterol_mg")),
        "calcium_mg": _safe_float(nutrition.get("calcium mg") or nutrition.get("calcium_mg")),
        "trans_fat_g": _safe_float(nutrition.get("trans fat g") or nutrition.get("trans_fat_g")),
    }


def _get_raw_nutri_breakdown(src: Dict[str, Any]) -> Dict[str, Any]:
    """Return raw nutrient map with updated breakdown taking precedence."""
    nutritional_data = src.get("category_data", {}).get("nutritional", {})
    if not isinstance(nutritional_data, dict):
        return {}
    updated = nutritional_data.get("nutri_breakdown_updated")
    if isinstance(updated, dict) and updated:
        return updated
    fallback = nutritional_data.get("nutri_breakdown")
    if isinstance(fallback, dict):
        return fallback
    return {}


def _format_nutrition_label_and_unit(raw_key: str) -> Tuple[str, str]:
    """Convert ES nutrient key like 'saturated fat g' to ('Saturated Fat', 'g')."""
    key = str(raw_key or "").strip().replace("_", " ")
    if not key:
        return "", ""

    parts = [p for p in key.split() if p]
    if not parts:
        return "", ""

    unit_tokens = {"g", "mg", "mcg", "kcal"}
    unit = ""
    if parts[-1].lower() in unit_tokens:
        unit = parts[-1].lower()
        parts = parts[:-1]

    label = " ".join(parts).title() if parts else key.title()
    return label, unit


def _build_dynamic_nutrition_items(src: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build PDP nutrition items dynamically from ES nutri_breakdown fields."""
    breakdown = _get_raw_nutri_breakdown(src)
    items: List[Dict[str, str]] = []
    if not breakdown:
        return items

    for raw_key, raw_value in breakdown.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue

        label, unit = _format_nutrition_label_and_unit(str(raw_key))
        if not label:
            continue

        display_val = int(value) if value == int(value) else round(value, 2)
        value_text = f"{display_val} {unit}" if unit else str(display_val)
        items.append({"nutrient": label, "value": value_text})

    return items


def _generate_macro_tags(nutrition: Dict[str, Optional[float]], max_tags: int = 2) -> List[Dict[str, Any]]:
    """Generate top N macro tags sorted by value descending."""
    macro_config = [
        ("protein_g", "protein", "g", "{v} g Protein"),
        ("carbs_g", "carbs", "g", "{v} g Carbs"),
        ("fat_g", "fat", "g", "{v} g Fat"),
        ("calories", "calories", "kcal", "{v} kcal"),
    ]
    available = []
    for field, nutrient, unit, fmt in macro_config:
        val = nutrition.get(field)
        if val is not None and val > 0:
            display_val = round(val)
            available.append({
                "label": fmt.format(v=display_val),
                "nutrient": nutrient,
                "value": display_val,
                "unit": unit,
                "_sort": val,
            })
    available.sort(key=lambda x: x["_sort"], reverse=True)
    return [{k: v for k, v in tag.items() if k != "_sort"} for tag in available[:max_tags]]


def _copy_if_present(src: Dict[str, Any], dest: Dict[str, Any], key: str) -> None:
    """Copy `key` only when source explicitly contains it."""
    if key in src:
        dest[key] = src.get(key)


def _normalize_variant_entries(raw_variants: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_variants, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw_variants:
        if not isinstance(item, dict):
            continue
        variant_id = str(item.get("id") or "").strip()
        if not variant_id:
            continue
        row: Dict[str, Any] = {"id": variant_id}
        if item.get("price") is not None:
            row["price"] = item.get("price")
        if item.get("mrp") is not None:
            row["mrp"] = item.get("mrp")
        size = str(item.get("size") or "").strip()
        if size:
            row["size"] = size
        image = str(item.get("image") or "").strip()
        if image:
            row["image"] = image
        out.append(row)
    return out


def transform_to_product_card(src: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Shared transformer: any product dict → standardized product card.

    Returns ``None`` for products whose visibility is in HIDDEN_VISIBILITY
    (e.g. "hardstop"), so callers must filter out None values.

    Handles **two input formats**:
      1. Raw ES ``_source`` (from search_by_ids, search_by_subcategory, get_product_by_id)
         — has nested ``category_data``, ``hero_image``, ``flean_score`` dict, ``stats``.
      2. Pre-transformed dict (from ``_transform_results`` / ``search()``)
         — has flat ``protein_g``, ``image``, ``flean_score`` number, ``flean_percentile``.

    Used by search, catalogue, scanner, best-selling, curated, and alternatives
    so that the Flutter developer always receives the same JSON shape for
    product listings / grids.
    """
    vis = src.get("visibility", "visible")
    if vis in HIDDEN_VISIBILITY:
        return None

    # Detect format: raw _source has 'category_data'; pre-transformed does not.
    is_raw_source = "category_data" in src

    if is_raw_source:
        nutritional_data = src.get("category_data", {}).get("nutritional", {})
        nutrition = _extract_nutrition_from_source(src)
        qty = nutritional_data.get("qty", "")
        images = src.get("images")
        image_url = images[0] if isinstance(images, list) and images else ""
        flean_score_data = src.get("flean_score", {})
        # Match PDP badge scoring semantics:
        # - prefer adjusted_score_label (numeric) when present
        # - else fall back to adjusted_score scaled to a 0–10 score (divide by 10)
        flean_score = None
        if isinstance(flean_score_data, dict):
            flean_score = _parse_flean_badge_score_double(flean_score_data.get("adjusted_score_label"))
        if flean_score is None:
            _adj = flean_score_data.get("adjusted_score") if isinstance(flean_score_data, dict) else flean_score_data
            _adj_val = _parse_flean_badge_score_double(_adj)
            if _adj_val is not None:
                flean_score = _adj_val / 10.0
        if flean_score is not None:
            flean_score = _round_flean_score_whole(flean_score)
        stats = src.get("stats", {})
        flean_percentile = None
        if stats.get("adjusted_score_percentiles"):
            flean_percentile = stats["adjusted_score_percentiles"].get("subcategory_percentile")
    else:
        # Pre-transformed format from _transform_results
        nutrition = {
            "protein_g": src.get("protein_g"),
            "carbs_g": src.get("carbs_g"),
            "fat_g": src.get("fat_g"),
            "fiber_g": src.get("fiber_g"),
            "calories": src.get("calories"),
        }
        qty = src.get("qty", "")
        image_url = src.get("image", "") or ""
        # Pre-transformed cards usually carry numeric flean_score; normalize to 0–10
        _fs = _parse_flean_badge_score_double(src.get("flean_score"))
        if _fs is not None and _fs > 10.0:
            flean_score = _fs / 10.0
        else:
            flean_score = _fs
        if flean_score is not None:
            flean_score = _round_flean_score_whole(flean_score)
        flean_percentile = src.get("flean_percentile")

    macro_tags = _generate_macro_tags(nutrition)
    nutrition_clean = {k: v for k, v in nutrition.items() if v is not None}

    card = {
        "id": src.get("id", ""),
        "parent_id": src.get("parent_id") or src.get("id", ""),
        "name": _clean_text(src.get("name", "")) or "",
        "brand": src.get("brand", ""),
        "price": src.get("price"),
        "mrp": src.get("mrp"),
        "currency": "INR",
        "qty": qty,
        "size": src.get("size", ""),
        "visibility": src.get("visibility", "visible"),
        "image_url": image_url,
        "macro_tags": macro_tags,
        "nutrition": nutrition_clean if nutrition_clean else None,
        "flean_score": flean_score,
        "flean_percentile": flean_percentile,
        "in_stock": True,
        "variants": _normalize_variant_entries(src.get("variants")),
    }
    _copy_if_present(src, card, "scheduled")
    return card


SCORE_TIERS: List[Dict[str, Any]] = [
    {"min": 90, "status": "elite",   "label": "Best",  "color": "#81A18C"},
    {"min": 75, "status": "top",     "label": "Top",       "color": "#8FAF9A2E"},
    {"min": 50, "status": "average", "label": "Average",  "color": "#F2E9BB80"},
    {"min": 25, "status": "subpar",  "label": "Poor",        "color": "#FFF3EF"},
    {"min": 0,  "status": "villain", "label": "Worst",     "color": "#EF4444"},
]

BONUS_SCORE_TIERS: List[Dict[str, Any]] = [
    {"min": 90, "status": "elite", "label": "High", "color": "#81A18C"},
    {"min": 75, "status": "top", "label": "Good", "color": "#8FAF9A2E"},
    {"min": 50, "status": "average", "label": "Average", "color": "#F2E9BB80"},
    {"min": 25, "status": "subpar", "label": "Poor", "color": "#FFF3EF"},
    {"min": 0, "status": "villain", "label": "Sub-Par", "color": "#EF4444"},
]

PENALTY_SCORE_TIERS: List[Dict[str, Any]] = [
    {"min": 90, "status": "elite", "label": "Very Low", "color": "#81A18C"},
    {"min": 75, "status": "top", "label": "Low", "color": "#8FAF9A2E"},
    {"min": 50, "status": "average", "label": "Present", "color": "#F2E9BB80"},
    {"min": 25, "status": "subpar", "label": "High", "color": "#FFF3EF"},
    {"min": 0, "status": "villain", "label": "Very High", "color": "#EF4444"},
]

SCORE_CARD_ICONS: Dict[str, str] = {
    "flean_rank": "https://img.flean.ai/assets/Pdp-Icons/01.svg",
    "protein":    "https://img.flean.ai/assets/Pdp-Icons/02.svg",
    "fiber":      "https://img.flean.ai/assets/Pdp-Icons/fiber1.svg",
    "sweeteners": "https://img.flean.ai/assets/Pdp-Icons/03.svg",
    "oils":       "https://img.flean.ai/assets/Pdp-Icons/04.svg",
    "calories":   "https://img.flean.ai/assets/Pdp-Icons/06.svg",
    "preservatives": "https://img.flean.ai/assets/Pdp-Icons/preservatives1.svg",
    "additives": "https://img.flean.ai/assets/Pdp-Icons/additives1.svg",
    "natural_sugar": "https://img.flean.ai/assets/Pdp-Icons/03.svg",
    "glycemic_index": "https://img.flean.ai/assets/Pdp-Icons/gi1.svg",
    "hydration": "https://img.flean.ai/assets/Pdp-Icons/hydration1.svg",
    "vitamins_minerals": "https://img.flean.ai/assets/Pdp-Icons/vitamin1.svg",
    "antioxidants": "https://img.flean.ai/assets/Pdp-Icons/antioxidant1.svg",
    "gut_health": "https://img.flean.ai/assets/Pdp-Icons/gut1.svg",
}

_LEGACY_HIGHLIGHT_TAGS: Dict[str, str] = {
    "protein": "protein_tags",
    "fiber": "carbs_fiber_tags",
    "sweeteners": "sweetners_sugar_tags",
    "oils": "oils_fats_tags",
    "calories": "energy_tags",
}

# ingredients_tags domain sets and worst-wins tier rules (status, value, tag_ids).
_INGREDIENTS_TIER_RULE = Tuple[str, str, FrozenSet[str]]

ADDITIVES_TAG_IDS: FrozenSet[str] = frozenset({
    "no_additives",
    "no_harmful_additives",
    "ok_additives_present",
    "artificial_additives_present",
    "harmful_additives_present",
})

ADDITIVES_TIER_RULES: List[_INGREDIENTS_TIER_RULE] = [
    ("villain", "Harmful", frozenset({"harmful_additives_present"})),
    ("subpar", "Caution", frozenset({"artificial_additives_present"})),
    ("average", "Safe", frozenset({"ok_additives_present"})),
    ("elite", "None", frozenset({"no_additives", "no_harmful_additives"})),
]

PRESERVATIVES_TAG_IDS: FrozenSet[str] = frozenset({
    "preservative_free",
    "ok_preservatives_present",
    "artificial_preservatives_present",
    "harmful_preservatives_present",
    "preservative_present",
})

PRESERVATIVES_TIER_RULES: List[_INGREDIENTS_TIER_RULE] = [
    ("villain", "Harmful", frozenset({"harmful_preservatives_present", "preservative_present"})),
    ("subpar", "Caution", frozenset({"artificial_preservatives_present"})),
    ("average", "Safe", frozenset({"ok_preservatives_present"})),
    ("elite", "None", frozenset({"preservative_free"})),
]

_GLYCEMIC_INDEX_TAG_IDS: FrozenSet[str] = frozenset({"low_gi", "medium_gi", "high_gi"})
_GLYCEMIC_INDEX_VALUE_BY_TAG: Dict[str, str] = {
    "high_gi": "High",
    "medium_gi": "Medium",
    "low_gi": "Low",
}
_GLYCEMIC_INDEX_TAG_PRIORITY: Tuple[str, ...] = ("high_gi", "medium_gi", "low_gi")
_GLYCEMIC_INDEX_STATUS_BY_VALUE: Dict[str, str] = {
    "Low": "elite",
    "Medium": "average",
    "High": "subpar",
}

_HYDRATION_TAG_IDS: FrozenSet[str] = frozenset({"hydrating"})
_HYDRATION_VALUE = "High"
_HYDRATION_STATUS = "elite"

_GUT_HEALTH_VALUE_BY_BUCKET: Dict[str, str] = {
    "negative": "Poor",
    "neutral": "Average",
    "positive": "Good",
}
_GUT_HEALTH_BUCKET_PRIORITY: Tuple[str, ...] = ("negative", "neutral", "positive")
_GUT_HEALTH_STATUS_BY_VALUE: Dict[str, str] = {
    "Good": "elite",
    "Average": "average",
    "Poor": "subpar",
}
_SENTIMENT_HIGHLIGHT_VALUE_BY_BUCKET = _GUT_HEALTH_VALUE_BY_BUCKET
_SENTIMENT_HIGHLIGHT_BUCKET_PRIORITY = _GUT_HEALTH_BUCKET_PRIORITY
_SENTIMENT_HIGHLIGHT_STATUS_BY_VALUE = _GUT_HEALTH_STATUS_BY_VALUE


_SCORE_TIER_BY_STATUS: Dict[str, Dict[str, Any]] = {t["status"]: t for t in SCORE_TIERS}


def _get_score_tier_from_table(
    percentile: float,
    tiers: List[Dict[str, Any]],
) -> Dict[str, str]:
    for tier in tiers:
        if percentile >= tier["min"]:
            return _tier_to_card_fields(tier)
    return _tier_to_card_fields(tiers[-1])


def _get_score_tier(percentile: float) -> Dict[str, str]:
    """Return status, label, color, and theme for a given percentile (5-tier system)."""
    return _get_score_tier_from_table(percentile, SCORE_TIERS)


def _tier_to_card_fields(tier: Dict[str, Any]) -> Dict[str, str]:
    status = str(tier["status"])
    return {
        "status": status,
        "label": tier["label"],
        "color": tier["color"],
        "theme": status,
    }


# Sentiment colors for PDP score-card subtitles (subset of SCORE_TIERS palette).
_HIGHLIGHT_SUBTITLE_POSITIVE_HEX = "#2E7D32"  # top tier green
_HIGHLIGHT_SUBTITLE_NEUTRAL_HEX = "#B49A61"  # average grey
_HIGHLIGHT_SUBTITLE_NEGATIVE_HEX = "#C62828"  # villain red

def _normalize_processing_type(src: Dict[str, Any]) -> str:
    cd = src.get("category_data")
    if not isinstance(cd, dict):
        return ""
    raw = cd.get("processing_type")
    if raw is None:
        return ""
    return str(raw).strip().lower()


def _is_ultra_processed(src: Dict[str, Any]) -> bool:
    return _normalize_processing_type(src) == "ultra_processed"


def _ingredients_tags_group(highlight_root: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(highlight_root, dict):
        return {}
    group = highlight_root.get("ingredients_tags")
    return group if isinstance(group, dict) else {}


def _ingredients_tags_has_negative(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    return bool(_highlight_tag_ids_from_group(group, "negative"))


def _watch_outs_tier(has_negative: bool) -> Dict[str, str]:
    if has_negative:
        return _tier_to_card_fields(SCORE_TIERS[-1])
    subpar = _SCORE_TIER_BY_STATUS["subpar"]
    return {
        "status": "warning",
        "label": "Caution",
        "color": subpar["color"],
        "theme": subpar["status"],
    }


def _resolve_highlight_tags(src: Dict[str, Any]) -> Dict[str, Any]:
    """Return highlight_tags dict from ES _source (Mongo-synced shape), or {}."""
    cd = src.get("category_data")
    if isinstance(cd, dict):
        tags = cd.get("tags")
        if isinstance(tags, dict):
            ht = tags.get("highlight_tags")
            if isinstance(ht, dict) and ht:
                return ht
    root_tags = src.get("tags")
    if isinstance(root_tags, dict):
        ht = root_tags.get("highlight_tags")
        if isinstance(ht, dict) and ht:
            return ht
    ht = src.get("highlight_tags")
    if isinstance(ht, dict) and ht:
        return ht
    return {}


def _highlight_tag_ids_from_group(group: Any, bucket: str) -> List[str]:
    if not isinstance(group, dict):
        return []
    raw = group.get(bucket)
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()]


def _collect_ingredients_tag_ids(group: Any) -> Set[str]:
    if not isinstance(group, dict):
        return set()
    ids: Set[str] = set()
    for bucket in ("positive", "neutral", "negative"):
        ids.update(_highlight_tag_ids_from_group(group, bucket))
    return ids


def _resolve_ingredients_domain_card(
    group: Any,
    domain_ids: FrozenSet[str],
    tier_rules: List[_INGREDIENTS_TIER_RULE],
) -> Optional[Dict[str, str]]:
    """Worst-wins tier match among domain tag ids present in ingredients_tags."""
    present = _collect_ingredients_tag_ids(group) & domain_ids
    if not present:
        return None
    for status, value, rule_tags in tier_rules:
        if present & rule_tags:
            tier = _SCORE_TIER_BY_STATUS.get(status)
            if not tier:
                continue
            fields = _tier_to_card_fields(tier)
            fields["value"] = value
            return fields
    return None


def _subtitle_new_from_highlight_group(
    group: Any,
    include_buckets: Optional[FrozenSet[str]] = None,
    include_tag_ids: Optional[FrozenSet[str]] = None,
) -> Optional[List[Dict[str, str]]]:
    """
    Build subtitle_new entries from one highlight_tags group.

    Tags are ordered negative, then positive, then neutral. Each entry has
    tag_label (from config map) and color_code from its sentiment bucket.
    When include_buckets is set, only those sentiment buckets are included.
    When include_tag_ids is set, only those tag ids are included.
    """
    if not isinstance(group, dict):
        return None

    bucket_colors = {
        "negative": _HIGHLIGHT_SUBTITLE_NEGATIVE_HEX,
        "positive": _HIGHLIGHT_SUBTITLE_POSITIVE_HEX,
        "neutral": _HIGHLIGHT_SUBTITLE_NEUTRAL_HEX,
    }
    items: List[Dict[str, str]] = []
    for bucket, color in (
        ("negative", bucket_colors["negative"]),
        ("positive", bucket_colors["positive"]),
        ("neutral", bucket_colors["neutral"]),
    ):
        if include_buckets is not None and bucket not in include_buckets:
            continue
        for tag_id in _highlight_tag_ids_from_group(group, bucket):
            if include_tag_ids is not None and tag_id not in include_tag_ids:
                continue
            label = label_for_tag_id(tag_id)
            if label:
                items.append({"tag_label": label, "color_code": color})
    return items if items else None


def _apply_highlight_subtitle_to_card(
    card: Dict[str, Any],
    highlight_root: Dict[str, Any],
    group_key: str,
    include_buckets: Optional[FrozenSet[str]] = None,
    include_tag_ids: Optional[FrozenSet[str]] = None,
) -> None:
    """If highlight data exists, set subtitle_new only; legacy subtitle unchanged."""
    group = highlight_root.get(group_key) if isinstance(highlight_root, dict) else None
    subtitle_new = _subtitle_new_from_highlight_group(
        group,
        include_buckets=include_buckets,
        include_tag_ids=include_tag_ids,
    )
    if subtitle_new:
        card["subtitle_new"] = subtitle_new


def _build_ingredients_domain_card(
    group: Any,
    card_key: str,
    title: str,
    domain_ids: FrozenSet[str],
    tier_rules: List[_INGREDIENTS_TIER_RULE],
    stats: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    resolved = _resolve_ingredients_domain_card(group, domain_ids, tier_rules)
    if not resolved:
        return None

    pctile_key = f"{card_key}_penalty_percentiles"
    pctile = (stats.get(pctile_key) or {}).get("subcategory_percentile")

    card: Dict[str, Any] = {
        "title": title,
        "value": resolved["value"],
        "percentile": round(pctile, 1) if pctile is not None else None,
        "status": resolved["status"],
        "status_label": resolved["label"],
        "color": resolved["color"],
        "theme": resolved["theme"],
        "icon_url": SCORE_CARD_ICONS[card_key],
        "visible": True,
    }
    subtitle_new = _subtitle_new_from_highlight_group(group, include_tag_ids=domain_ids)
    if subtitle_new:
        card["subtitle_new"] = subtitle_new
    else:
        card["subtitle"] = title
    return card


_BADGE_SCORE_DENY = {"", "na", "n/a", "null", "-", "not detected"}


def _numeric_chars_for_float(s: str) -> str:
    return "".join(
        ch for ch in s if ("0" <= ch <= "9") or ch in ".+-eE"
    )


def _round_flean_score_whole(val: float) -> int:
    """Round score to nearest whole number using half-up semantics."""
    return int(val + 0.5) if val >= 0 else int(val - 0.5)


def _parse_flean_badge_score_double(val: Any) -> Optional[float]:
    """Parse ES label or numeric into a float for score normalization."""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if s in _BADGE_SCORE_DENY:
            return None
        s = _numeric_chars_for_float(s)
        if not s:
            return None
        try:
            return float(s)
        except (ValueError, OverflowError):
            return None
    return None


def _resolve_subcategory_path(src: Dict[str, Any]) -> str:
    cat_paths = src.get("category_paths", [])
    if not cat_paths:
        return ""
    longest_path = max(cat_paths, key=lambda p: len(str(p)) if p else 0)
    return str(longest_path).strip() if longest_path else ""


def _subcategory_percentile(stats: Dict[str, Any], stats_field: str) -> Optional[float]:
    raw = (stats.get(stats_field) or {}).get("subcategory_percentile")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _aggregate_subcategory_percentile(
    stats: Dict[str, Any],
    stats_fields: Tuple[str, ...],
    *,
    mode: str = "max",
) -> Optional[float]:
    values = [
        v
        for field in stats_fields
        if (v := _subcategory_percentile(stats, field)) is not None
    ]
    if not values:
        return None
    if mode == "max":
        return max(values)
    return min(values)


def _tier_from_highlight_group(group: Any) -> Dict[str, str]:
    if not isinstance(group, dict):
        return _tier_to_card_fields(_SCORE_TIER_BY_STATUS["average"])
    if _highlight_tag_ids_from_group(group, "negative"):
        return _tier_to_card_fields(_SCORE_TIER_BY_STATUS["villain"])
    if _highlight_tag_ids_from_group(group, "positive"):
        return _tier_to_card_fields(SCORE_TIERS[0])
    return _tier_to_card_fields(_SCORE_TIER_BY_STATUS["average"])


def _resolve_highlight_group_key(
    score_key: str,
    meta_by_key: Dict[str, Dict[str, Any]],
) -> str:
    if meta_by_key:
        return str((meta_by_key.get(score_key) or {}).get("highlight_tag") or "").strip()
    return _LEGACY_HIGHLIGHT_TAGS.get(score_key, "")


def _apply_card_highlight(
    card: Dict[str, Any],
    score_key: str,
    highlight_root: Dict[str, Any],
    meta_by_key: Dict[str, Dict[str, Any]],
) -> None:
    tag = _resolve_highlight_group_key(score_key, meta_by_key)
    if tag:
        _apply_highlight_subtitle_to_card(card, highlight_root, tag)


def _card_title(score_key: str, meta_by_key: Dict[str, Dict[str, Any]]) -> str:
    entry = meta_by_key.get(score_key) if meta_by_key else None
    if entry and entry.get("title"):
        return str(entry["title"])
    spec = CARD_STATS_REGISTRY.get(score_key) or {}
    default = spec.get("default_title")
    if default:
        return str(default)
    return score_key.replace("_", " ").title()


def _tier_for_percentile(pctile: float, tier_mode: str) -> Dict[str, str]:
    if tier_mode == "penalty":
        effective = round(100 - pctile, 1)
        return _get_score_tier_from_table(effective, PENALTY_SCORE_TIERS)
    return _get_score_tier_from_table(pctile, BONUS_SCORE_TIERS)


def _percentile_from_registry_spec(stats: Dict[str, Any], spec: Dict[str, Any]) -> Optional[float]:
    stats_fields = spec.get("stats_fields") or ()
    if spec.get("aggregate") == "max":
        return _aggregate_subcategory_percentile(stats, tuple(stats_fields), mode="max")
    if stats_fields:
        return _subcategory_percentile(stats, stats_fields[0])
    return None


def _build_percentile_card_from_registry(
    score_key: str,
    stats: Dict[str, Any],
    meta_by_key: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    spec = CARD_STATS_REGISTRY.get(score_key) or {}
    tier_mode = spec.get("tier_mode")
    if not tier_mode or tier_mode == "highlight_only":
        return None

    pctile = _percentile_from_registry_spec(stats, spec)
    if pctile is None:
        return None

    _tier = _tier_for_percentile(pctile, tier_mode)
    subtitle = str(spec.get("subtitle") or "Efficiency")
    if tier_mode == "penalty":
        subtitle = f"Percentile: {round(pctile)}"

    icon_url = SCORE_CARD_ICONS.get(score_key)
    card: Dict[str, Any] = {
        "title": _card_title(score_key, meta_by_key),
        "value": _tier["label"],
        "subtitle": subtitle,
        "percentile": round(pctile, 1),
        "status": _tier["status"],
        "status_label": _tier["label"],
        "color": _tier["color"],
        "theme": _tier["theme"],
        "visible": True,
    }
    if icon_url:
        card["icon_url"] = icon_url
    return card


def _build_highlight_card_from_registry(
    score_key: str,
    highlight_root: Dict[str, Any],
    meta_by_key: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    group_key = _resolve_highlight_group_key(score_key, meta_by_key)
    if not group_key:
        return None
    group = highlight_root.get(group_key) if isinstance(highlight_root, dict) else None
    if not isinstance(group, dict):
        return None
    if not any(_highlight_tag_ids_from_group(group, bucket) for bucket in ("positive", "neutral", "negative")):
        return None

    _tier = _tier_from_highlight_group(group)
    icon_url = SCORE_CARD_ICONS.get(score_key)
    card: Dict[str, Any] = {
        "title": _card_title(score_key, meta_by_key),
        "value": _tier["label"],
        "subtitle": "Efficiency",
        "percentile": None,
        "status": _tier["status"],
        "status_label": _tier["label"],
        "color": _tier["color"],
        "theme": _tier["theme"],
        "visible": True,
    }
    if icon_url:
        card["icon_url"] = icon_url
    return card


@dataclass
class _ScoreCardBuildContext:
    stats: Dict[str, Any]
    nutrition: Dict[str, Any]
    nutritional_data: Dict[str, Any]
    subcategory_label: str
    flean_percentile: Optional[float]
    highlight_root: Dict[str, Any]
    ingredients_group: Any
    show_watch_outs: bool
    meta_by_key: Dict[str, Dict[str, Any]]


def _build_calories_card(ctx: _ScoreCardBuildContext) -> Optional[Dict[str, Any]]:
    cal_raw = ctx.nutrition.get("calories")
    if cal_raw is None:
        return None

    cal_val = round(cal_raw)
    cal_basis = ctx.nutritional_data.get("qty", "100 g")
    calories_pctile = _subcategory_percentile(ctx.stats, "calories_penalty_percentiles")
    if calories_pctile is not None:
        _cal_tier = _get_score_tier(round(100 - calories_pctile, 1))
    else:
        _cal_tier = _tier_to_card_fields(_SCORE_TIER_BY_STATUS["average"])

    icon_url = SCORE_CARD_ICONS.get("calories")
    card: Dict[str, Any] = {
        "title": _card_title("calories", ctx.meta_by_key),
        "value": f"{cal_val} kcal/ {cal_basis}",
        "subtitle": cal_basis,
        "percentile": round(calories_pctile, 1) if calories_pctile is not None else None,
        "status": _cal_tier["status"],
        "status_label": _cal_tier["label"],
        "color": _cal_tier["color"],
        "theme": _cal_tier["theme"],
        "visible": True,
    }
    if icon_url:
        card["icon_url"] = icon_url
    return card


def _build_additives_card(ctx: _ScoreCardBuildContext) -> Optional[Dict[str, Any]]:
    spec = CARD_STATS_REGISTRY.get("additives") or {}
    pctile = _percentile_from_registry_spec(ctx.stats, spec)
    if pctile is None:
        return None
    _tier = _tier_for_percentile(pctile, spec.get("tier_mode", "penalty"))
    card: Dict[str, Any] = {
        "title": _card_title("additives", ctx.meta_by_key),
        "value": _tier["label"],
        "subtitle": f"Percentile: {round(pctile)}",
        "percentile": round(pctile, 1),
        "status": _tier["status"],
        "status_label": _tier["label"],
        "color": _tier["color"],
        "theme": _tier["theme"],
        "icon_url": SCORE_CARD_ICONS["additives"],
        "visible": True,
    }
    subtitle_new = _subtitle_new_from_highlight_group(
        ctx.ingredients_group, include_tag_ids=ADDITIVES_TAG_IDS
    )
    if subtitle_new:
        card["subtitle_new"] = subtitle_new
    return card


def _build_preservatives_card(ctx: _ScoreCardBuildContext) -> Optional[Dict[str, Any]]:
    return _build_ingredients_domain_card(
        ctx.ingredients_group,
        "preservatives",
        "Preservatives",
        PRESERVATIVES_TAG_IDS,
        PRESERVATIVES_TIER_RULES,
        ctx.stats,
    )


def _build_watch_outs_card(ctx: _ScoreCardBuildContext) -> Optional[Dict[str, Any]]:
    if not ctx.show_watch_outs:
        return None
    has_negative = _ingredients_tags_has_negative(ctx.ingredients_group)
    _wo_tier = _watch_outs_tier(has_negative)
    empty_food_pctile = (ctx.stats.get("empty_food_penalty_percentiles") or {}).get(
        "subcategory_percentile"
    )
    return {
        "title": _card_title("watch_outs", ctx.meta_by_key),
        "value": "Processed",
        "subtitle": "Ultra Processed",
        "percentile": round(empty_food_pctile, 1) if empty_food_pctile is not None else None,
        "status": _wo_tier["status"],
        "status_label": _wo_tier["label"],
        "color": _wo_tier["color"],
        "theme": _wo_tier["theme"],
        "visible": True,
    }


def _build_flean_rank_card(ctx: _ScoreCardBuildContext) -> Optional[Dict[str, Any]]:
    if ctx.flean_percentile is None:
        return None
    rank_pct = round(100 - ctx.flean_percentile, 1)
    _tier = _get_score_tier(ctx.flean_percentile)
    return {
        "title": _card_title("flean_rank", ctx.meta_by_key),
        "value": f"Top {rank_pct}%",
        "subtitle": ctx.subcategory_label,
        "percentile": round(ctx.flean_percentile, 1),
        "status": _tier["status"],
        "status_label": _tier["label"],
        "color": _tier["color"],
        "theme": _tier["theme"],
        "icon_url": SCORE_CARD_ICONS["flean_rank"],
    }


def _resolve_glycemic_index_value(group: Any) -> Optional[str]:
    present = _collect_ingredients_tag_ids(group) & _GLYCEMIC_INDEX_TAG_IDS
    if not present:
        return None
    for tag_id in _GLYCEMIC_INDEX_TAG_PRIORITY:
        if tag_id in present:
            return _GLYCEMIC_INDEX_VALUE_BY_TAG[tag_id]
    return None


def _glycemic_index_tier_for_value(value: str) -> Dict[str, str]:
    status = _GLYCEMIC_INDEX_STATUS_BY_VALUE.get(value, "average")
    tier = _SCORE_TIER_BY_STATUS.get(status) or _SCORE_TIER_BY_STATUS["average"]
    fields = _tier_to_card_fields(tier)
    fields["value"] = value
    return fields


def _build_glycemic_index_card(ctx: _ScoreCardBuildContext) -> Optional[Dict[str, Any]]:
    group_key = _resolve_highlight_group_key("glycemic_index", ctx.meta_by_key)
    if not group_key:
        return None
    group = ctx.highlight_root.get(group_key) if isinstance(ctx.highlight_root, dict) else None
    value = _resolve_glycemic_index_value(group)
    if not value:
        return None
    resolved = _glycemic_index_tier_for_value(value)
    return {
        "title": _card_title("glycemic_index", ctx.meta_by_key),
        "value": resolved["value"],
        "subtitle": "Efficiency",
        "percentile": None,
        "status": resolved["status"],
        "status_label": resolved["value"],
        "color": resolved["color"],
        "theme": resolved["theme"],
        "icon_url": SCORE_CARD_ICONS["glycemic_index"],
        "visible": True,
    }


def _has_hydration_tag(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    return bool(_collect_ingredients_tag_ids(group) & _HYDRATION_TAG_IDS)


def _hydration_tier_for_value() -> Dict[str, str]:
    tier = _SCORE_TIER_BY_STATUS.get(_HYDRATION_STATUS) or _SCORE_TIER_BY_STATUS["average"]
    fields = _tier_to_card_fields(tier)
    fields["value"] = _HYDRATION_VALUE
    return fields


def _build_hydration_card(ctx: _ScoreCardBuildContext) -> Optional[Dict[str, Any]]:
    group_key = _resolve_highlight_group_key("hydration", ctx.meta_by_key)
    if not group_key:
        return None
    group = ctx.highlight_root.get(group_key) if isinstance(ctx.highlight_root, dict) else None
    if not _has_hydration_tag(group):
        return None
    resolved = _hydration_tier_for_value()
    return {
        "title": _card_title("hydration", ctx.meta_by_key),
        "value": resolved["value"],
        "subtitle": "Efficiency",
        "percentile": None,
        "status": resolved["status"],
        "status_label": resolved["value"],
        "color": resolved["color"],
        "theme": resolved["theme"],
        "icon_url": SCORE_CARD_ICONS["hydration"],
        "visible": True,
    }


def _resolve_sentiment_highlight_value(group: Any) -> Optional[str]:
    if not isinstance(group, dict):
        return None
    for bucket in _SENTIMENT_HIGHLIGHT_BUCKET_PRIORITY:
        if _highlight_tag_ids_from_group(group, bucket):
            return _SENTIMENT_HIGHLIGHT_VALUE_BY_BUCKET[bucket]
    return None


def _sentiment_highlight_tier_for_value(value: str) -> Dict[str, str]:
    status = _SENTIMENT_HIGHLIGHT_STATUS_BY_VALUE.get(value, "average")
    tier = _SCORE_TIER_BY_STATUS.get(status) or _SCORE_TIER_BY_STATUS["average"]
    fields = _tier_to_card_fields(tier)
    fields["value"] = value
    return fields


def _build_sentiment_highlight_card(
    score_key: str,
    ctx: _ScoreCardBuildContext,
) -> Optional[Dict[str, Any]]:
    group_key = _resolve_highlight_group_key(score_key, ctx.meta_by_key)
    if not group_key:
        return None
    group = ctx.highlight_root.get(group_key) if isinstance(ctx.highlight_root, dict) else None
    value = _resolve_sentiment_highlight_value(group)
    if not value:
        return None
    resolved = _sentiment_highlight_tier_for_value(value)
    icon_url = SCORE_CARD_ICONS.get(score_key)
    card: Dict[str, Any] = {
        "title": _card_title(score_key, ctx.meta_by_key),
        "value": resolved["value"],
        "subtitle": "Efficiency",
        "percentile": None,
        "status": resolved["status"],
        "status_label": resolved["value"],
        "color": resolved["color"],
        "theme": resolved["theme"],
        "visible": True,
    }
    if icon_url:
        card["icon_url"] = icon_url
    return card


def _build_score_card(
    score_key: str,
    ctx: _ScoreCardBuildContext,
    built: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    spec = CARD_STATS_REGISTRY.get(score_key)
    if not spec:
        return None

    build_type = spec.get("build_type", "percentile")
    if build_type == "watch_outs":
        return _build_watch_outs_card(ctx)
    if build_type == "flean_rank":
        if "watch_outs" in built:
            return None
        return _build_flean_rank_card(ctx)
    if build_type == "additives":
        return _build_additives_card(ctx)
    if build_type == "preservatives":
        return _build_preservatives_card(ctx)
    if build_type == "glycemic_index":
        return _build_glycemic_index_card(ctx)
    if build_type == "hydration":
        return _build_hydration_card(ctx)
    if build_type == "calories":
        return _build_calories_card(ctx)
    if build_type == "sentiment_highlight":
        return _build_sentiment_highlight_card(score_key, ctx)
    if build_type == "highlight_only":
        return _build_highlight_card_from_registry(score_key, ctx.highlight_root, ctx.meta_by_key)
    if build_type == "percentile":
        return _build_percentile_card_from_registry(score_key, ctx.stats, ctx.meta_by_key)
    return None


def _build_score_cards(
    src: Dict[str, Any],
    *,
    subcategory_label: str,
    flean_percentile: Optional[float],
    stats: Dict[str, Any],
    nutrition: Dict[str, Any],
    nutritional_data: Dict[str, Any],
    allowed_keys: Optional[FrozenSet[str]] = None,
    cards_config: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    def _should_build(key: str) -> bool:
        return allowed_keys is None or key in allowed_keys

    meta_by_key = score_key_meta_from_config(cards_config) if cards_config else {}
    highlight_root = _resolve_highlight_tags(src)
    ctx = _ScoreCardBuildContext(
        stats=stats,
        nutrition=nutrition,
        nutritional_data=nutritional_data,
        subcategory_label=subcategory_label,
        flean_percentile=flean_percentile,
        highlight_root=highlight_root,
        ingredients_group=_ingredients_tags_group(highlight_root),
        show_watch_outs=_is_ultra_processed(src),
        meta_by_key=meta_by_key,
    )

    score_cards: Dict[str, Any] = {}
    for score_key in SCORE_CARD_BUILD_ORDER:
        if not _should_build(score_key):
            continue
        card = _build_score_card(score_key, ctx, score_cards)
        if not card:
            continue
        score_cards[score_key] = card
        if score_key != "preservatives":
            _apply_card_highlight(card, score_key, highlight_root, meta_by_key)

    return score_cards


def transform_to_pdp(src: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full PDP transformer: raw ES _source → optimized key-value PDP data.

    Optimized for Flutter developer ease-of-use:
    - Direct key access (score_cards.protein.value instead of array filtering)
    - Ready-to-render labels matching Figma exactly
    - Pre-formatted display values
    - Status fields for color coding
    """
    # ── Extract raw data ──
    images = src.get("images")
    nutritional_data = src.get("category_data", {}).get("nutritional", {})
    nutrition = _extract_nutrition_from_source(src)
    stats = src.get("stats", {})
    package_claims = src.get("package_claims", {}) or {}
    flean_score_data = src.get("flean_score", {}) or {}

    # Derive category / subcategory from category_paths
    cat_paths = src.get("category_paths", [])
    subcategory_label = ""
    category_label = ""
    if cat_paths:
        longest_path = max(cat_paths, key=lambda p: len(str(p)) if p else 0)
        segments = str(longest_path).split("/") if longest_path else []
        subcategory_label = segments[-1].replace("_", " ").title() if segments else ""
        category_label = segments[-2].replace("_", " ").title() if len(segments) >= 2 else ""

    # Listing / PDP in_stock: ES visibility (optional pincode+Redis override in product_api)
    _vis_norm = str(src.get("visibility", "visible") or "visible").strip().lower()
    in_stock = _vis_norm == "visible"

    # ── product_info ──
    product_info = {
        "id": src.get("id", ""),
        "parent_id": src.get("parent_id") or src.get("id", ""),
        "name": _clean_text(src.get("name", "")) or "",
        "brand": src.get("brand", ""),
        "price": src.get("price"),
        "mrp": src.get("mrp"),
        "currency": "INR",
        "image_url": images[0] if isinstance(images, list) and images else "",
        "image_urls": images[1:] if isinstance(images, list) and len(images) > 1 else [],
        "qty": nutritional_data.get("qty", ""),
        "size": src.get("size", ""),
        "visibility": src.get("visibility", "visible"),
        "description": _clean_text(src.get("description", "")) or "",
        "in_stock": in_stock,
        "category": category_label,
        "subcategory": subcategory_label,
        "variants": _normalize_variant_entries(src.get("variants")),
    }
    _copy_if_present(src, product_info, "scheduled")

    # ── flean_badge ──
    flean_percentile = None
    if stats.get("adjusted_score_percentiles"):
        flean_percentile = stats["adjusted_score_percentiles"].get("subcategory_percentile")

    # Derive level from percentile (5-tier)
    if flean_percentile is not None:
        _tier = _get_score_tier(flean_percentile)
        level = _tier["status"]
        level_text = _tier["label"]
        level_color = _tier["color"]
    else:
        level, level_text, level_color = "unknown", "Not Rated", "#6B7280"

    badge_score_double: Optional[int] = None
    badge_display_str: Optional[str] = None
    if isinstance(flean_score_data, dict):
        badge_score_raw = _parse_flean_badge_score_double(
            flean_score_data.get("adjusted_score_label")
        )
        if badge_score_raw is not None:
            badge_score_double = _round_flean_score_whole(badge_score_raw)
    if badge_score_double is not None:
        badge_display_str = str(badge_score_double)
    else:
        adj = (
            flean_score_data.get("adjusted_score")
            if isinstance(flean_score_data, dict)
            else flean_score_data
        )
        adj_val = _parse_flean_badge_score_double(adj)
        if adj_val is not None:
            # Fallback: derive the same 0-10 rounded score for value + display.
            badge_score_double = _round_flean_score_whole(adj_val / 10.0)
            badge_display_str = str(badge_score_double)
        else:
            badge_display_str = "N/A"

    flean_badge = {
        "score": badge_score_double,
        "score_display": badge_display_str,
        "level": level,
        "level_text": level_text,
        "color": level_color,
    }

    # ── score_cards (config-driven: only build cards listed in Redis config) ──
    cards_config = get_subcategory_cards_config_for_path(_resolve_subcategory_path(src))
    build_kwargs = {
        "subcategory_label": subcategory_label,
        "flean_percentile": flean_percentile,
        "stats": stats,
        "nutrition": nutrition,
        "nutritional_data": nutritional_data,
    }
    if cards_config:
        allowed = allowed_score_keys_from_config(cards_config)
        score_cards = _build_score_cards(
            src,
            allowed_keys=allowed,
            cards_config=cards_config,
            **build_kwargs,
        )
        score_cards = apply_order_from_config(score_cards, cards_config)
    else:
        score_cards = _build_score_cards(src, **build_kwargs)

    # ── notes (static display notes for UI) ──
    notes = {
        "criteria_note": "Per 100 g labels reflect Flean Criteria.",
        "ranking_note": "Note: Overall ranking considers multiple factors. Individual warnings highlight specific concerns.",
    }

    # ── highlights (from real ES fields only) ──
    dietary_labels = package_claims.get("dietary_labels", [])
    health_claims = package_claims.get("health_claims", [])
    marketing_kw = package_claims.get("marketing_keywords", [])

    highlights_data = [
        ("Brand", src.get("brand", "")),
        ("Product Name", _clean_text(src.get("name", "")) or ""),
        ("Weight / Volume", nutritional_data.get("qty", "")),
        ("Category", category_label),
        ("Subcategory", subcategory_label),
        ("Dietary Preference", ", ".join(dietary_labels) if isinstance(dietary_labels, list) and dietary_labels else ""),
        ("Health Claims", ", ".join(health_claims) if isinstance(health_claims, list) and health_claims else ""),
        ("Marketing Keywords", ", ".join(marketing_kw) if isinstance(marketing_kw, list) and marketing_kw else ""),
    ]
    highlights = [{"label": label, "value": value} for label, value in highlights_data if value]

    # ── ingredients (prefer structured data; fallback to raw_text) ──
    ingredients_raw = src.get("ingredients", {}) or {}
    structured = ingredients_raw.get("structured", {}) if isinstance(ingredients_raw, dict) else {}
    structured_list = (structured.get("ingredients") or []) if isinstance(structured, dict) else []

    if structured_list:
        ingredients = []
        for ing in structured_list:
            if not isinstance(ing, dict):
                continue
            entry: Dict[str, Any] = {"name": ing.get("name", "")}
            if ing.get("percentage") is not None:
                entry["percentage"] = ing["percentage"]
            if ing.get("is_composite") and ing.get("components"):
                entry["components"] = [c.get("name", "") for c in ing["components"] if isinstance(c, dict) and c.get("name")]
            ingredients.append(entry)
        additives = structured.get("additives", []) or []
    else:
        ingredients_text = ""
        if isinstance(ingredients_raw, dict):
            ingredients_text = _clean_text(ingredients_raw.get("raw_text", "")) or ""
        elif isinstance(ingredients_raw, str):
            ingredients_text = _clean_text(ingredients_raw) or ""
        ingredients = [{"name": i.strip()} for i in ingredients_text.replace("|", ",").split(",") if i.strip()] if ingredients_text else []
        additives = []

    # ── nutrition (dynamic from ES nutri_breakdown, with basis) ──
    nutri_items = _build_dynamic_nutrition_items(src)

    nutrition_section = {
        "basis": nutritional_data.get("qty", "per 100 g"),
        "items": nutri_items,
    }

    # ── additional_info (static defaults; manufacturer/seller data not in ES) ──
    additional_info = [
        {"label": "Disclaimer", "value": "Product packaging, specifications and information may change from time to time. Please refer to the product label for the most accurate and updated information."},
        {"label": "Country of Origin", "value": "India"},
    ]

    # ── scoring_detail (penalties, bonuses, RDA from flean_score) ──
    scoring_detail = {}
    if isinstance(flean_score_data, dict):
        penalties = flean_score_data.get("penalties", {}) or {}
        bonuses = flean_score_data.get("bonuses", {}) or {}
        rda_pct = flean_score_data.get("rda_pct", {}) or {}

        if penalties:
            scoring_detail["penalties"] = {k: v for k, v in penalties.items() if v is not None and v != 0}
        if bonuses:
            scoring_detail["bonuses"] = {k: v for k, v in bonuses.items() if v is not None and v != 0}
        if rda_pct:
            scoring_detail["rda_pct"] = {k: round(v, 1) for k, v in rda_pct.items() if v is not None}

        total_penalty = flean_score_data.get("total_penalty")
        total_bonus = flean_score_data.get("total_bonus")
        if total_penalty is not None:
            scoring_detail["total_penalty"] = round(total_penalty, 2)
        if total_bonus is not None:
            scoring_detail["total_bonus"] = round(total_bonus, 2)

    # ── cons_list (watch-out items from ES) ──
    cons_list = src.get("cons_list", []) or []

    return {
        "product_info": product_info,
        "flean_badge": flean_badge,
        "score_cards": score_cards,
        "scoring_detail": scoring_detail,
        "notes": notes,
        "highlights": highlights,
        "ingredients": ingredients,
        "additives": additives,
        "nutrition": nutrition_section,
        "cons_list": cons_list,
        "additional_info": additional_info,
        "macro_tags": _generate_macro_tags(nutrition),
    }
