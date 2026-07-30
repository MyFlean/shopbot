"""
Search V2 category hierarchy utilities.

This module provides helpers for category-scoped flows that rely only on
`category_hierarchies.segments`:
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
    out: List[str] = []
    parsed = category_scope.get(_SUBCATEGORY_BUCKETS_NAME) or {}
    if not isinstance(parsed, dict):
        return []
    # Support both terms-buckets and scripted_metric return shapes.
    segment3_values = parsed.get("segment3_values")
    raw_values = (segment3_values or {}).get("value") if isinstance(segment3_values, dict) else parsed.get("value")
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
