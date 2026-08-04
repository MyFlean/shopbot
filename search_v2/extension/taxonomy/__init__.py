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
from typing import Any, Dict, List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

_SUBCATEGORY_TERMS_LIMIT = 500
_SUBCATEGORY_AGG_NAME = "category_hierarchy_nested"
_SUBCATEGORY_FILTER_NAME = "category_scope"
_SUBCATEGORY_BUCKETS_NAME = "subcategory_candidates"
_CATEGORY_AGG_NAME = "department_hierarchy_nested"
_CATEGORY_FILTER_NAME = "department_scope"
_CATEGORY_BUCKETS_NAME = "category_candidates"
_DEPARTMENT_SUBCATEGORY_AGG_NAME = "department_subcategory_hierarchy_nested"
_DEPARTMENT_SUBCATEGORY_FILTER_NAME = "department_subcategory_scope"
_DEPARTMENT_SUBCATEGORY_BUCKETS_NAME = "department_subcategory_candidates"
_APP_CONFIG_CATEGORIES_URL = "https://api.flean.ai/ui/app-config/categories"
_APP_CONFIG_TIMEOUT_SEC = 10.0

_log = logging.getLogger(__name__)


def build_subcategory_terms_aggregation(category: str) -> Dict[str, Any]:
    """
    Build nested aggregation that extracts category-scoped subcategory terms.

    The aggregation runs over `category_hierarchies` nested docs and emits
    strict segment[3] values from rows that contain the requested segment[2]
    category token.
    """
    normalized = str(category or "").strip().lower()
    if not normalized:
        return {}
    return {
        _SUBCATEGORY_AGG_NAME: {
            "nested": {"path": "category_hierarchies"},
            "aggs": {
                _SUBCATEGORY_FILTER_NAME: {
                    "filter": {
                        "term": {"category_hierarchies.segments": normalized}
                    },
                    "aggs": {
                        _SUBCATEGORY_BUCKETS_NAME: {
                            "reverse_nested": {},
                            "aggs": {
                                "segment3_values": {
                                    "scripted_metric": {
                                        "params": {
                                            "category": normalized,
                                            "limit": _SUBCATEGORY_TERMS_LIMIT,
                                        },
                                        "init_script": "state.subcategories = new HashSet();",
                                        "map_script": (
                                            "def rows = params._source['category_hierarchies']; "
                                            "if (rows == null) return; "
                                            "for (def row : rows) { "
                                            "  if (row == null) continue; "
                                            "  def segs = row['segments']; "
                                            "  if (segs == null || segs.size() <= 3) continue; "
                                            "  def l2 = segs[2]; "
                                            "  def l3 = segs[3]; "
                                            "  if (l2 == null || l3 == null) continue; "
                                            "  if (l2.toString().toLowerCase() == params.category) { "
                                            "    if (state.subcategories.size() < params.limit) { "
                                            "      state.subcategories.add(l3.toString().toLowerCase()); "
                                            "    } "
                                            "  } "
                                            "}"
                                        ),
                                        "combine_script": "return state.subcategories;",
                                        "reduce_script": (
                                            "def out = new HashSet(); "
                                            "for (def s : states) { "
                                            "  if (s == null) continue; "
                                            "  out.addAll(s); "
                                            "} "
                                            "return out;"
                                        ),
                                    }
                                }
                            }
                        }
                    },
                }
            },
        }
    }


def _parse_segment_values_from_bucket(
    parsed: Dict[str, Any],
    scripted_metric_key: str,
) -> List[str]:
    out: List[str] = []
    if not isinstance(parsed, dict):
        return out
    # Support both terms-buckets and scripted_metric return shapes.
    scripted = parsed.get(scripted_metric_key)
    raw_values = (scripted or {}).get("value") if isinstance(scripted, dict) else parsed.get("value")
    if isinstance(raw_values, (list, set, tuple)):
        for value in raw_values:
            key = str(value or "").strip().lower()
            if key and key not in out:
                out.append(key)
        return out

    buckets = parsed.get("buckets") or []
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            key = str(bucket.get("key") or "").strip().lower()
            if not key:
                continue
            if key not in out:
                out.append(key)
    return out


def parse_subcategories_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
    category: str,
) -> List[str]:
    """
    Parse unique subcategories from category_hierarchies aggregation response.

    Expected input shape:
      aggregations.category_hierarchy_nested.category_scope
        .subcategory_candidates.buckets[]
    """
    normalized = str(category or "").strip().lower()
    if not normalized:
        return []

    agg_root = (aggregations or {}).get(_SUBCATEGORY_AGG_NAME) or {}
    category_scope = agg_root.get(_SUBCATEGORY_FILTER_NAME) or {}
    parsed = category_scope.get(_SUBCATEGORY_BUCKETS_NAME) or {}
    if not isinstance(parsed, dict):
        return []
    return _parse_segment_values_from_bucket(parsed, "segment3_values")


