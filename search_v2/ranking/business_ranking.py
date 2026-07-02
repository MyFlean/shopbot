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
already compressed toward 1.0 (mirrors Search V1's scoring_config.py
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

def flean_nutrition_rule(source: Dict[str, Any], subcategory: str, settings: SearchV2Settings) -> float:
    """Reuses the real existing bonuses/penalties from
    shopping_bot/scoring_config.py (see scoring_rules_importer.py). Same
    compression formula Search V1 used: each rule's own weight is compressed
    halfway toward 1.0 so several stacking bonuses/penalties can't blow past
    the overall clamp before clamping even applies.

    Fallback: when stats.adjusted_score_percentiles.subcategory_percentile is
    absent (common for fresh produce which has a Flean score but no computed
    subcategory percentile), falls back to flean_score.adjusted_score.
    flean_score.adjusted_score is in [0, 1]; the formula 1 + 0.15 * v maps it
    to [1.0, 1.15] — identical output range to the percentile formula.
    """
    rules = get_rules_for_subcategory(subcategory)
    multiplier = 1.0

    flean_pct = _get_nested(source, "stats.adjusted_score_percentiles.subcategory_percentile")
    if isinstance(flean_pct, (int, float)):
        multiplier *= 1.0 + 0.15 * (max(0.0, min(100.0, flean_pct)) / 100.0)
    else:
        adjusted = _get_nested(source, "flean_score.adjusted_score")
        if isinstance(adjusted, (int, float)):
            multiplier *= 1.0 + 0.15 * max(0.0, min(1.0, float(adjusted)))

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


def apply_business_ranking(
    items: List[Any],
    subcategory: str = "_default",
    rules: Optional[List[RuleFn]] = None,
    settings: Optional[SearchV2Settings] = None,
) -> List[RankedItem]:
    """
    `items`: anything with `.doc_id`, `.source`, `.fused_score` (and
    optionally `.lexical_rank`/`.lexical_score`/`.semantic_rank`/`.semantic_score`)
    — i.e. retrieval.hybrid_search_orchestrator.ResultItem, by duck typing
    rather than an import (keeping this module's only dependency on
    retrieval-side code be the CALLER's, not this file's — see module
    docstring on independence).
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
            multiplier = max(settings.BUSINESS_MIN_MULTIPLIER, min(settings.BUSINESS_MAX_MULTIPLIER, multiplier))

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

    ranked.sort(key=lambda r: r.final_score, reverse=True)
    return ranked


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
