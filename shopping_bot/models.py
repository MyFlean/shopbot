"""
Dataclass models shared across the whole application.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Union

from .enums import QueryIntent, ResponseType, BackendFunction, UserSlot


@dataclass
class UserContext:
    user_id: str
    session_id: str
    permanent: Dict[str, Any] = field(default_factory=dict)
    session: Dict[str, Any] = field(default_factory=dict)
    fetched_data: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


@dataclass
class RequirementAssessment:
    intent: QueryIntent
    # Allow both slots & backend functions
    missing_data: List[Union[BackendFunction, UserSlot]]
    rationale: Dict[str, str]
    priority_order: List[Union[BackendFunction, UserSlot]]


@dataclass
class BotResponse:
    response_type: ResponseType
    content: Dict[str, Any]
    functions_executed: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


# NEW ── Follow-up result & patch
@dataclass
class FollowUpPatch:
    slots: Dict[str, Any]
    intent_override: str | None = None
    reset_context: bool = False


@dataclass
class FollowUpResult:
    is_follow_up: bool
    patch: FollowUpPatch
    reason: str = ""