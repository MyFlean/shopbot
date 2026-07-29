from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.query_processing.health_intent_classifier import classify_health_intent


def test_detects_single_goal():
    get_compiled_registry(force_reload=True)
    result = classify_health_intent("keto bread")
    assert result.detected
    assert "keto" in result.goal_diet_ids
    assert "keto" in result.matched_phrases


def test_detects_high_protein_phrase():
    get_compiled_registry(force_reload=True)
    result = classify_health_intent("high protein oats")
    assert result.detected
    assert "high_protein" in result.goal_diet_ids


def test_detects_compound_goals():
    get_compiled_registry(force_reload=True)
    result = classify_health_intent("vegan gluten free snacks")
    assert result.detected
    assert "vegan" in result.goal_diet_ids
    assert "gluten_free" in result.goal_diet_ids


def test_no_detection_for_plain_product():
    get_compiled_registry(force_reload=True)
    result = classify_health_intent("potato chips")
    assert not result.detected
    assert result.goal_diet_ids == ()


def test_empty_query():
    result = classify_health_intent("")
    assert not result.detected
