"""Goal/Diet registry — single source of truth for Shop by Goals/Diets."""

from search_v2.goal_diet.merge import merge_goal_diet_plans
from search_v2.goal_diet.registry_loader import (
    NUTRITION_PROFILE_TO_GOAL_DIET,
    get_all_triggers,
    get_compiled_registry,
    get_default_sort_for_goal_diet_ids,
)

__all__ = [
    "NUTRITION_PROFILE_TO_GOAL_DIET",
    "get_all_triggers",
    "get_compiled_registry",
    "get_default_sort_for_goal_diet_ids",
    "merge_goal_diet_plans",
]
