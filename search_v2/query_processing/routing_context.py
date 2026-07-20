from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from search_v2.query_processing.health_intent_classifier import HealthIntentResult
    from search_v2.query_processing.product_intent_extractor import ProductIntentResult
    from search_v2.retrieval.filters import SearchFilters


@dataclass(frozen=True)
class RoutingContext:
    product_intent_source: str = "none"
    product_intent_confidence: float = 0.0
    product_intent_is_compound: bool = False
    has_fresh_produce_match: bool = False
    health_intent_detected: bool = False
    has_nutritional_constraint: bool = False
    health_intent_matched_phrases: Tuple[str, ...] = ()


def build_routing_context(
    product_intent: Optional["ProductIntentResult"],
    health_intent: Optional["HealthIntentResult"],
    filters: Optional["SearchFilters"] = None,
) -> RoutingContext:
    is_compound = bool(
        product_intent and product_intent.primary_product and " " in product_intent.primary_product
    )
    has_nutritional_constraint = bool(
        filters and (filters.macro_filters or filters.nutrition_profiles)
    )
    return RoutingContext(
        product_intent_source=product_intent.source if product_intent else "none",
        product_intent_confidence=product_intent.confidence if product_intent else 0.0,
        product_intent_is_compound=is_compound,
        has_fresh_produce_match=bool(product_intent.fresh_produce_ids) if product_intent else False,
        health_intent_detected=health_intent.detected if health_intent else False,
        has_nutritional_constraint=has_nutritional_constraint,
        health_intent_matched_phrases=health_intent.matched_phrases if health_intent else (),
    )
