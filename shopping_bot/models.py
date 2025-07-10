# shopping_bot/models.py
"""
Dataclass models shared across the whole application.
They live in one place so that other modules don't need to
re-declare the same structures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List

from .enums import QueryIntent, ResponseType, ShoppingFunction


# ──────────────────────────────────────────────────────────────────────────────
# Conversation-state models
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class UserContext:
    """
    Holds everything we know about a user & the current chat session.

    • permanent  → long-lived user profile data (e.g. default address)
    • session    → per-chat ephemeral data (budget, preferences, etc.)
    • fetched_data → cached results of expensive fetches
    """
    user_id: str
    session_id: str
    permanent: Dict[str, Any] = field(default_factory=dict)
    session: Dict[str, Any] = field(default_factory=dict)
    fetched_data: Dict[str, Any] = field(default_factory=dict)

    # Handy helper for JSON serialisation
    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


@dataclass
class RequirementAssessment:
    """
    Output of the 'what do we still need?' reasoning step.
    """
    intent: QueryIntent
    missing_data: List[ShoppingFunction]
    rationale: Dict[str, str]
    priority_order: List[ShoppingFunction]


@dataclass
class BotResponse:
    """
    Unified return type from the bot core.

    • response_type controls how the frontend should treat it
    • content is a free-form dict (question payload or final answer)
    • functions_executed logs which data fetches just ran
    """
    response_type: ResponseType
    content: Dict[str, Any]
    functions_executed: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)
