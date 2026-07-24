#!/usr/bin/env python3
"""
v1_v2_parity_diff.py — deep, field-level structural diff between a V1 and a
V2 JSON response for the same request. Unlike postman_regression_runner.py
(pass/fail + product-id-set diffs), this reports:

  - fields present in V1 but missing in V2 (potential regression)
  - fields present in V2 but not V1 (flagged for manual classification —
    expected improvement vs accidental)
  - fields present in both with a different JSON type
  - fields that are null in one and absent/present-with-value in the other
  - for list-of-dict arrays (e.g. products), per-item key-set differences
    summarized once (not per-item, to stay readable) plus id ordering

This is a read-only reporting tool — it does not fix anything, only reports,
since classifying "expected improvement" vs "potential regression" requires
human/product judgement this script deliberately does not attempt.

Usage:
  python v1_v2_parity_diff.py --v1 v1_response.json --v2 v2_response.json --name "Basic Search"
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Set, Tuple


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return type(v).__name__


def _walk_paths(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """Flatten a JSON tree into {path: value}, treating lists of dicts
    specially (indexed by position isn't meaningful across engines with
    different result sets, so list items are summarized separately)."""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.update(_walk_paths(v, path))
            else:
                out[path] = v
    elif isinstance(obj, list):
        out[prefix] = obj  # keep list itself for separate list-aware handling
    return out


def _find_lists_of_dicts(obj: Any, prefix: str = "") -> Dict[str, List[Dict[str, Any]]]:
    found: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                found[path] = v
            elif isinstance(v, (dict, list)):
                found.update(_find_lists_of_dicts(v, path))
    return found


def diff_responses(v1: Dict[str, Any], v2: Dict[str, Any], name: str) -> Dict[str, Any]:
    report: Dict[str, Any] = {"name": name}

    # 1. Scalar field diffs (excluding list contents, handled separately)
    def scalar_only(obj):
        return {k: v for k, v in _walk_paths(obj).items() if not isinstance(v, list)}

    s1, s2 = scalar_only(v1), scalar_only(v2)
    missing_in_v2 = sorted(set(s1) - set(s2))
    new_in_v2 = sorted(set(s2) - set(s1))
    type_changes = []
    null_semantics = []
    value_changes = []
    for k in sorted(set(s1) & set(s2)):
        a, b = s1[k], s2[k]
        ta, tb = _type_name(a), _type_name(b)
        if ta != tb:
            if (a is None) != (b is None):
                null_semantics.append({"field": k, "v1": a, "v2": b})
            else:
                type_changes.append({"field": k, "v1_type": ta, "v2_type": tb, "v1": a, "v2": b})
        elif a != b:
            value_changes.append({"field": k, "v1": a, "v2": b})

    report["scalar_fields_missing_in_v2"] = missing_in_v2
    report["scalar_fields_new_in_v2"] = new_in_v2
    report["scalar_type_changes"] = type_changes
    report["scalar_null_semantics_differences"] = null_semantics
    report["scalar_value_changes"] = value_changes

    # 2. List-of-dict fields (e.g. products, filters, collections)
    lists1, lists2 = _find_lists_of_dicts(v1), _find_lists_of_dicts(v2)
    list_report = {}
    for path in sorted(set(lists1) | set(lists2)):
        l1, l2 = lists1.get(path, []), lists2.get(path, [])
        keys1 = set().union(*[set(d.keys()) for d in l1]) if l1 else set()
        keys2 = set().union(*[set(d.keys()) for d in l2]) if l2 else set()
        entry = {
            "v1_count": len(l1),
            "v2_count": len(l2),
            "keys_missing_in_v2_items": sorted(keys1 - keys2),
            "keys_new_in_v2_items": sorted(keys2 - keys1),
        }
        # id-based ordering comparison, if items have an "id" field
        ids1 = [d.get("id") for d in l1 if "id" in d]
        ids2 = [d.get("id") for d in l2 if "id" in d]
        if ids1 or ids2:
            set1, set2 = set(ids1), set(ids2)
            entry["missing_ids_in_v2"] = [i for i in ids1 if i not in set2]
            entry["extra_ids_in_v2"] = [i for i in ids2 if i not in set1]
            common = [i for i in ids1 if i in set2]
            if common:
                rank1 = {i: idx for idx, i in enumerate(ids1)}
                rank2 = {i: idx for idx, i in enumerate(ids2)}
                reordered = sum(1 for i in common if rank1[i] != rank2[i])
                entry["ranking_difference"] = f"{reordered}/{len(common)} common items reordered"
        list_report[path] = entry
    report["list_fields"] = list_report

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1", required=True)
    parser.add_argument("--v2", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()

    with open(args.v1) as f:
        v1 = json.load(f)
    with open(args.v2) as f:
        v2 = json.load(f)

    report = diff_responses(v1, v2, args.name)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