def build_category_terms_aggregation(department: str) -> Dict[str, Any]:
    """
    Build nested aggregation that extracts department-scoped category terms.

    The aggregation runs over `category_hierarchies` nested docs and emits
    strict segment[2] values from rows that contain the requested segment[1]
    department token.
    """
    normalized = str(department or "").strip().lower()
    if not normalized:
        return {}
    return {
        _CATEGORY_AGG_NAME: {
            "nested": {"path": "category_hierarchies"},
            "aggs": {
                _CATEGORY_FILTER_NAME: {
                    "filter": {
                        "term": {"category_hierarchies.segments": normalized}
                    },
                    "aggs": {
                        _CATEGORY_BUCKETS_NAME: {
                            "reverse_nested": {},
                            "aggs": {
                                "segment2_values": {
                                    "scripted_metric": {
                                        "params": {
                                            "department": normalized,
                                            "limit": _SUBCATEGORY_TERMS_LIMIT,
                                        },
                                        "init_script": "state.categories = new HashSet();",
                                        "map_script": (
                                            "def rows = params._source['category_hierarchies']; "
                                            "if (rows == null) return; "
                                            "for (def row : rows) { "
                                            "  if (row == null) continue; "
                                            "  def segs = row['segments']; "
                                            "  if (segs == null || segs.size() <= 2) continue; "
                                            "  def l1 = segs[1]; "
                                            "  def l2 = segs[2]; "
                                            "  if (l1 == null || l2 == null) continue; "
                                            "  if (l1.toString().toLowerCase() == params.department) { "
                                            "    if (state.categories.size() < params.limit) { "
                                            "      state.categories.add(l2.toString().toLowerCase()); "
                                            "    } "
                                            "  } "
                                            "}"
                                        ),
                                        "combine_script": "return state.categories;",
                                        "reduce_script": (
                                            "def out = new HashSet(); "
                                            "for (def s : states) { "
                                            "  if (s == null) continue; "
                                            "  out.addAll(s); "
                                            "} "
                                            "return out;"
                                        ),
                                    }
                                }
                            }
                        }
                    },
                }
            },
        }
    }


def parse_categories_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
    department: str,
) -> List[str]:
    """
    Parse unique categories from department-scoped aggregation response.

    Expected input shape:
      aggregations.department_hierarchy_nested.department_scope
        .category_candidates.segment2_values.value[]
    """
    normalized = str(department or "").strip().lower()
    if not normalized:
        return []

    agg_root = (aggregations or {}).get(_CATEGORY_AGG_NAME) or {}
    department_scope = agg_root.get(_CATEGORY_FILTER_NAME) or {}
    parsed = department_scope.get(_CATEGORY_BUCKETS_NAME) or {}
    if not isinstance(parsed, dict):
        return []
    return _parse_segment_values_from_bucket(parsed, "segment2_values")


def build_department_subcategory_terms_aggregation(department: str) -> Dict[str, Any]:
    """
    Build nested aggregation that extracts department-scoped category->subcategories.

    The aggregation runs over `category_hierarchies` nested docs and emits a
    scripted metric map:
      {
        "category_id_1": ["subcategory_a", "subcategory_b"],
        "category_id_2": ["subcategory_c"],
      }
    """
    normalized = str(department or "").strip().lower()
    if not normalized:
        return {}
    return {
        _DEPARTMENT_SUBCATEGORY_AGG_NAME: {
            "nested": {"path": "category_hierarchies"},
            "aggs": {
                _DEPARTMENT_SUBCATEGORY_FILTER_NAME: {
                    "filter": {
                        "term": {"category_hierarchies.segments": normalized}
                    },
                    "aggs": {
                        _DEPARTMENT_SUBCATEGORY_BUCKETS_NAME: {
                            "reverse_nested": {},
                            "aggs": {
                                "category_subcategory_values": {
                                    "scripted_metric": {
                                        "params": {
                                            "department": normalized,
                                            "limit": _SUBCATEGORY_TERMS_LIMIT,
                                        },
                                        "init_script": "state.category_map = new HashMap();",
                                        "map_script": (
                                            "def rows = params._source['category_hierarchies']; "
                                            "if (rows == null) return; "
                                            "for (def row : rows) { "
                                            "  if (row == null) continue; "
                                            "  def segs = row['segments']; "
                                            "  if (segs == null || segs.size() <= 3) continue; "
                                            "  def l1 = segs[1]; "
                                            "  def l2 = segs[2]; "
                                            "  def l3 = segs[3]; "
                                            "  if (l1 == null || l2 == null || l3 == null) continue; "
                                            "  if (l1.toString().toLowerCase() != params.department) continue; "
                                            "  def categoryKey = l2.toString().toLowerCase(); "
                                            "  def subcategoryValue = l3.toString().toLowerCase(); "
                                            "  def bucket = state.category_map.get(categoryKey); "
                                            "  if (bucket == null) { "
                                            "    bucket = new HashSet(); "
                                            "    state.category_map.put(categoryKey, bucket); "
                                            "  } "
                                            "  if (bucket.size() < params.limit) { "
                                            "    bucket.add(subcategoryValue); "
                                            "  } "
                                            "}"
                                        ),
                                        "combine_script": "return state.category_map;",
                                        "reduce_script": (
                                            "def out = new HashMap(); "
                                            "for (def m : states) { "
                                            "  if (m == null) continue; "
                                            "  for (def entry : m.entrySet()) { "
                                            "    def key = entry.getKey(); "
                                            "    def vals = entry.getValue(); "
                                            "    if (key == null || vals == null) continue; "
                                            "    def bucket = out.get(key); "
                                            "    if (bucket == null) { "
                                            "      bucket = new HashSet(); "
                                            "      out.put(key, bucket); "
                                            "    } "
                                            "    bucket.addAll(vals); "
                                            "  } "
                                            "} "
                                            "def normalized = new HashMap(); "
                                            "for (def entry : out.entrySet()) { "
                                            "  normalized.put(entry.getKey(), new ArrayList(entry.getValue())); "
                                            "} "
                                            "return normalized;"
                                        ),
                                    }
                                }
                            }
                        }
                    },
                }
            },
        }
    }


