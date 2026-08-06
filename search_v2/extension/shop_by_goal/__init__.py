"""Public API for shop-by-goal config and resolution."""

from .errors import GoalConfigError
from .loader import fetch_diet_config, fetch_goal_config
from .models import DietRule, GoalRule
from .resolver import resolve_diet_selection, resolve_goal_selection

__all__ = [
    "GoalConfigError",
    "DietRule",
    "GoalRule",
    "fetch_diet_config",
    "fetch_goal_config",
    "resolve_diet_selection",
    "resolve_goal_selection",
]
