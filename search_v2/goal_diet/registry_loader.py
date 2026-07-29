from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from search_v2.goal_diet.types import CompiledGoalDietDefinition, CompiledRegistry
from search_v2.retrieval.filters import NUTRIENT_FIELD_MAP

_log = logging.getLogger(__name__)

DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"
GOALS_DIR = DEFINITIONS_DIR / "goals"
DIETS_DIR = DEFINITIONS_DIR / "diets"
_FIELDS_PATH = DEFINITIONS_DIR / "_fields.yaml"

_compiled_registry_cache: Optional[CompiledRegistry] = None
_percentile_field_map: Optional[Dict[str, str]] = None
_derived_metric_map: Optional[Dict[str, Dict[str, Any]]] = None

# Painless value expressions — computed at query time from indexed nutrient fields.
_DERIVED_METRIC_VALUE_EXPR: Dict[str, str] = {
    "net_carbs": (
        "doc['category_data.nutritional.nutri_breakdown_updated.carbs_g'].value"
        " - doc['category_data.nutritional.nutri_breakdown_updated.fiber_g'].value"
    ),
    "fat_cal_pct": (
        "(doc['category_data.nutritional.nutri_breakdown_updated.fat_g'].value * 9.0"
        " / doc['category_data.nutritional.nutri_breakdown_updated.energy_kcal'].value) * 100.0"
    ),
    "protein_cal_pct": (
        "(doc['category_data.nutritional.nutri_breakdown_updated.protein_g'].value * 4.0"
        " / doc['category_data.nutritional.nutri_breakdown_updated.energy_kcal'].value) * 100.0"
    ),
}

# Legacy nutrition_profiles API values → canonical goal/diet IDs.
NUTRITION_PROFILE_TO_GOAL_DIET: Dict[str, str] = {
    "high_protein": "high_protein",
    "high_fiber": "high_fiber",
    "low_carb": "keto",
    "low_sugar": "low_sugar",
    "low_sodium": "low_sodium",
    "low_fat": "low_fat",
}


def get_compiled_registry(*, force_reload: bool = False) -> CompiledRegistry:
    """Lazy singleton — mirrors load_scoring_rules() pattern."""
    global _compiled_registry_cache, _percentile_field_map, _derived_metric_map
    if _compiled_registry_cache is not None and not force_reload:
        return _compiled_registry_cache
    _percentile_field_map = _load_percentile_field_map()
    _derived_metric_map = _load_derived_metric_map()
    _compiled_registry_cache = _compile_registry()
    return _compiled_registry_cache


def get_default_sort_for_goal_diet_ids(ids: Optional[List[str]]) -> Optional[str]:
    if not ids:
        return None
    from search_v2.goal_diet.merge import merge_goal_diet_plans

    return merge_goal_diet_plans(ids).default_sort


def get_all_triggers() -> Tuple[str, ...]:
    """All trigger phrases — used by typo correction protected-word list."""
    registry = get_compiled_registry()
    phrases: List[str] = []
    for definition in registry.definitions.values():
        phrases.extend(definition.triggers)
    return tuple(dict.fromkeys(phrases))


