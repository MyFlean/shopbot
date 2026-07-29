from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses


def test_keto_uses_derived_metric_scripts_not_percentile_proxies():
    get_compiled_registry(force_reload=True)
    clauses = build_filter_clauses(SearchFilters(goal_diet_ids=["keto"]))
    text = str(clauses.filter_clauses)
    assert "script" in text
    assert "carbs_penalty_percentiles" not in text
    assert "healthy_fat_percentiles" not in text
    assert "lte_net_carbs" in text
    assert "gte_fat_cal_pct" in text


def test_high_protein_includes_protein_cal_pct_script():
    get_compiled_registry(force_reload=True)
    definition = get_compiled_registry().definitions["high_protein"]
    text = str(definition.include_clauses)
    assert "gte_protein_cal_pct" in text
    assert "stats.protein_percentiles" not in text
