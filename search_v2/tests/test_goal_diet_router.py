from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.config.settings import SETTINGS
from search_v2.query_processing.query_router import HYBRID, LEXICAL_ONLY, route
from search_v2.query_processing.routing_context import RoutingContext


def test_goal_plus_strong_product_intent_is_lexical():
    ctx = RoutingContext(
        product_intent_source="head_term",
        product_intent_confidence=0.9,
        product_intent_is_compound=True,
        goal_diet_detected=True,
    )
    assert route(ctx, SETTINGS) == LEXICAL_ONLY


def test_goal_only_stays_hybrid():
    ctx = RoutingContext(goal_diet_detected=True)
    assert route(ctx, SETTINGS) == HYBRID


def test_macro_constraint_still_hybrid_even_with_product_intent():
    ctx = RoutingContext(
        product_intent_source="head_term",
        product_intent_confidence=0.9,
        product_intent_is_compound=True,
        goal_diet_detected=True,
        has_nutritional_constraint=True,
    )
    assert route(ctx, SETTINGS) == HYBRID


def test_plain_product_lexical_via_product_intent():
    ctx = RoutingContext(
        product_intent_source="head_term",
        product_intent_confidence=0.9,
        product_intent_is_compound=False,
        goal_diet_detected=False,
    )
    assert route(ctx, SETTINGS) == LEXICAL_ONLY
