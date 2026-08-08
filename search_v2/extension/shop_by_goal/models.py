"""Data models for shop-by-goal rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GoalRule:
    goal_id: str
    label: str
    enabled: bool
    include_all: List[Dict[str, Any]]
    exclude_any: List[Dict[str, Any]]
    sort_order: Optional[List[Dict[str, str]]]
    sort_by: Optional[str]


@dataclass(frozen=True)
class DietRule:
    diet_id: str
    label: str
    enabled: bool
    include_all: List[Dict[str, Any]]
    exclude_any: List[Dict[str, Any]]
    sort_order: Optional[List[Dict[str, str]]]
    sort_by: Optional[str]
