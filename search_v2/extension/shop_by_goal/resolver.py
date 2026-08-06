"""Rule parsing and query-clause resolution for shop-by-goal."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .constants import (
    DERIVED_FIELD_SCRIPTS,
    DERIVED_OP_MAP,
    SORT_FIELD_TO_UNIFIED_SORT,
)
from .errors import GoalConfigError
from .loader import fetch_diet_config, fetch_goal_config
from .models import DietRule, GoalRule

_log = logging.getLogger(__name__)


def _normalize_selector_ids(raw: Any) -> List[str]:
    parts: List[Any]
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [raw]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for part in parts:
        if not isinstance(part, str):
            continue
        for token in part.split(","):
            normalized = token.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
    return out


def _normalize_goal_ids(raw: Any) -> List[str]:
    return _normalize_selector_ids(raw)


def _normalize_diet_ids(raw: Any) -> List[str]:
    return _normalize_selector_ids(raw)


def _validated_field_name(field_name: Any) -> str:
    if not isinstance(field_name, str):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal rule leaf 'field' must be a string",
            500,
        )
    normalized = field_name.strip()
    if not normalized:
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal rule leaf 'field' cannot be empty",
            500,
        )
    if "nutri_breakdown_updated" in normalized:
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal rule fields must use 'nutri_breakdown' (not 'nutri_breakdown_updated')",
            500,
        )
    return normalized


def _term_contains_clause(field_name: str, value: Any) -> Dict[str, Any]:
    return {
        "bool": {
            "should": [
                {"term": {field_name: value}},
                {"term": {f"{field_name}.keyword": value}},
            ],
            "minimum_should_match": 1,
        }
    }


def _match_clause(field_name: str, value: Any) -> Dict[str, Any]:
    return {"match": {field_name: value}}


def _match_phrase_clause(field_name: str, value: Any) -> Dict[str, Any]:
    return {"match_phrase": {field_name: value}}


def _derived_script_clause(field_name: str, op: str, value: Any) -> Dict[str, Any]:
    base_script = DERIVED_FIELD_SCRIPTS.get(field_name)
    predicate = DERIVED_OP_MAP.get(op)
    if not base_script or not predicate:
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            f"Unsupported derived field condition '{field_name} {op}'",
            500,
        )
    return {
        "script": {
            "script": {
                "lang": "painless",
                "source": f"{base_script} return {predicate};",
                "params": {"value": value},
            }
        }
    }


def _leaf_condition_to_clause(node: Dict[str, Any]) -> Dict[str, Any]:
    field_name = _validated_field_name(node.get("field"))
    op = str(node.get("op") or "").strip().lower()
    value = node.get("value")
    if not op:
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal rule leaf condition must include 'op'",
            500,
        )
    if field_name.startswith("derived."):
        return _derived_script_clause(field_name, op, value)

    if op in {"gt", "gte", "lt", "lte"}:
        return {"range": {field_name: {op: value}}}
    if op == "eq":
        return {"term": {field_name: value}}
    if op == "neq":
        return {"bool": {"must_not": [{"term": {field_name: value}}]}}
    if op == "contains":
        return _term_contains_clause(field_name, value)
    if op == "contains_any":
        values = [item for item in (value or []) if item is not None]
        return {
            "bool": {
                "should": [_term_contains_clause(field_name, item) for item in values],
                "minimum_should_match": 1,
            }
        }
    if op == "match_any":
        values = [item for item in (value or []) if item is not None]
        return {
            "bool": {
                "should": [_match_clause(field_name, item) for item in values],
                "minimum_should_match": 1,
            }
        }
    if op == "match_phrase_any":
        values = [item for item in (value or []) if item is not None]
        return {
            "bool": {
                "should": [_match_phrase_clause(field_name, item) for item in values],
                "minimum_should_match": 1,
            }
        }
    if op == "not_empty":
        return {"exists": {"field": field_name}}

    raise GoalConfigError(
        "GOAL_CONFIG_SCHEMA_INVALID",
        f"Unsupported goal condition operator '{op}'",
        500,
    )


def _node_to_clause(node: Any) -> Dict[str, Any]:
    if not isinstance(node, dict):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal condition node must be an object",
            500,
        )
    if "all_of" in node:
        entries = node.get("all_of")
        if not isinstance(entries, list) or not entries:
            raise GoalConfigError(
                "GOAL_CONFIG_SCHEMA_INVALID",
                "'all_of' must be a non-empty array",
                500,
            )
        return {"bool": {"filter": [_node_to_clause(item) for item in entries]}}
    if "any_of" in node:
        entries = node.get("any_of")
        if not isinstance(entries, list) or not entries:
            raise GoalConfigError(
                "GOAL_CONFIG_SCHEMA_INVALID",
                "'any_of' must be a non-empty array",
                500,
            )
        return {
            "bool": {
                "should": [_node_to_clause(item) for item in entries],
                "minimum_should_match": 1,
            }
        }
    return _leaf_condition_to_clause(node)


def _sort_alias_from_sort_order(sort_order: Any) -> Optional[str]:
    if not isinstance(sort_order, list):
        return None
    for entry in sort_order:
        if not isinstance(entry, dict):
            continue
        field_name = str(entry.get("field") or "").strip()
        order = str(entry.get("order") or "").strip().lower()
        alias = SORT_FIELD_TO_UNIFIED_SORT.get((field_name, order))
        if alias:
            return alias
    return None


def _parse_sort_order(sort_order: Any) -> Optional[List[Dict[str, str]]]:
    if sort_order is None:
        return None
    if not isinstance(sort_order, list) or not sort_order:
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal 'sort_order' must be a non-empty array of {field, order}",
            500,
        )
    parsed: List[Dict[str, str]] = []
    for entry in sort_order:
        if not isinstance(entry, dict):
            raise GoalConfigError(
                "GOAL_CONFIG_SCHEMA_INVALID",
                "Each goal sort_order entry must be an object",
                500,
            )
        field_name = _validated_field_name(entry.get("field"))
        order = str(entry.get("order") or "").strip().lower()
        if order not in {"asc", "desc"}:
            raise GoalConfigError(
                "GOAL_CONFIG_SCHEMA_INVALID",
                "Goal sort_order 'order' must be 'asc' or 'desc'",
                500,
            )
        parsed.append({"field": field_name, "order": order})
    return parsed or None


def _parse_goal_rule(entry: Dict[str, Any]) -> GoalRule:
    goal_id = str(entry.get("id") or "").strip().lower()
    label = str(entry.get("label") or "").strip()
    enabled = bool(entry.get("enabled", True))
    include_all = entry.get("include_all") or []
    exclude_any = entry.get("exclude_any") or []
    if not goal_id:
        raise GoalConfigError("GOAL_CONFIG_SCHEMA_INVALID", "Goal entry missing 'id'", 500)
    if not label:
        raise GoalConfigError("GOAL_CONFIG_SCHEMA_INVALID", f"Goal '{goal_id}' missing 'label'", 500)
    if not isinstance(include_all, list) or not isinstance(exclude_any, list):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            f"Goal '{goal_id}' requires array fields include_all/exclude_any",
            500,
        )
    sort_order = _parse_sort_order(entry.get("sort_order"))
    sort_by_raw = str(entry.get("sort_by") or "").strip().lower() or None
    sort_by = sort_by_raw or _sort_alias_from_sort_order(sort_order)
    return GoalRule(
        goal_id=goal_id,
        label=label,
        enabled=enabled,
        include_all=include_all,
        exclude_any=exclude_any,
        sort_order=sort_order,
        sort_by=sort_by,
    )


def _parse_diet_rule(entry: Dict[str, Any]) -> DietRule:
    diet_id = str(entry.get("id") or "").strip().lower()
    label = str(entry.get("label") or "").strip()
    enabled = bool(entry.get("enabled", True))
    include_all = entry.get("include_all") or []
    exclude_any = entry.get("exclude_any") or []
    if not diet_id:
        raise GoalConfigError("GOAL_CONFIG_SCHEMA_INVALID", "Diet entry missing 'id'", 500)
    if not label:
        raise GoalConfigError("GOAL_CONFIG_SCHEMA_INVALID", f"Diet '{diet_id}' missing 'label'", 500)
    if not isinstance(include_all, list) or not isinstance(exclude_any, list):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            f"Diet '{diet_id}' requires array fields include_all/exclude_any",
            500,
        )
    sort_order = _parse_sort_order(entry.get("sort_order"))
    sort_by_raw = str(entry.get("sort_by") or "").strip().lower() or None
    sort_by = sort_by_raw or _sort_alias_from_sort_order(sort_order)
    return DietRule(
        diet_id=diet_id,
        label=label,
        enabled=enabled,
        include_all=include_all,
        exclude_any=exclude_any,
        sort_order=sort_order,
        sort_by=sort_by,
    )


def resolve_goal_selection(
    selected_goals: Any,
    *,
    config_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve selected goal IDs into SearchFilters overlays.

    Returns a dict with:
      - goal_ids
      - goal_labels
      - goal_filter_clauses
      - goal_must_not_clauses
      - goal_sort_order
      - goal_sort_by (best-effort compatibility)
      - goal_config_version
    """
    goal_ids = _normalize_goal_ids(selected_goals)
    if not goal_ids:
        return {
            "goal_ids": [],
            "goal_labels": [],
            "goal_filter_clauses": [],
            "goal_must_not_clauses": [],
            "goal_sort_order": None,
            "goal_sort_by": None,
            "goal_config_version": None,
        }

    payload = config_payload if config_payload is not None else fetch_goal_config()
    goals_raw = payload.get("goals")
    if not isinstance(goals_raw, list):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal config payload missing 'goals' array",
            500,
        )

    parsed_rules: Dict[str, GoalRule] = {}
    for raw_goal in goals_raw:
        if not isinstance(raw_goal, dict):
            continue
        rule = _parse_goal_rule(raw_goal)
        parsed_rules[rule.goal_id] = rule

    if not parsed_rules:
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal config has no valid goal definitions",
            500,
        )

    selected_rules: List[GoalRule] = []
    for goal_id in goal_ids:
        rule = parsed_rules.get(goal_id)
        if rule is None:
            raise GoalConfigError(
                "UNKNOWN_GOAL",
                f"Unknown goal selector '{goal_id}'",
                400,
            )
        if not rule.enabled:
            raise GoalConfigError(
                "GOAL_DISABLED",
                f"Goal selector '{goal_id}' is disabled",
                400,
            )
        selected_rules.append(rule)

    filter_clauses: List[Dict[str, Any]] = []
    must_not_clauses: List[Dict[str, Any]] = []
    goal_sort_order: Optional[List[Dict[str, str]]] = None
    goal_sort_by: Optional[str] = None
    for rule in selected_rules:
        for include_node in rule.include_all:
            filter_clauses.append(_node_to_clause(include_node))
        for exclude_node in rule.exclude_any:
            must_not_clauses.append(_node_to_clause(exclude_node))
        if goal_sort_order is None and rule.sort_order:
            goal_sort_order = [dict(item) for item in rule.sort_order]
        if not goal_sort_by and rule.sort_by:
            goal_sort_by = rule.sort_by

    config_version = payload.get("schema_version") or payload.get("version")
    _log.info(
        "shop_by_goal: resolved goals=%s version=%s sort=%s sort_order=%s",
        goal_ids,
        config_version,
        goal_sort_by,
        goal_sort_order,
    )
    return {
        "goal_ids": goal_ids,
        "goal_labels": [rule.label for rule in selected_rules],
        "goal_filter_clauses": filter_clauses,
        "goal_must_not_clauses": must_not_clauses,
        "goal_sort_order": goal_sort_order,
        "goal_sort_by": goal_sort_by,
        "goal_config_version": config_version,
    }


