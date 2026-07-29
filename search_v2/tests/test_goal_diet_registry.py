from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.goal_diet.registry_loader import (
    NUTRITION_PROFILE_TO_GOAL_DIET,
    get_all_triggers,
    get_compiled_registry,
    get_default_sort_for_goal_diet_ids,
)


@pytest.fixture(autouse=True)
def fresh_registry():
    get_compiled_registry(force_reload=True)
    yield


def test_registry_loads_all_tiles():
    registry = get_compiled_registry()
    assert len(registry.definitions) >= 20
    assert "high_protein" in registry.definitions
    assert "keto" in registry.definitions
    assert registry.definitions["high_protein"].kind == "goal"
    assert registry.definitions["keto"].kind == "diet"


def test_registry_compiles_filter_clauses():
    definition = get_compiled_registry().definitions["high_protein"]
    assert definition.include_clauses
    assert definition.exclude_clauses
    assert "bool" in definition.include_clauses[0]
    assert definition.default_sort == "protein"


def test_registry_compiles_hybrid_vegan_inclusion():
    definition = get_compiled_registry().definitions["vegan"]
    text = str(definition.include_clauses)
    assert "dietary_tags" in text
    assert "dietary_label" in text
    assert "ingredients.raw_text" in text


def test_registry_trigger_index_longest_first():
    registry = get_compiled_registry()
    lengths = [len(t[2]) for t in registry.trigger_index]
    assert lengths == sorted(lengths, reverse=True)


def test_get_all_triggers_non_empty():
    triggers = get_all_triggers()
    assert "keto" in triggers
    assert "high protein" in triggers


def test_default_sort_first_goal_wins():
    assert get_default_sort_for_goal_diet_ids(["heart_healthy"]) == "quality"
    assert get_default_sort_for_goal_diet_ids(["high_protein", "low_sugar"]) == "protein"


def test_nutrition_profile_mapping():
    assert NUTRITION_PROFILE_TO_GOAL_DIET["high_protein"] == "high_protein"
    assert NUTRITION_PROFILE_TO_GOAL_DIET["low_carb"] == "keto"
