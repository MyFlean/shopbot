from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CompiledGoalDietDefinition:
    """Runtime representation of one goal or diet tile."""

    id: str
    kind: str  # "goal" | "diet"
    display_name: str
    triggers: Tuple[str, ...]
    trigger_patterns: Tuple[re.Pattern, ...]
    include_clauses: Tuple[Dict[str, Any], ...]
    exclude_clauses: Tuple[Dict[str, Any], ...]
    default_sort: Optional[str] = None


@dataclass(frozen=True)
class CompiledRegistry:
    """Fully compiled in-memory registry — loaded once, cached forever."""

    definitions: Dict[str, CompiledGoalDietDefinition]
    trigger_index: Tuple[Tuple[re.Pattern, str, str], ...]
    # (pattern, definition_id, trigger_phrase) — longest triggers matched first per definition


@dataclass(frozen=True)
class MergedGoalDietPlan:
    """Output of merging multiple goal/diet IDs into retrieval clauses."""

    filter_clauses: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    must_not_clauses: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    default_sort: Optional[str] = None
