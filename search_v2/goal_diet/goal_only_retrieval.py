from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from search_v2.retrieval.lexical_query_builder import _core_text

if TYPE_CHECKING:
    from search_v2.query_processing.query_pipeline import ProcessedQuery
    from search_v2.query_processing.routing_context import RoutingContext
    from search_v2.retrieval.filters import SearchFilters


def is_goal_only_retrieval(
    query: "ProcessedQuery",
    filters: Optional["SearchFilters"],
    routing_context: Optional["RoutingContext"],
) -> bool:
    """
    True when the request should behave like a Shop-by-Goal tile: Goal/Diet
    filters only, no lexical/semantic text constraint.

    Conditions (all required):
      - Health Intake detected a goal/diet (or filters carry goal_diet_ids)
      - goal_diet_ids present on SearchFilters
      - No parallel macro/nutrition constraint from NL extraction
      - No active high-confidence product-type hard filter (goal + product queries)
      - Remaining query text is empty after stripping matched goal/diet phrases
    """
    from search_v2.retrieval.filters import SearchFilters

    if routing_context is None or not routing_context.goal_diet_detected:
        return False
    if not isinstance(filters, SearchFilters) or not filters.goal_diet_ids:
        return False
    if routing_context.has_nutritional_constraint:
        return False
    if filters.product_type_mode == "filter" and filters.product_type:
        return False
    if filters.product_ids:
        return False

    matched = tuple(routing_context.health_intent_matched_phrases or ())
    primary = query.primary_text().strip()
    if not primary:
        return bool(matched) or bool(filters.goal_diet_ids)

    if not matched:
        return False

    core = _core_text(primary, matched).strip()
    return not core


def filter_only_processed_query(query: "ProcessedQuery") -> "ProcessedQuery":
    """Return a ProcessedQuery whose variants are blank — triggers match_all + filters in build_query()."""
    from search_v2.query_processing.query_pipeline import ProcessedQuery, QueryVariant

    return ProcessedQuery(
        raw_query=query.raw_query,
        normalized_query="",
        variants=[QueryVariant(text="", is_correction=False, confidence=1.0)],
        correction_result=query.correction_result,
    )


def resolve_router_decision(routing_context: Optional["RoutingContext"], settings) -> Optional[str]:
    if routing_context is None:
        return None
    if not getattr(settings, "ENABLE_QUERY_ROUTER", True):
        return None
    from search_v2.query_processing.query_router import route

    return route(routing_context, settings)
