"""Typed exceptions for shop-by-goal config and resolution."""

from __future__ import annotations


class GoalConfigError(Exception):
    """Typed config/runtime failure for goal resolution."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
