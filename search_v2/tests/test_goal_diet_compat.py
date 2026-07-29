from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses, merge_filters
from search_v2.retrieval.sorting import resolve_sort_for_filters


def test_nutrition_profiles_shim_maps_to_goal_diet_ids():
    get_compiled_registry(force_reload=True)
    sf = SearchFilters.from_dict({"nutrition_profiles": ["high_protein", "low_sugar"]})
    assert sf.goal_diet_ids is not None
    assert "high_protein" in sf.goal_diet_ids
    assert "low_sugar" in sf.goal_diet_ids
    assert sf.nutrition_profiles is None


def test_explicit_goal_diet_ids_preserved():
    sf = SearchFilters.from_dict({"goal_diet_ids": ["keto", "vegan"]})
    assert sf.goal_diet_ids == ["keto", "vegan"]


def test_merge_filters_unions_goal_diet_ids():
    base = SearchFilters(goal_diet_ids=["keto"])
    overlay = SearchFilters(goal_diet_ids=["vegan"])
    merged = merge_filters(base, overlay)
    assert merged.goal_diet_ids == ["keto", "vegan"]


def test_legacy_nutrition_profiles_still_filter():
    get_compiled_registry(force_reload=True)
    sf = SearchFilters.from_dict({"nutrition_profiles": ["unknown_profile"]})
    assert sf.nutrition_profiles == ["unknown_profile"]
    clauses = build_filter_clauses(sf)
    assert not any("goal" in str(c) for c in clauses.filter_clauses)


def test_explicit_sort_overrides_goal_default():
    get_compiled_registry(force_reload=True)
    assert resolve_sort_for_filters("price_asc", ["high_protein"]) == "price_asc"


def test_goal_default_sort_when_no_explicit_sort():
    get_compiled_registry(force_reload=True)
    assert resolve_sort_for_filters(None, ["high_protein"]) == "protein"
