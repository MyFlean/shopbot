from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from search_v2.config.settings import SearchV2Settings
    from search_v2.query_processing.routing_context import RoutingContext

LEXICAL_ONLY: Literal["LEXICAL_ONLY"] = "LEXICAL_ONLY"
HYBRID: Literal["HYBRID"] = "HYBRID"

RouteDecision = Literal["LEXICAL_ONLY", "HYBRID"]


def route(context: "RoutingContext", settings: Optional["SearchV2Settings"] = None) -> RouteDecision:
    if settings is None:
        from search_v2.config.settings import SETTINGS as settings

    if context.has_nutritional_constraint:
        return HYBRID

    if context.health_intent_detected:
        return HYBRID

    if context.has_fresh_produce_match:
        return LEXICAL_ONLY

    if context.product_intent_source == "category_fallback":
        return LEXICAL_ONLY

    if context.product_intent_source == "head_term" and (
        context.product_intent_is_compound
        or context.product_intent_confidence >= settings.ROUTER_CONFIDENCE_THRESHOLD
    ):
        return LEXICAL_ONLY

    return HYBRID
