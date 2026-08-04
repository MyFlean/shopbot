"""
Search V2 category hierarchy utilities.

This module provides helpers for category-scoped flows that rely only on
`category_hierarchies.segments`:
  - department match at segments[1]
  - category match at segments[2]
  - returned subcategory values at segments[3]
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union
from urllib import error as urllib_error
from urllib import request as urllib_request

_SUBCATEGORY_TERMS_LIMIT = 500
_SUBCATEGORY_AGG_NAME = "category_hierarchy_nested"
_SUBCATEGORY_FILTER_NAME = "category_scope"
_SUBCATEGORY_BUCKETS_NAME = "subcategory_candidates"
_CATEGORY_AGG_NAME = "department_hierarchy_nested"
_CATEGORY_FILTER_NAME = "department_scope"
_CATEGORY_BUCKETS_NAME = "category_candidates"
_DEPARTMENT_AGG_NAME = "departments_hierarchy_nested"
_DEPARTMENT_BUCKETS_NAME = "department_candidates"
_APP_CONFIG_CATEGORIES_URL = "https://api.flean.ai/ui/app-config/categories"
_APP_CONFIG_TIMEOUT_SEC = 10.0

HierarchyScope = Union[str, Sequence[str]]

_log = logging.getLogger(__name__)


def _normalize_scope_values(scope: HierarchyScope) -> List[str]:
    if isinstance(scope, str):
        raw_values: Iterable[Any] = [scope]
    else:
        raw_values = scope
    out: List[str] = []
    seen: set[str] = set()
    for value in raw_values:
        normalized = str(value or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _segments_scope_filter(values: List[str]) -> Dict[str, Any]:
    if len(values) == 1:
        return {"term": {"category_hierarchies.segments": values[0]}}
    return {"terms": {"category_hierarchies.segments": values}}


def _counted_segment_metric(
    *,
    state_key: str,
    parents_param: str,
    parent_index: int,
    child_index: int,
    parents: List[str],
) -> Dict[str, Any]:
    """Scripted metric collecting parent-doc counts for a child segment index."""
    return {
        "scripted_metric": {
            "params": {
                parents_param: parents,
                "limit": _SUBCATEGORY_TERMS_LIMIT,
            },
            "init_script": f"state.{state_key} = new HashMap();",
            "map_script": (
                f"def parents = new HashSet(); "
                f"for (def p : params.{parents_param}) {{ parents.add(p); }} "
                "def rows = params._source['category_hierarchies']; "
                "if (rows == null) return; "
                f"def seen = new HashSet(); "
                "for (def row : rows) { "
                "  if (row == null) continue; "
                "  def segs = row['segments']; "
                f"  if (segs == null || segs.size() <= {child_index}) continue; "
                f"  def parent = segs[{parent_index}]; "
                f"  def child = segs[{child_index}]; "
                "  if (parent == null || child == null) continue; "
                "  def parentKey = parent.toString().toLowerCase(); "
                "  def childKey = child.toString().toLowerCase(); "
                "  if (!parents.contains(parentKey) || seen.contains(childKey)) continue; "
                "  seen.add(childKey); "
                f"  if (state.{state_key}.size() >= params.limit && !state.{state_key}.containsKey(childKey)) continue; "
                f"  def current = state.{state_key}.getOrDefault(childKey, 0); "
                f"  state.{state_key}.put(childKey, current + 1); "
                "}"
            ),
            "combine_script": f"return state.{state_key};",
            "reduce_script": (
                "def out = new HashMap(); "
                "for (def s : states) { "
                "  if (s == null) continue; "
                "  for (def entry : s.entrySet()) { "
                "    def key = entry.getKey(); "
                "    def add = entry.getValue(); "
                "    def current = out.getOrDefault(key, 0); "
                "    out.put(key, current + add); "
                "  } "
                "} "
                "return out;"
            ),
        }
    }


def build_subcategory_terms_aggregation(category: HierarchyScope) -> Dict[str, Any]:
    """
    Build nested aggregation that extracts category-scoped subcategory terms.

    The aggregation runs over `category_hierarchies` nested docs and emits
    strict segment[3] values from rows that contain the requested segment[2]
    category token(s).
    """
    parents = _normalize_scope_values(category)
    if not parents:
        return {}
    return {
        _SUBCATEGORY_AGG_NAME: {
            "nested": {"path": "category_hierarchies"},
            "aggs": {
                _SUBCATEGORY_FILTER_NAME: {
                    "filter": _segments_scope_filter(parents),
                    "aggs": {
                        _SUBCATEGORY_BUCKETS_NAME: {
                            "reverse_nested": {},
                            "aggs": {
                                "segment3_values": _counted_segment_metric(
                                    state_key="subcategories",
                                    parents_param="categories",
                                    parent_index=2,
                                    child_index=3,
                                    parents=parents,
                                )
                            },
                        }
                    },
                }
            },
        }
    }


def _parse_segment_counts_from_bucket(
    parsed: Dict[str, Any],
    scripted_metric_key: str,
) -> Dict[str, int]:
    """Parse id -> count from terms buckets or scripted_metric map/list values."""
    counts: Dict[str, int] = {}
    if not isinstance(parsed, dict):
        return counts

    scripted = parsed.get(scripted_metric_key)
    raw_values = (scripted or {}).get("value") if isinstance(scripted, dict) else parsed.get("value")
    if isinstance(raw_values, dict):
        for key, count in raw_values.items():
            normalized = str(key or "").strip().lower()
            if not normalized:
                continue
            try:
                counts[normalized] = int(count or 0)
            except (TypeError, ValueError):
                counts[normalized] = 0
        return counts
    if isinstance(raw_values, (list, set, tuple)):
        for value in raw_values:
            if isinstance(value, dict):
                key = str(value.get("key") or "").strip().lower()
                try:
                    count = int(value.get("count") or 0)
                except (TypeError, ValueError):
                    count = 0
            else:
                key = str(value or "").strip().lower()
                count = 1
            if key:
                counts[key] = max(counts.get(key, 0), count)
        return counts

    buckets = parsed.get("buckets") or []
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            key = str(bucket.get("key") or "").strip().lower()
            if not key:
                continue
            try:
                counts[key] = int(bucket.get("doc_count", 0) or 0)
            except (TypeError, ValueError):
                counts[key] = 0
    return counts


def _parse_segment_values_from_bucket(
    parsed: Dict[str, Any],
    scripted_metric_key: str,
) -> List[str]:
    return list(_parse_segment_counts_from_bucket(parsed, scripted_metric_key).keys())


def parse_subcategories_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
    category: HierarchyScope,
) -> List[str]:
    """
    Parse unique subcategories from category_hierarchies aggregation response.

    Expected input shape:
      aggregations.category_hierarchy_nested.category_scope
        .subcategory_candidates.buckets[]
    """
    if not _normalize_scope_values(category):
        return []

    agg_root = (aggregations or {}).get(_SUBCATEGORY_AGG_NAME) or {}
    category_scope = agg_root.get(_SUBCATEGORY_FILTER_NAME) or {}
    parsed = category_scope.get(_SUBCATEGORY_BUCKETS_NAME) or {}
    if not isinstance(parsed, dict):
        return []
    return _parse_segment_values_from_bucket(parsed, "segment3_values")


def parse_subcategory_counts_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
    category: HierarchyScope,
) -> Dict[str, int]:
    if not _normalize_scope_values(category):
        return {}
    agg_root = (aggregations or {}).get(_SUBCATEGORY_AGG_NAME) or {}
    category_scope = agg_root.get(_SUBCATEGORY_FILTER_NAME) or {}
    parsed = category_scope.get(_SUBCATEGORY_BUCKETS_NAME) or {}
    if not isinstance(parsed, dict):
        return {}
    return _parse_segment_counts_from_bucket(parsed, "segment3_values")


def build_category_terms_aggregation(department: HierarchyScope) -> Dict[str, Any]:
    """
    Build nested aggregation that extracts department-scoped category terms.

    The aggregation runs over `category_hierarchies` nested docs and emits
    strict segment[2] values from rows that contain the requested segment[1]
    department token(s).
    """
    parents = _normalize_scope_values(department)
    if not parents:
        return {}
    return {
        _CATEGORY_AGG_NAME: {
            "nested": {"path": "category_hierarchies"},
            "aggs": {
                _CATEGORY_FILTER_NAME: {
                    "filter": _segments_scope_filter(parents),
                    "aggs": {
                        _CATEGORY_BUCKETS_NAME: {
                            "reverse_nested": {},
                            "aggs": {
                                "segment2_values": _counted_segment_metric(
                                    state_key="categories",
                                    parents_param="departments",
                                    parent_index=1,
                                    child_index=2,
                                    parents=parents,
                                )
                            },
                        }
                    },
                }
            },
        }
    }


def parse_categories_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
    department: HierarchyScope,
) -> List[str]:
    """
    Parse unique categories from department-scoped aggregation response.

    Expected input shape:
      aggregations.department_hierarchy_nested.department_scope
        .category_candidates.segment2_values.value[]
    """
    if not _normalize_scope_values(department):
        return []

    agg_root = (aggregations or {}).get(_CATEGORY_AGG_NAME) or {}
    department_scope = agg_root.get(_CATEGORY_FILTER_NAME) or {}
    parsed = department_scope.get(_CATEGORY_BUCKETS_NAME) or {}
    if not isinstance(parsed, dict):
        return []
    return _parse_segment_values_from_bucket(parsed, "segment2_values")


def parse_category_counts_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
    department: HierarchyScope,
) -> Dict[str, int]:
    if not _normalize_scope_values(department):
        return {}
    agg_root = (aggregations or {}).get(_CATEGORY_AGG_NAME) or {}
    department_scope = agg_root.get(_CATEGORY_FILTER_NAME) or {}
    parsed = department_scope.get(_CATEGORY_BUCKETS_NAME) or {}
    if not isinstance(parsed, dict):
        return {}
    return _parse_segment_counts_from_bucket(parsed, "segment2_values")


def build_department_terms_aggregation() -> Dict[str, Any]:
    """Build nested aggregation that extracts department tokens at segments[1]."""
    return {
        _DEPARTMENT_AGG_NAME: {
            "nested": {"path": "category_hierarchies"},
            "aggs": {
                _DEPARTMENT_BUCKETS_NAME: {
                    "reverse_nested": {},
                    "aggs": {
                        "segment1_values": {
                            "scripted_metric": {
                                "params": {"limit": _SUBCATEGORY_TERMS_LIMIT},
                                "init_script": "state.departments = new HashMap();",
                                "map_script": (
                                    "def rows = params._source['category_hierarchies']; "
                                    "if (rows == null) return; "
                                    "def seen = new HashSet(); "
                                    "for (def row : rows) { "
                                    "  if (row == null) continue; "
                                    "  def segs = row['segments']; "
                                    "  if (segs == null || segs.size() <= 1) continue; "
                                    "  def l1 = segs[1]; "
                                    "  if (l1 == null) continue; "
                                    "  def key = l1.toString().toLowerCase(); "
                                    "  if (seen.contains(key)) continue; "
                                    "  seen.add(key); "
                                    "  if (state.departments.size() >= params.limit "
                                    "      && !state.departments.containsKey(key)) continue; "
                                    "  def current = state.departments.getOrDefault(key, 0); "
                                    "  state.departments.put(key, current + 1); "
                                    "}"
                                ),
                                "combine_script": "return state.departments;",
                                "reduce_script": (
                                    "def out = new HashMap(); "
                                    "for (def s : states) { "
                                    "  if (s == null) continue; "
                                    "  for (def entry : s.entrySet()) { "
                                    "    def key = entry.getKey(); "
                                    "    def add = entry.getValue(); "
                                    "    def current = out.getOrDefault(key, 0); "
                                    "    out.put(key, current + add); "
                                    "  } "
                                    "} "
                                    "return out;"
                                ),
                            }
                        }
                    },
                }
            },
        }
    }


def parse_departments_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
) -> List[str]:
    counts = parse_department_counts_from_aggregations(aggregations)
    return list(counts.keys())


def parse_department_counts_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
) -> Dict[str, int]:
    agg_root = (aggregations or {}).get(_DEPARTMENT_AGG_NAME) or {}
    parsed = agg_root.get(_DEPARTMENT_BUCKETS_NAME) or {}
    if not isinstance(parsed, dict):
        return {}
    return _parse_segment_counts_from_bucket(parsed, "segment1_values")


def fetch_app_config_categories(
    url: str = _APP_CONFIG_CATEGORIES_URL,
    timeout_sec: float = _APP_CONFIG_TIMEOUT_SEC,
) -> List[Dict[str, Any]]:
    """Fetch category metadata from app-config endpoint."""
    try:
        with urllib_request.urlopen(url, timeout=timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, ValueError) as exc:
        _log.warning("taxonomy: failed to fetch app-config categories (%s)", exc)
        return []
    except Exception as exc:  # defensive fallback
        _log.warning("taxonomy: unexpected app-config fetch error (%s)", exc)
        return []

    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def sync_subcategory_metadata_with_app_config(
    category: str,
    es_subcategory_ids: List[str],
    categories_payload: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """
    Return subcategories as ordered metadata objects (`id`, `image`, `name`).

    The returned list is the intersection of:
      - ES-derived subcategory ids
      - app-config category.subcategories ids
    Ordering follows app-config.
    """
    category_id = str(category or "").strip().lower()
    if not category_id:
        return []

    normalized_es_ids = {
        str(item or "").strip().lower()
        for item in (es_subcategory_ids or [])
        if str(item or "").strip()
    }
    if not normalized_es_ids:
        return []

    categories = categories_payload if categories_payload is not None else fetch_app_config_categories()
    if not isinstance(categories, list):
        return []

    target_category: Optional[Dict[str, Any]] = None
    for entry in categories:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or "").strip().lower() == category_id:
            target_category = entry
            break

    if not target_category:
        return []

    subcategories = target_category.get("subcategories")
    if not isinstance(subcategories, list):
        return []

    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for subcategory in subcategories:
        if not isinstance(subcategory, dict):
            continue
        raw_id = str(subcategory.get("id") or "").strip()
        normalized_id = raw_id.lower()
        if not raw_id or normalized_id not in normalized_es_ids or normalized_id in seen:
            continue
        out.append(
            {
                "id": raw_id,
                "image": str(subcategory.get("image") or "").strip(),
                "name": str(subcategory.get("name") or "").strip(),
            }
        )
        seen.add(normalized_id)
    return out


def sync_category_metadata_with_app_config(
    department: str,
    es_category_ids: List[str],
    categories_payload: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """
    Return categories as ordered metadata objects (`id`, `image`, `name`).

    The returned list is the intersection of:
      - ES-derived category ids under the department
      - app-config top-level category ids
    Ordering follows app-config.
    """
    department_id = str(department or "").strip().lower()
    if not department_id:
        return []

    normalized_es_ids = {
        str(item or "").strip().lower()
        for item in (es_category_ids or [])
        if str(item or "").strip()
    }
    if not normalized_es_ids:
        return []

    categories = categories_payload if categories_payload is not None else fetch_app_config_categories()
    if not isinstance(categories, list):
        return []

    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for entry in categories:
        if not isinstance(entry, dict):
            continue
        raw_id = str(entry.get("id") or "").strip()
        normalized_id = raw_id.lower()
        if not raw_id or normalized_id not in normalized_es_ids or normalized_id in seen:
            continue
        out.append(
            {
                "id": raw_id,
                "image": str(entry.get("image") or "").strip(),
                "name": str(entry.get("name") or "").strip(),
            }
        )
        seen.add(normalized_id)
    return out
