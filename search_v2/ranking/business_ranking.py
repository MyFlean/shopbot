"""
search_v2/ranking/business_ranking.py
─────────────────────────────────────────
Independent post-retrieval business ranking — the brief is explicit: "Keep
retrieval and business ranking independent... Business ranking should refine
relevance, never replace it." This module has ZERO dependency on
retrieval/hybrid_search_orchestrator.py (only the reverse is true — callers
import both and pipe one into the other); ranking knows nothing about how
the relevance score it's refining was produced (lexical-only, semantic-only,
RRF, weighted, or native_hybrid all produce the same `fused_score` shape it
consumes identically).

Signals, all from the brief's checklist, each implemented as an independent,
pluggable rule contributing a BOUNDED multiplier component (same governing
principle as the bonuses/penalties this builds on — see scoring_rules.json):

  Flean score / nutrition  -> flean_nutrition_rule(), reuses the REAL existing
      business rules from shopping_bot/scoring_config.py's
      CATEGORY_SCORING_RULES (imported via scoring_rules_importer.py — AST
      extraction, not copy-paste, same drift-free reasoning as the synonym
      milestone's category_map/type_map reuse).
  Ratings                   -> ratings_rule()
  Review count               -> review_count_rule() (log-scaled — 1000 reviews
      shouldn't matter 10x more than 100; diminishing returns)
  Stock                      -> stock_rule()
  Freshness                  -> freshness_rule() (best-effort — see its
      docstring on schema uncertainty)
  Category priorities        -> category_priority_rule()
  Business rules (general)   -> the whole rule-list mechanism IS this — see
      DEFAULT_RULES and register_rule() for adding more without editing this
      file.

Every rule receives the raw `_source` document and returns a multiplier
already compressed toward 1.0 (mirrors prior hybrid implementation's scoring_config.py
approach) so that even with several rules stacking, apply_business_ranking()
clamping the PRODUCT to [BUSINESS_MIN_MULTIPLIER, BUSINESS_MAX_MULTIPLIER]
actually means something — a single unbounded rule could otherwise blow past
the clamp's intent before clamping even happens.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from search_v2.config.settings import SearchV2Settings, SETTINGS

DEFAULT_SCORING_RULES_PATH = Path(__file__).resolve().parent / "scoring_rules.json"

RuleFn = Callable[[Dict[str, Any], str, SearchV2Settings], float]


@dataclass
class RankedItem:
    doc_id: str
    source: Dict[str, Any]
    relevance_score: float
    business_multiplier: float
    final_score: float
    rule_breakdown: Dict[str, float] = field(default_factory=dict)
    lexical_rank: Optional[int] = None
    lexical_score: Optional[float] = None
    semantic_rank: Optional[int] = None
    semantic_score: Optional[float] = None


def _get_nested(source: Dict[str, Any], dotted_path: str) -> Any:
    value: Any = source
    for key in dotted_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


_scoring_rules_cache: Optional[Dict[str, Any]] = None


def load_scoring_rules(path: Path = DEFAULT_SCORING_RULES_PATH) -> Dict[str, Any]:
    global _scoring_rules_cache
    if _scoring_rules_cache is not None:
        return _scoring_rules_cache
    if not path.exists():
        _scoring_rules_cache = {"_default": {"bonuses": [], "penalties": []}}
        return _scoring_rules_cache
    _scoring_rules_cache = json.loads(path.read_text(encoding="utf-8"))
    return _scoring_rules_cache


def get_rules_for_subcategory(subcategory: str, rules_data: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
    rules_data = rules_data if rules_data is not None else load_scoring_rules()
    return rules_data.get(subcategory, rules_data.get("_default", {"bonuses": [], "penalties": []}))


# ── Individual rules ─────────────────────────────────────────────────────

def _symmetric_deviation_multiplier(deviation: float, settings: SearchV2Settings) -> float:
    """Map a deviation-from-neutral in [-1.0, +1.0] to a multiplier using the
    FULL configured business-ranking headroom on each side
    (BUSINESS_MAX_MULTIPLIER above neutral, BUSINESS_MIN_MULTIPLIER below) —
    settings-driven, not a hardcoded fraction of it, so the signal always
    uses exactly the headroom the team has vetted as safe (see
    apply_business_ranking()'s relevance-first guarantee), no more and no
    less, regardless of how those bounds are tuned later."""
    deviation = max(-1.0, min(1.0, deviation))
    if deviation >= 0:
        return 1.0 + deviation * (settings.BUSINESS_MAX_MULTIPLIER - 1.0)
    return 1.0 + deviation * (1.0 - settings.BUSINESS_MIN_MULTIPLIER)


def flean_nutrition_rule(source: Dict[str, Any], subcategory: str, settings: SearchV2Settings) -> float:
    """Reuses the real existing bonuses/penalties from
    shopping_bot/scoring_config.py (see scoring_rules_importer.py). Same
    compression formula prior hybrid implementation used: each rule's own weight is compressed
    halfway toward 1.0 so several stacking bonuses/penalties can't blow past
    the overall clamp before clamping even applies.

    Base term is SYMMETRIC around the 50th percentile (neutral) and spans
    the full configured clamp range in both directions — a product at the
    100th percentile gets the full BUSINESS_MAX_MULTIPLIER, a product at the
    0th percentile gets the full BUSINESS_MIN_MULTIPLIER, matching V1's
    scoring_config.py intent of Flean score being a first-class ranking
    signal, not one that saturates against the clamp halfway through the
    percentile range and never differentiates below-median products at all
    (the previous 1.0-to-1.15-only formula did both).

    Fallback: when stats.adjusted_score_percentiles.subcategory_percentile is
    absent (common for fresh produce which has a Flean score but no computed
    subcategory percentile), falls back to flean_score.adjusted_score (native
    Mongo-sourced field, read as-is — no recomputation), treating 0.5 as
    neutral over its native [0, 1] range.
    """
    rules = get_rules_for_subcategory(subcategory)
    multiplier = 1.0

    flean_pct = _get_nested(source, "stats.adjusted_score_percentiles.subcategory_percentile")
    if isinstance(flean_pct, (int, float)):
        deviation = (max(0.0, min(100.0, flean_pct)) - 50.0) / 50.0
        multiplier *= _symmetric_deviation_multiplier(deviation, settings)
    else:
        adjusted = _get_nested(source, "flean_score.adjusted_score")
        if isinstance(adjusted, (int, float)):
            deviation = (max(0.0, min(1.0, float(adjusted))) - 0.5) * 2.0
            multiplier *= _symmetric_deviation_multiplier(deviation, settings)

    for bonus in rules.get("bonuses", []):
        pct = _get_nested(source, bonus["field"])
        if isinstance(pct, (int, float)) and pct >= bonus["threshold"]:
            multiplier *= 1.0 + (bonus["weight"] - 1.0) * 0.5

    for penalty in rules.get("penalties", []):
        pct = _get_nested(source, penalty["field"])
        if isinstance(pct, (int, float)) and pct >= penalty["threshold"]:
            multiplier *= 1.0 - (1.0 - penalty["weight"]) * 0.5

    return multiplier


def ratings_rule(source: Dict[str, Any], subcategory: str, settings: SearchV2Settings) -> float:
    """Bonus for well-rated products, penalty for poorly-rated ones — neutral
    (1.0) if no rating data exists yet (a new product shouldn't be punished
    for having no reviews; that's review_count_rule's job, separately)."""
    rating = _get_nested(source, "review_stats.avg_rating")
    if not isinstance(rating, (int, float)):
        return 1.0
    # 5-star scale assumed (matches review_stats.avg_rating as produced by
    # the existing _hit_to_product_dict() shape) — 3.0 is treated as neutral,
    # symmetric bonus/penalty around it, capped at a modest ±12%.
    deviation = (rating - 3.0) / 2.0  # -1.0 (rating=1) .. +1.0 (rating=5)
    return 1.0 + max(-0.12, min(0.12, deviation * 0.12))


def review_count_rule(source: Dict[str, Any], subcategory: str, settings: SearchV2Settings) -> float:
    """Log-scaled — going from 10 to 100 reviews matters more than 1000 to
    10000; diminishing returns, not linear."""
    count = _get_nested(source, "review_stats.total_reviews")
    if not isinstance(count, (int, float)) or count <= 0:
        return 1.0
    bonus = min(0.10, 0.02 * math.log10(1 + count))
    return 1.0 + bonus


def stock_rule(source: Dict[str, Any], subcategory: str, settings: SearchV2Settings) -> float:
    """Demotes (does not exclude) out-of-stock items — exclusion, if wanted,
    is a FILTER concern (retrieval/lexical_query_builder.build_filters), not
    a ranking concern; this module only ever refines order. Checks a few
    common field-name variants defensively since the exact availability
    schema wasn't independently re-verified for this milestone — see
    ARCHITECTURE.md's note on schema-uncertain signals."""
    availability = source.get("availability")
    if not isinstance(availability, dict):
        return 1.0

    in_stock = availability.get("in_stock")
    if in_stock is None:
        in_stock = availability.get("is_in_stock")
    if in_stock is None:
        status = availability.get("stock_status")
        if isinstance(status, str):
            in_stock = status.lower() in ("in_stock", "available", "instock")
    if in_stock is None:
        return 1.0  # unknown — stay neutral rather than guess

    return 1.0 if in_stock else 0.6


def freshness_rule(source: Dict[str, Any], subcategory: str, settings: SearchV2Settings) -> float:
    """
    Best-effort, schema-uncertain — unlike the other rules, no V1/existing
    code confirmed an exact freshness field name for this catalog. Looks for
    a precomputed `freshness_score` (0-100, treated like the percentile
    fields above) if present; otherwise neutral. This is a clearly-marked
    placeholder for a real signal, not a fabricated one — see
    ARCHITECTURE.md and MIGRATION_GUIDE.md for what to wire up once the real
    field name is confirmed (e.g. days-since-received, expiry proximity).
    """
    score = _get_nested(source, "freshness_score")
    if not isinstance(score, (int, float)):
        return 1.0
    return 1.0 + 0.10 * (max(0.0, min(100.0, score)) / 100.0 - 0.5) * 2  # ±10% around the midpoint


def category_priority_rule(source: Dict[str, Any], subcategory: str, settings: SearchV2Settings) -> float:
    """Configurable, business-named category boosts — SETTINGS.CATEGORY_PRIORITY_BOOSTS,
    e.g. {"organic": 1.1}. Empty by default; this rule is a no-op until the
    business actually names priorities, which is the point — no opinion baked
    in by Search V2 itself."""
    if not settings.CATEGORY_PRIORITY_BOOSTS:
        return 1.0
    multiplier = 1.0
    candidates = {source.get("category_group"), source.get("leaf_category"), subcategory}
    for tag in source.get("descriptive_tags") or []:
        candidates.add(tag)
    for key, boost in settings.CATEGORY_PRIORITY_BOOSTS.items():
        if key in candidates:
            multiplier *= boost
    return multiplier


DEFAULT_RULES: List[RuleFn] = [
    flean_nutrition_rule, ratings_rule, review_count_rule,
    stock_rule, freshness_rule, category_priority_rule,
]

_NUTRIENT_BUCKET_BOUNDARIES: Dict[str, List[float]] = {
    "sugar_g": [5, 8, 12, 18],
    "sodium_mg": [120, 200, 350, 600],
    "saturated_fat_g": [1.5, 2.5, 3.5, 5],
    "fat_g": [3, 7.8, 12.7, 17.5],
    "trans_fat_g": [0, 0.2, 0.5, 1.0],
    "energy_kcal": [100, 150, 250, 400],
    "fiber_g": [1, 3, 6, 8],
    "protein_g": [3, 6, 9, 15],
    "potassium_mg": [150, 300, 500, 700],
    "carbs_g": [15, 30, 45, 60],
}

_NUTRIENT_BUCKET_SCORES: Dict[str, List[float]] = {
    "sugar_g": [1.0, 0.75, 0.5, 0.25, 0.0],
    "sodium_mg": [1.0, 0.75, 0.5, 0.25, 0.0],
    "saturated_fat_g": [1.0, 0.75, 0.5, 0.25, 0.0],
    "trans_fat_g": [1.0, 0.7, 0.4, 0.15, 0.0],
    "fat_g": [1.0, 0.75, 0.5, 0.25, 0.0],
    "carbs_g": [1.0, 0.75, 0.5, 0.25, 0.0],
    "energy_kcal": [1.0, 0.75, 0.5, 0.25, 0.0],
    "fiber_g": [0.0, 0.25, 0.5, 0.75, 1.0],
    "protein_g": [0.0, 0.25, 0.5, 0.75, 1.0],
    "potassium_mg": [0.0, 0.25, 0.5, 0.75, 1.0],
}

TRANSITION_BAND_PERCENT = 0.05


def _build_bucket_curve(boundaries: List[float], scores: List[float]) -> List[tuple]:
    points: List[tuple] = []
    for i, b in enumerate(boundaries):
        band = b * TRANSITION_BAND_PERCENT
        points.append((b - band, scores[i]))
        points.append((b + band, scores[i + 1]))
    return points


_CLINICAL_NUTRIENT_CURVES: Dict[str, List[tuple]] = {
    key: _build_bucket_curve(_NUTRIENT_BUCKET_BOUNDARIES[key], _NUTRIENT_BUCKET_SCORES[key])
    for key in _NUTRIENT_BUCKET_BOUNDARIES
}

_HEALTH_PREFERENCE_NUTRIENT: Dict[str, str] = {
    "high_protein": "protein_g",
    "high_fiber": "fiber_g",
    "low_carb": "carbs_g",
    "low_sugar": "sugar_g",
    "low_sodium": "sodium_mg",
    "low_fat": "fat_g",
    "low_saturated_fat": "saturated_fat_g",
    "low_trans_fat": "trans_fat_g",
    "low_calorie": "energy_kcal",
    "high_potassium": "potassium_mg",
}

# Potassium only exists on the older, space-keyed nutri_breakdown object, not
# nutri_breakdown_updated — see _read_nutrient().
_LEGACY_NUTRIENT_PATH: Dict[str, str] = {
    "potassium_mg": "category_data.nutritional.nutri_breakdown.potassium mg",
}

# Calibrated so a SINGLE primary-preference nutrient at its own clinical
# extreme (deviation=+-1.0) reaches the full HEALTH_INTENT_MIN/MAX_MULTIPLIER
# boundary on its own (0.18 = 1.18-1.0 = 1.0-0.82, settings.py's defaults) —
# same "100th/0th percentile gets the full clamp" full-range philosophy
# flean_nutrition_rule already uses, now that health_preference_rule has its
# own dedicated headroom (see apply_business_ranking()) instead of sharing
# the business clamp. Secondary keeps the original 2:1 primary:secondary
# ratio. Multiple qualifying preferences compound multiplicatively and are
# clamped afterward, so this is a per-nutrient calibration, not a promise
# that every combination lands exactly on the boundary.
_HEALTH_PRIMARY_WEIGHT = 0.18
_HEALTH_SECONDARY_WEIGHT = 0.09
_FLEAN_ADJUSTMENT_WEIGHT = 0.02


def _interpolate_clinical_score(value: float, curve: List[tuple]) -> float:
    if value <= curve[0][0]:
        return curve[0][1]
    if value >= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= value <= x1:
            frac = (value - x0) / (x1 - x0) if x1 > x0 else 0.0
            return y0 + (y1 - y0) * frac
    return curve[-1][1]


def _read_nutrient(source: Dict[str, Any], nutrient_key: str) -> Any:
    value = _get_nested(source, f"category_data.nutritional.nutri_breakdown_updated.{nutrient_key}")
    if isinstance(value, (int, float)):
        return value
    legacy_path = _LEGACY_NUTRIENT_PATH.get(nutrient_key)
    if legacy_path:
        return _get_nested(source, legacy_path)
    return value


def _read_no_added_sugar_score(source: Dict[str, Any]) -> Optional[float]:
    ingredient_tags = _get_nested(source, "category_data.tags.ingredient_tags")
    if isinstance(ingredient_tags, list) and "no_added_sugar" in ingredient_tags:
        return 1.0
    sweetener_tags = _get_nested(source, "category_data.tags.highlight_tags.sweetners_sugar_tags.positive")
    if isinstance(sweetener_tags, list) and "no_added_sugar" in sweetener_tags:
        return 1.0
    return None


def _flean_adjustment_score(source: Dict[str, Any]) -> float:
    cd = source.get("category_data")
    cd = cd if isinstance(cd, dict) else {}
    processing_type = str(cd.get("processing_type") or "").strip().lower()
    tags = cd.get("tags")
    ingredients_tags = (tags.get("highlight_tags") or {}).get("ingredients_tags") if isinstance(tags, dict) else None
    ingredients_tags = ingredients_tags if isinstance(ingredients_tags, dict) else {}
    negative = ingredients_tags.get("negative") or []
    positive = ingredients_tags.get("positive") or []

    score = 0.0
    if processing_type == "ultra_processed":
        score -= 1.0
    elif processing_type in ("unprocessed", "light_processed"):
        score += 0.5
    if negative:
        score -= 1.0
    if positive:
        score += 0.5

    return max(-1.0, min(1.0, score))


def health_preference_rule(
    source: Dict[str, Any],
    primary_preferences: tuple,
    secondary_preferences: tuple,
) -> float:
    seen_nutrients = set()
    multiplier = 1.0

    def _apply(pref: str, weight: float) -> None:
        nonlocal multiplier
        if pref == "no_added_sugar":
            if "no_added_sugar" in seen_nutrients:
                return
            seen_nutrients.add("no_added_sugar")
            clinical_score = _read_no_added_sugar_score(source)
            if clinical_score is None:
                return
            multiplier *= 1.0 + (clinical_score - 0.5) * 2.0 * weight
            return

        nutrient_key = _HEALTH_PREFERENCE_NUTRIENT.get(pref)
        if nutrient_key is None or nutrient_key in seen_nutrients:
            return
        seen_nutrients.add(nutrient_key)
        value = _read_nutrient(source, nutrient_key)
        if not isinstance(value, (int, float)):
            return
        clinical_score = _interpolate_clinical_score(float(value), _CLINICAL_NUTRIENT_CURVES[nutrient_key])
        deviation = (clinical_score - 0.5) * 2.0
        multiplier *= 1.0 + deviation * weight

    for pref in primary_preferences:
        _apply(pref, _HEALTH_PRIMARY_WEIGHT)
    for pref in secondary_preferences:
        _apply(pref, _HEALTH_SECONDARY_WEIGHT)

    if seen_nutrients:
        multiplier *= 1.0 + _flean_adjustment_score(source) * _FLEAN_ADJUSTMENT_WEIGHT

    return multiplier


def _is_exact_product_type_match(
    source: Dict[str, Any], product_type: Optional[str], product_type_category: Optional[str]
) -> bool:
    """True if `source` is an exact Product Intent Identification match for
    the query's resolved product_type — i.e. the SAME admission test
    retrieval/filters.py already uses for a high-confidence hard filter
    (product_type substring match, or category-leaf match), just evaluated
    here in Python against a document already in hand rather than as an ES
    clause. Reusing that exact definition (not inventing a second one) is
    deliberate: "exact match" means the same thing everywhere in this
    pipeline, whether or not the query's confidence was high enough to
    invoke a hard filter at retrieval time.

    Generic and catalog-derived — reads only fields Product Intent
    Identification already populated (product_type, category_hierarchies) —
    no product name, category, or word ever appears in this function."""
    if not product_type:
        return False
    doc_product_type = str(source.get("product_type") or "").lower()
    if product_type.lower() in doc_product_type:
        return True
    if product_type_category:
        for segment in source.get("category_hierarchies") or []:
            if not isinstance(segment, dict):
                continue
            segments = segment.get("segments") or []
            if segments and str(segments[-1]) == product_type_category:
                return True
    return False


def apply_business_ranking(
    items: List[Any],
    subcategory: str = "_default",
    rules: Optional[List[RuleFn]] = None,
    settings: Optional[SearchV2Settings] = None,
    resort: bool = True,
    product_type: Optional[str] = None,
    product_type_category: Optional[str] = None,
    health_intent: Optional[Any] = None,
) -> List[RankedItem]:
    """
    `items`: anything with `.doc_id`, `.source`, `.fused_score` (and
    optionally `.lexical_rank`/`.lexical_score`/`.semantic_rank`/`.semantic_score`)
    — i.e. retrieval.hybrid_search_orchestrator.ResultItem, by duck typing
    rather than an import (keeping this module's only dependency on
    retrieval-side code be the CALLER's, not this file's — see module
    docstring on independence).

    Relevance-first guarantee (Flean/business signals are a SECONDARY,
    tie-breaking signal, never a primary one): `final_score = relevance_score
    * multiplier`, where `multiplier` is the PRODUCT of two independently
    clamped factors — the general business signals (Flean nutrition,
    freshness, category priority, ...), clamped to [BUSINESS_MIN_MULTIPLIER,
    BUSINESS_MAX_MULTIPLIER] (0.82-1.18 by default), and, only when Health
    Intent is detected for the query, health_preference_rule's own factor,
    clamped separately to [HEALTH_INTENT_MIN_MULTIPLIER,
    HEALTH_INTENT_MAX_MULTIPLIER] (same 0.82-1.18 by default). They are kept
    separate rather than sharing one clamp because a fresh-produce item's
    Flean signal alone routinely saturates the business bound, which would
    otherwise leave zero headroom for health_preference_rule to
    differentiate a health-poor item from a health-friendly one — see
    settings.py for the full reasoning. Because each factor is a bounded
    *ratio* of the item's own relevance_score — not an absolute add-on — a
    query with no detected Health Intent shifts final_score by at most ~20%
    relative to that item's own relevance, exactly as before this change.
    Two items whose relevance differs by MORE than that can never be
    reordered by business ranking alone, regardless of which
    retrieval/fusion strategy produced relevance_score (RRF, weighted, or
    raw lexical BM25 all have this property preserved automatically, since
    it's scale-relative, not scale-specific). Items close enough in
    relevance to be "comparably relevant" CAN be reordered — which is
    exactly the desired behavior: prefer higher Flean score (or, when Health
    Intent is active, better alignment with the user's stated health
    objective) among near-ties, never override a real relevance gap. Do not
    widen either clamp's bounds without re-verifying this property (see
    settings.py's own note on the incident that narrowed the business bound
    from [0.75, 1.35]).

    `product_type` / `product_type_category` (optional — pass
    req.filters.product_type / .product_type_category from Product Intent
    Identification; None for either is a complete no-op, byte-identical to
    before this parameter existed): when set, resorting is done in TWO tiers
    — every item that is an exact product-type match (see
    _is_exact_product_type_match()) is ranked ahead of every item that
    isn't, with final_score ordering preserved WITHIN each tier. This closes
    a specific gap the bounded-multiplier guarantee above does not cover on
    its own: that guarantee only protects against a LARGE relevance gap
    being overturned, but for a small product family (e.g. only 4 "eggs"
    documents exist) an exact match's relevance can legitimately be close
    enough to a same-word-but-different-product lexical match (e.g. "egg
    mayonnaise", "egg-less rusk") that the existing bound allows reordering
    — mathematically consistent with the bound, but wrong from a search
    standpoint: a document that IS the product family a user asked for
    should never rank below one that merely mentions the word. The tiering
    only ever affects ORDER; it does not change relevance_score,
    business_multiplier, or final_score for any item, and a query with no
    resolved product_type (product_type=None) behaves exactly as before.

    `resort`: when False, `business_multiplier`/`final_score` are still
    computed and returned (so callers can display/debug them), but the
    incoming item ORDER is preserved rather than re-sorted by final_score.
    Set this to False when the caller already applied an explicit,
    non-relevance sort the user asked for (e.g. price_asc, protein_desc) —
    Flean/business signals are a relevance tie-breaker, not a replacement for
    an explicit user-requested sort, so they must never re-shuffle it.
    """
    settings = settings or SETTINGS
    rules = rules if rules is not None else DEFAULT_RULES
    ranked: List[RankedItem] = []

    for item in items:
        relevance_score = float(getattr(item, "fused_score", 0.0) or 0.0)
        source = getattr(item, "source", {}) or {}

        multiplier = 1.0
        breakdown: Dict[str, float] = {}
        if settings.ENABLE_BUSINESS_RANKING:
            rule_weights = getattr(settings, "BUSINESS_RULE_WEIGHTS", {})
            for rule in rules:
                component = rule(source, subcategory, settings)
                weight = rule_weights.get(rule.__name__, 1.0)
                # Scale the rule's DEVIATION from neutral (1.0) by its weight.
                # weight=1.0 → full effect (multiplier *= component)
                # weight=0.0 → neutral  (multiplier *= 1.0, rule disabled)
                # weight=0.5 → half the bonus/penalty
                effective = 1.0 + (component - 1.0) * weight
                breakdown[rule.__name__] = round(effective, 4)
                multiplier *= effective

            # Clamp the general business signals (Flean nutrition, freshness,
            # category priority, ...) on their own BEFORE folding in health —
            # see settings.py's HEALTH_INTENT_MIN/MAX_MULTIPLIER for why
            # health_preference_rule must not share this headroom.
            multiplier = max(settings.BUSINESS_MIN_MULTIPLIER, min(settings.BUSINESS_MAX_MULTIPLIER, multiplier))

            if (
                getattr(settings, "ENABLE_HEALTH_PREFERENCE_RANKING", True)
                and health_intent is not None
                and getattr(health_intent, "detected", False)
            ):
                component = health_preference_rule(
                    source, health_intent.primary_preferences, health_intent.secondary_preferences,
                )
                weight = rule_weights.get("health_preference_rule", 1.0)
                effective = 1.0 + (component - 1.0) * weight
                effective = max(
                    settings.HEALTH_INTENT_MIN_MULTIPLIER, min(settings.HEALTH_INTENT_MAX_MULTIPLIER, effective)
                )
                breakdown["health_preference_rule"] = round(effective, 4)
                multiplier *= effective

        ranked.append(RankedItem(
            doc_id=getattr(item, "doc_id", None),
            source=source,
            relevance_score=relevance_score,
            business_multiplier=round(multiplier, 4),
            final_score=round(relevance_score * multiplier, 4),
            rule_breakdown=breakdown,
            lexical_rank=getattr(item, "lexical_rank", None),
            lexical_score=getattr(item, "lexical_score", None),
            semantic_rank=getattr(item, "semantic_rank", None),
            semantic_score=getattr(item, "semantic_score", None),
        ))

    if resort:
        if product_type:
            # Tier 0 (exact product-type match) sorts entirely ahead of tier 1
            # (everything else); final_score still governs order WITHIN each
            # tier. See product_type/product_type_category in the docstring
            # above. Ascending sort on (tier, -final_score) == descending
            # final_score within an ascending tier order — ties within a tier
            # keep their input order (Python's sort is stable).
            ranked.sort(
                key=lambda r: (
                    0 if _is_exact_product_type_match(r.source, product_type, product_type_category) else 1,
                    -r.final_score,
                )
            )
        else:
            ranked.sort(key=lambda r: r.final_score, reverse=True)
    return ranked


def has_lab_report(source: Dict[str, Any]) -> bool:
    return bool(_get_nested(source, "category_data.lab_reports.url"))


def promote_lab_tested(
    ranked: List[RankedItem],
    product_type: Optional[str],
    product_type_category: Optional[str],
) -> List[RankedItem]:
    """Deterministic priority rule, not a weighted score: every Lab Tested item
    matching the query's resolved base product (reuses
    _is_exact_product_type_match(), same test used elsewhere) moves ahead of
    the rest, both groups keeping their existing relative order. Only
    reorders `ranked` — never fetches anything else, so a different base
    product's Lab Tested item (chips for a "cake" query) can't be promoted."""
    if not product_type:
        return ranked
    promoted, rest = [], []
    for item in ranked:
        if has_lab_report(item.source) and _is_exact_product_type_match(
            item.source, product_type, product_type_category
        ):
            promoted.append(item)
        else:
            rest.append(item)
    return promoted + rest if promoted else ranked


def register_rule(rules: List[RuleFn], rule: RuleFn, position: Optional[int] = None) -> List[RuleFn]:
    """Convenience for adding a custom business rule without editing this
    file — e.g. `my_rules = register_rule(list(DEFAULT_RULES), seasonal_boost_rule)`.
    Returns a NEW list; never mutates DEFAULT_RULES in place."""
    new_rules = list(rules)
    if position is None:
        new_rules.append(rule)
    else:
        new_rules.insert(position, rule)
    return new_rules