def parse_department_subcategories_from_aggregations(
    aggregations: Optional[Dict[str, Any]],
    department: str,
) -> Dict[str, List[str]]:
    """
    Parse unique category->subcategories map from department aggregation response.

    Expected input shape:
      aggregations.department_subcategory_hierarchy_nested.department_subcategory_scope
        .department_subcategory_candidates.category_subcategory_values.value
    """
    normalized = str(department or "").strip().lower()
    if not normalized:
        return {}

    agg_root = (aggregations or {}).get(_DEPARTMENT_SUBCATEGORY_AGG_NAME) or {}
    department_scope = agg_root.get(_DEPARTMENT_SUBCATEGORY_FILTER_NAME) or {}
    parsed = department_scope.get(_DEPARTMENT_SUBCATEGORY_BUCKETS_NAME) or {}
    scripted_metric = parsed.get("category_subcategory_values") if isinstance(parsed, dict) else None
    raw_map = (
        scripted_metric.get("value")
        if isinstance(scripted_metric, dict)
        else None
    )
    if not isinstance(raw_map, dict):
        return {}

    out: Dict[str, List[str]] = {}
    for raw_category, raw_subcategories in raw_map.items():
        category_id = str(raw_category or "").strip().lower()
        if not category_id:
            continue
        normalized_subcategories: List[str] = []
        if isinstance(raw_subcategories, (list, set, tuple)):
            for item in raw_subcategories:
                normalized_subcategory = str(item or "").strip().lower()
                if normalized_subcategory and normalized_subcategory not in normalized_subcategories:
                    normalized_subcategories.append(normalized_subcategory)
        if normalized_subcategories:
            out[category_id] = normalized_subcategories
    return out


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
                # App-config categories primarily expose top-level `icon`.
                "image": str(entry.get("image") or entry.get("icon") or "").strip(),
                "name": str(entry.get("name") or "").strip(),
            }
        )
        seen.add(normalized_id)
    return out


def sync_department_subcategory_metadata_with_app_config(
    department: str,
    es_subcategory_ids_by_category: Dict[str, List[str]],
    categories_payload: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Return department grouped subcategories in app-config order.

    Output shape:
      [
        {
          "category": {"id", "image", "name"},
          "items": [{"id", "image", "name"}],
        }
      ]
    """
    department_id = str(department or "").strip().lower()
    if not department_id:
        return []
    if not isinstance(es_subcategory_ids_by_category, dict) or not es_subcategory_ids_by_category:
        return []

    categories = categories_payload if categories_payload is not None else fetch_app_config_categories()
    if not isinstance(categories, list):
        return []

    category_ids = [
        str(category_id or "").strip().lower()
        for category_id in es_subcategory_ids_by_category.keys()
        if str(category_id or "").strip()
    ]
    category_metadata_rows = sync_category_metadata_with_app_config(
        department=department_id,
        es_category_ids=category_ids,
        categories_payload=categories,
    )
    category_metadata_by_id = {
        str(entry.get("id") or "").strip().lower(): entry
        for entry in category_metadata_rows
        if isinstance(entry, dict) and str(entry.get("id") or "").strip()
    }

    out: List[Dict[str, Any]] = []
    for category_entry in categories:
        if not isinstance(category_entry, dict):
            continue
        raw_category_id = str(category_entry.get("id") or "").strip()
        category_id = raw_category_id.lower()
        if not raw_category_id:
            continue
        es_ids = es_subcategory_ids_by_category.get(category_id)
        if not es_ids:
            continue
        items = sync_subcategory_metadata_with_app_config(
            category=category_id,
            es_subcategory_ids=es_ids,
            categories_payload=categories,
        )
        if not items:
            continue
        category_meta = category_metadata_by_id.get(category_id)
        if not category_meta:
            continue
        out.append(
            {
                "category": {
                    "id": str(category_meta.get("id") or "").strip(),
                    "image": str(category_meta.get("image") or "").strip(),
                    "name": str(category_meta.get("name") or "").strip(),
                },
                "items": items,
            }
        )
    return out