def resolve_diet_selection(
    selected_diets: Any,
    *,
    config_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve selected diet IDs into SearchFilters overlays.

    Returns a dict with:
      - diet_ids
      - diet_labels
      - diet_filter_clauses
      - diet_must_not_clauses
      - diet_sort_order
      - diet_sort_by (best-effort compatibility)
      - diet_config_version
    """
    diet_ids = _normalize_diet_ids(selected_diets)
    if not diet_ids:
        return {
            "diet_ids": [],
            "diet_labels": [],
            "diet_filter_clauses": [],
            "diet_must_not_clauses": [],
            "diet_sort_order": None,
            "diet_sort_by": None,
            "diet_config_version": None,
        }

    payload = config_payload if config_payload is not None else fetch_diet_config()
    diets_raw = payload.get("diets")
    if not isinstance(diets_raw, list):
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Diet config payload missing 'diets' array",
            500,
        )

    parsed_rules: Dict[str, DietRule] = {}
    for raw_diet in diets_raw:
        if not isinstance(raw_diet, dict):
            continue
        rule = _parse_diet_rule(raw_diet)
        parsed_rules[rule.diet_id] = rule

    if not parsed_rules:
        raise GoalConfigError(
            "GOAL_CONFIG_SCHEMA_INVALID",
            "Goal config has no valid diet definitions",
            500,
        )

    selected_rules: List[DietRule] = []
    for diet_id in diet_ids:
        rule = parsed_rules.get(diet_id)
        if rule is None:
            raise GoalConfigError(
                "UNKNOWN_DIET",
                f"Unknown diet selector '{diet_id}'",
                400,
            )
        if not rule.enabled:
            raise GoalConfigError(
                "DIET_DISABLED",
                f"Diet selector '{diet_id}' is disabled",
                400,
            )
        selected_rules.append(rule)

    filter_clauses: List[Dict[str, Any]] = []
    must_not_clauses: List[Dict[str, Any]] = []
    diet_sort_order: Optional[List[Dict[str, str]]] = None
    diet_sort_by: Optional[str] = None
    for rule in selected_rules:
        for include_node in rule.include_all:
            filter_clauses.append(_node_to_clause(include_node))
        for exclude_node in rule.exclude_any:
            must_not_clauses.append(_node_to_clause(exclude_node))
        if diet_sort_order is None and rule.sort_order:
            diet_sort_order = [dict(item) for item in rule.sort_order]
        if not diet_sort_by and rule.sort_by:
            diet_sort_by = rule.sort_by

    config_version = payload.get("schema_version") or payload.get("version")
    _log.info(
        "shop_by_goal: resolved diets=%s version=%s sort=%s sort_order=%s",
        diet_ids,
        config_version,
        diet_sort_by,
        diet_sort_order,
    )
    return {
        "diet_ids": diet_ids,
        "diet_labels": [rule.label for rule in selected_rules],
        "diet_filter_clauses": filter_clauses,
        "diet_must_not_clauses": must_not_clauses,
        "diet_sort_order": diet_sort_order,
        "diet_sort_by": diet_sort_by,
        "diet_config_version": config_version,
    }