def _load_percentile_field_map() -> Dict[str, str]:
    if not _FIELDS_PATH.is_file():
        return {}
    raw = yaml.safe_load(_FIELDS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    fields = raw.get("percentile_fields")
    if not isinstance(fields, dict):
        return {}
    return {str(k): str(v) for k, v in fields.items()}


def _load_derived_metric_map() -> Dict[str, Dict[str, Any]]:
    if not _FIELDS_PATH.is_file():
        return {}
    raw = yaml.safe_load(_FIELDS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    metrics = raw.get("derived_metrics")
    if not isinstance(metrics, dict):
        return {}
    return {str(k): v for k, v in metrics.items() if isinstance(v, dict)}


def _compile_registry() -> CompiledRegistry:
    definitions: Dict[str, CompiledGoalDietDefinition] = {}
    trigger_index: List[Tuple[re.Pattern, str, str]] = []

    for yaml_path in sorted(GOALS_DIR.glob("*.yaml")) + sorted(DIETS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid registry file (not a mapping): {yaml_path}")
        definition = _compile_definition(raw, yaml_path)
        if definition.id in definitions:
            raise ValueError(f"Duplicate goal/diet id '{definition.id}' in {yaml_path}")
        definitions[definition.id] = definition
        for trigger, pattern in zip(definition.triggers, definition.trigger_patterns):
            trigger_index.append((pattern, definition.id, trigger))

    trigger_index.sort(key=lambda item: len(item[2]), reverse=True)

    _log.info(
        "goal_diet.registry: compiled %d definitions (%d goals, %d diets)",
        len(definitions),
        sum(1 for d in definitions.values() if d.kind == "goal"),
        sum(1 for d in definitions.values() if d.kind == "diet"),
    )
    return CompiledRegistry(
        definitions=definitions,
        trigger_index=tuple(trigger_index),
    )


def _compile_definition(raw: Dict[str, Any], path: Path) -> CompiledGoalDietDefinition:
    goal_id = str(raw.get("id") or "").strip()
    if not goal_id:
        raise ValueError(f"Missing id in {path}")

    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ("goal", "diet"):
        raise ValueError(f"Invalid kind '{kind}' in {path} — expected goal or diet")

    display_name = str(raw.get("display_name") or goal_id.replace("_", " ").title())
    triggers_raw = raw.get("triggers") or []
    if not isinstance(triggers_raw, list) or not triggers_raw:
        raise ValueError(f"Missing triggers in {path}")

    triggers = tuple(str(t).strip().lower() for t in triggers_raw if str(t).strip())
    patterns = tuple(re.compile(r"\b" + re.escape(trigger) + r"\b") for trigger in triggers)

    filters = raw.get("filters") or {}
    include_raw = filters.get("include") or []
    exclude_raw = filters.get("exclude") or []
    if not isinstance(include_raw, list):
        raise ValueError(f"filters.include must be a list in {path}")

    include_clauses = tuple(_compile_filter_entry(entry, path) for entry in include_raw)
    exclude_clauses = tuple(
        _compile_filter_entry(entry, path) for entry in (exclude_raw if isinstance(exclude_raw, list) else [])
    )

    default_sort = raw.get("sort")
    if default_sort is not None:
        default_sort = str(default_sort).strip() or None

    return CompiledGoalDietDefinition(
        id=goal_id,
        kind=kind,
        display_name=display_name,
        triggers=triggers,
        trigger_patterns=patterns,
        include_clauses=include_clauses,
        exclude_clauses=exclude_clauses,
        default_sort=default_sort,
    )


def _resolve_percentile_field(name: str) -> str:
    field_map = _percentile_field_map or {}
    resolved = field_map.get(name)
    if not resolved:
        raise ValueError(f"Unknown percentile field alias '{name}'")
    return resolved


def _compile_derived_metric_clause(metric_name: str, bounds: Dict[str, Any], path: Path) -> Dict[str, Any]:
    metric_key = str(metric_name).strip()
    metric_map = _derived_metric_map or {}
    if metric_key not in _DERIVED_METRIC_VALUE_EXPR:
        raise ValueError(f"Unknown derived metric '{metric_key}' in {path}")

    requires_raw = metric_map.get(metric_key, {}).get("requires") or []
    if not isinstance(requires_raw, list):
        raise ValueError(f"derived_metrics.{metric_key}.requires must be a list in _fields.yaml")

    existence_checks = [
        f"doc.containsKey('{field}') && !doc['{field}'].empty"
        for field in requires_raw
        if str(field).strip()
    ]
    guard = " && ".join(existence_checks) if existence_checks else "true"

    value_expr = _DERIVED_METRIC_VALUE_EXPR[metric_key]
    if metric_key in ("fat_cal_pct", "protein_cal_pct"):
        guard = (
            f"{guard} && doc['category_data.nutritional.nutri_breakdown_updated.energy_kcal'].value > 0"
            if existence_checks
            else guard
        )

    comparisons: List[str] = []
    params: Dict[str, Any] = {}
    _PAINLESS_OPS = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}
    for op, bound in bounds.items():
        op_key = str(op).strip()
        if op_key not in _PAINLESS_OPS:
            raise ValueError(f"Unsupported derived_metric operator '{op_key}' in {path}")
        param_name = f"{op_key}_{metric_key}"
        params[param_name] = bound
        comparisons.append(f"value {_PAINLESS_OPS[op_key]} params.{param_name}")

    compare_expr = " && ".join(comparisons)
    source = f"""
        if (!({guard})) {{
            return false;
        }}
        double value = {value_expr};
        return {compare_expr};
    """

    return {
        "script": {
            "script": {
                "lang": "painless",
                "source": source,
                "params": params,
            }
        }
    }


def _compile_filter_entry(entry: Any, path: Path) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError(f"Filter entry must be a mapping in {path}: {entry!r}")

    if "any_of" in entry:
        children = entry["any_of"]
        if not isinstance(children, list) or not children:
            raise ValueError(f"any_of must be a non-empty list in {path}")
        clauses = [_compile_filter_entry(child, path) for child in children]
        return {"bool": {"should": clauses, "minimum_should_match": 1}}

    if "all_of" in entry:
        children = entry["all_of"]
        if not isinstance(children, list) or not children:
            raise ValueError(f"all_of must be a non-empty list in {path}")
        clauses = [_compile_filter_entry(child, path) for child in children]
        return {"bool": {"must": clauses}}

    if "nutrient" in entry:
        spec = entry["nutrient"]
        if not isinstance(spec, dict) or not spec:
            raise ValueError(f"nutrient filter must be a non-empty mapping in {path}")
        es_range: Dict[str, Any] = {}
        for key, bounds in spec.items():
            if not isinstance(bounds, dict):
                raise ValueError(f"nutrient bounds must be a mapping in {path}")
            es_field = NUTRIENT_FIELD_MAP.get(str(key), str(key))
            es_range[es_field] = dict(bounds)
        return {"range": es_range}

    if "derived_metric" in entry:
        spec = entry["derived_metric"]
        if not isinstance(spec, dict) or not spec:
            raise ValueError(f"derived_metric filter must be a non-empty mapping in {path}")
        if len(spec) != 1:
            raise ValueError(f"derived_metric entry must specify exactly one metric in {path}")
        metric_name, bounds = next(iter(spec.items()))
        if not isinstance(bounds, dict):
            raise ValueError(f"derived_metric bounds must be a mapping in {path}")
        return _compile_derived_metric_clause(str(metric_name), dict(bounds), path)

    if "percentile" in entry:
        spec = entry["percentile"]
        if not isinstance(spec, dict) or not spec:
            raise ValueError(f"percentile filter must be a non-empty mapping in {path}")
        es_range: Dict[str, Any] = {}
        for alias, bounds in spec.items():
            if not isinstance(bounds, dict):
                raise ValueError(f"percentile bounds must be a mapping in {path}")
            es_field = _resolve_percentile_field(str(alias))
            es_range[es_field] = dict(bounds)
        return {"range": es_range}

    if "flean_score" in entry:
        spec = entry["flean_score"]
        if not isinstance(spec, dict) or not spec:
            raise ValueError(f"flean_score filter must be a non-empty mapping in {path}")
        es_range: Dict[str, Any] = {}
        for field_name, bounds in spec.items():
            if not isinstance(bounds, dict):
                raise ValueError(f"flean_score bounds must be a mapping in {path}")
            es_range[f"flean_score.{field_name}"] = dict(bounds)
        return {"range": es_range}

    if "flean_bonus" in entry:
        spec = entry["flean_bonus"]
        if not isinstance(spec, dict) or not spec:
            raise ValueError(f"flean_bonus filter must be a non-empty mapping in {path}")
        es_range: Dict[str, Any] = {}
        for field_name, bounds in spec.items():
            if not isinstance(bounds, dict):
                raise ValueError(f"flean_bonus bounds must be a mapping in {path}")
            es_range[f"flean_score.bonuses.{field_name}"] = dict(bounds)
        return {"range": es_range}

    if "flean_penalty" in entry:
        spec = entry["flean_penalty"]
        if not isinstance(spec, dict) or not spec:
            raise ValueError(f"flean_penalty filter must be a non-empty mapping in {path}")
        es_range: Dict[str, Any] = {}
        for field_name, bounds in spec.items():
            if not isinstance(bounds, dict):
                raise ValueError(f"flean_penalty bounds must be a mapping in {path}")
            es_range[f"flean_score.penalties.{field_name}"] = dict(bounds)
        return {"range": es_range}

    if "processing_type" in entry:
        value = entry["processing_type"]
        if isinstance(value, dict) and "not" in value:
            blocked = str(value["not"]).strip()
            return {"bool": {"must_not": [{"term": {"category_data.processing_type": blocked}}]}}
        return {"term": {"category_data.processing_type": str(value).strip()}}

    if "dietary_label" in entry:
        return {"term": {"category_data.dietary_label": str(entry["dietary_label"]).strip().lower()}}

    if "ingredient_text_any" in entry:
        phrases = entry["ingredient_text_any"]
        if not isinstance(phrases, list) or not phrases:
            raise ValueError(f"ingredient_text_any must be a non-empty list in {path}")
        return {
            "bool": {
                "should": [
                    {"match_phrase": {"ingredients.raw_text": str(phrase).strip()}}
                    for phrase in phrases
                    if str(phrase).strip()
                ],
                "minimum_should_match": 1,
            }
        }

    if "ingredient_text_exclude" in entry:
        phrases = entry["ingredient_text_exclude"]
        if not isinstance(phrases, list) or not phrases:
            raise ValueError(f"ingredient_text_exclude must be a non-empty list in {path}")
        return {
            "bool": {
                "must_not": [
                    {"match_phrase": {"ingredients.raw_text": str(phrase).strip()}}
                    for phrase in phrases
                    if str(phrase).strip()
                ]
            }
        }

    if "exists" in entry:
        return {"exists": {"field": str(entry["exists"]).strip()}}

    if "dietary_tag" in entry:
        tag = str(entry["dietary_tag"]).strip().lower().replace("-", "_").replace(" ", "_")
        return {
            "bool": {
                "should": [
                    {"term": {"category_data.tags.dietary_tags": tag}},
                    {"term": {"category_data.tags.dietary_tags.keyword": tag}},
                ],
                "minimum_should_match": 1,
            }
        }

    if "ingredient_tag" in entry:
        tag = str(entry["ingredient_tag"]).strip().lower().replace("-", "_").replace(" ", "_")
        return {
            "bool": {
                "should": [
                    {"term": {"category_data.tags.ingredient_tags": tag}},
                    {"term": {"category_data.tags.ingredient_tags.keyword": tag}},
                ],
                "minimum_should_match": 1,
            }
        }

    if "range" in entry:
        range_spec = entry["range"]
        if not isinstance(range_spec, dict):
            raise ValueError(f"range filter must be a mapping in {path}")
        es_range = {}
        for field, bounds in range_spec.items():
            if not isinstance(bounds, dict):
                raise ValueError(f"range bounds must be a mapping in {path}")
            es_range[str(field)] = dict(bounds)
        return {"range": es_range}

    if "term" in entry:
        term_spec = entry["term"]
        if not isinstance(term_spec, dict):
            raise ValueError(f"term filter must be a mapping in {path}")
        return {"term": dict(term_spec)}

    if "match_phrase" in entry:
        match_spec = entry["match_phrase"]
        if not isinstance(match_spec, dict):
            raise ValueError(f"match_phrase filter must be a mapping in {path}")
        return {"match_phrase": dict(match_spec)}

    raise ValueError(f"Unsupported filter entry in {path}: {entry!r}")
