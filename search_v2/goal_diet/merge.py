from __future__ import annotations

from typing import List, Optional

from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.goal_diet.types import MergedGoalDietPlan


def merge_goal_diet_plans(ids: List[str]) -> MergedGoalDietPlan:
    """
    Single merge implementation for multi-goal/diet combinations.

    Semantics:
      - inclusion filters: AND (all selected goals must be satisfied)
      - exclusion filters: union (exclude if any goal excludes)
      - default sort: first resolved goal/diet with a sort wins
    """
    registry = get_compiled_registry()
    include: List[dict] = []
    exclude: List[dict] = []
    default_sort: Optional[str] = None

    seen_include = set()
    seen_exclude = set()

    for goal_id in ids:
        definition = registry.definitions.get(goal_id)
        if definition is None:
            continue
        for clause in definition.include_clauses:
            key = _clause_key(clause)
            if key not in seen_include:
                seen_include.add(key)
                include.append(clause)
        for clause in definition.exclude_clauses:
            key = _clause_key(clause)
            if key not in seen_exclude:
                seen_exclude.add(key)
                exclude.append(clause)
        if default_sort is None and definition.default_sort:
            default_sort = definition.default_sort

    return MergedGoalDietPlan(
        filter_clauses=tuple(include),
        must_not_clauses=tuple(exclude),
        default_sort=default_sort,
    )


def _clause_key(clause: dict) -> str:
    """Stable dedup key for ES clause dicts."""
    return repr(_normalize(clause))


def _normalize(obj):
    if isinstance(obj, dict):
        return tuple((k, _normalize(v)) for k, v in sorted(obj.items()))
    if isinstance(obj, list):
        return tuple(_normalize(v) for v in obj)
    return obj
