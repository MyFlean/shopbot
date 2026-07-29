from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.goal_diet.merge import merge_goal_diet_plans
from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses


def test_merge_and_includes_all_goals():
    get_compiled_registry(force_reload=True)
    plan = merge_goal_diet_plans(["high_protein", "low_sugar"])
    assert len(plan.filter_clauses) >= 2
    assert plan.default_sort == "protein"


def test_build_filter_clauses_from_goal_diet_ids():
    get_compiled_registry(force_reload=True)
    sf = SearchFilters(goal_diet_ids=["keto"])
    clauses = build_filter_clauses(sf)
    assert clauses.filter_clauses
    assert clauses.must_not_clauses
    text = str(clauses.filter_clauses)
    assert "script" in text
    assert "net_carbs" in text or "carbs_g" in text
    assert "fat_cal_pct" in text or "fat_g" in text


def test_browse_and_search_share_filter_engine():
    get_compiled_registry(force_reload=True)
    sf = SearchFilters(goal_diet_ids=["vegan"])
    clauses = build_filter_clauses(sf)
    assert any("dietary_tags" in str(c) for c in clauses.filter_clauses)


def test_merge_deduplicates_identical_clauses():
    get_compiled_registry(force_reload=True)
    plan = merge_goal_diet_plans(["high_protein", "high_protein"])
    assert len(plan.filter_clauses) == 1


def test_dietary_labels_skipped_when_equivalent_goal_diet_active():
    get_compiled_registry(force_reload=True)
    goal_only = build_filter_clauses(SearchFilters(goal_diet_ids=["keto"]))
    with_redundant_label = build_filter_clauses(
        SearchFilters(goal_diet_ids=["keto"], dietary_labels=["KETO"])
    )
    assert with_redundant_label.filter_clauses == goal_only.filter_clauses
    assert with_redundant_label.must_not_clauses == goal_only.must_not_clauses
