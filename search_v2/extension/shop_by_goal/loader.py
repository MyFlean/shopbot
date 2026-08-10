"""Loading and validating shop-by-goal app-config payloads."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from .constants import (
    DEFAULT_DIET_CONFIG_URL,
    DEFAULT_GOAL_CONFIG_URL,
    DIET_CONFIG_URL_ENV,
    GOAL_CONFIG_TIMEOUT_SEC,
    GOAL_CONFIG_URL_ENV,
)
from .errors import GoalConfigError


def default_goal_config_url() -> str:
    """Resolve goal config URL from env with default fallback."""
    return os.getenv(GOAL_CONFIG_URL_ENV, DEFAULT_GOAL_CONFIG_URL).strip() or DEFAULT_GOAL_CONFIG_URL


def default_diet_config_url() -> str:
    """Resolve diet config URL from env with default fallback."""
    return os.getenv(DIET_CONFIG_URL_ENV, DEFAULT_DIET_CONFIG_URL).strip() or DEFAULT_DIET_CONFIG_URL


def _fetch_config_payload(
    *,
    source_url: str,
    timeout_sec: Optional[float] = None,
    selector_name: str,
) -> Dict[str, Any]:
    selector_label = selector_name.strip().lower() or "selector"
    timeout = timeout_sec if timeout_sec is not None else GOAL_CONFIG_TIMEOUT_SEC
    if not source_url:
        raise GoalConfigError(
            f"{selector_label.upper()}_CONFIG_URL_MISSING",
            f"{selector_label.title()} config URL is not configured",
            500,
        )
    try:
        with urllib_request.urlopen(source_url, timeout=timeout) as response:
            raw_payload = json.loads(response.read().decode("utf-8"))
    except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError) as exc:
        raise GoalConfigError(
            f"{selector_label.upper()}_CONFIG_FETCH_FAILED",
            f"Failed to fetch {selector_label} config from '{source_url}': {exc}",
            503,
        ) from exc
    except ValueError as exc:
        raise GoalConfigError(
            f"{selector_label.upper()}_CONFIG_JSON_INVALID",
            f"{selector_label.title()} config endpoint returned invalid JSON",
            500,
        ) from exc
    except Exception as exc:
        raise GoalConfigError(
            f"{selector_label.upper()}_CONFIG_FETCH_FAILED",
            f"Unexpected error fetching {selector_label} config: {exc}",
            503,
        ) from exc

    if not isinstance(raw_payload, dict):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            f"{selector_label.title()} config payload must be a JSON object",
            500,
        )
    return raw_payload


def fetch_goal_config(
    *,
    url: Optional[str] = None,
    timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Fetch and validate app-config goal JSON payload."""
    payload = _fetch_config_payload(
        source_url=(url or default_goal_config_url()).strip(),
        timeout_sec=timeout_sec,
        selector_name="goal",
    )
    goals = payload.get("goals")
    if not isinstance(goals, list):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal config payload must include 'goals' array",
            500,
        )
    return payload


def fetch_diet_config(
    *,
    url: Optional[str] = None,
    timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Fetch and validate app-config diet JSON payload."""
    payload = _fetch_config_payload(
        source_url=(url or default_diet_config_url()).strip(),
        timeout_sec=timeout_sec,
        selector_name="diet",
    )
    diets = payload.get("diets")
    if not isinstance(diets, list):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Diet config payload must include 'diets' array",
            500,
        )
    return payload
