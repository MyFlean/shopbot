#!/usr/bin/env python3
"""
postman_regression_runner.py — executes every request in a Postman v2.1
collection against a running ShopBot instance and reports pass/fail, status
code, latency, and a response summary for each. Optionally diffs two
previously-saved result files against each other (e.g. a V1-only run vs a
V2-only run) to report response differences.

Why two separate runs for V1-vs-V2, not one: SEARCH_ENGINE is read from the
SERVER process's environment, not from anything a client can override
per-request — so a true V1-vs-V2 comparison means starting the server twice
(once per SEARCH_ENGINE value) and diffing two saved result files, not
something a single invocation of this script can do against one live server.

Usage:
  # Run once against whatever server is currently up (reports pass/fail,
  # status, latency for every request):
  python postman_regression_runner.py --collection Flean_HomePage_Search_APIs.postman_collection.json \
      --base-url http://localhost:8080 --out results_auto.json

  # Then, to get a V1-vs-V2 comparison: start the server once with
  # SEARCH_ENGINE=v1 and run with --out results_v1.json, then again with
  # SEARCH_ENGINE=v2 and --out results_v2.json, then:
  python postman_regression_runner.py --diff results_v1.json results_v2.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def _resolve_vars(text: str, variables: Dict[str, str]) -> str:
    def _sub(m: re.Match) -> str:
        return variables.get(m.group(1), m.group(0))
    return _VAR_RE.sub(_sub, text)


def _flatten_items(items: List[Dict[str, Any]], prefix: str = "") -> List[Dict[str, Any]]:
    """Postman collections nest requests inside folders arbitrarily deep."""
    out = []
    for it in items:
        name = prefix + it.get("name", "?")
        if "item" in it:
            out.extend(_flatten_items(it["item"], name + " / "))
        elif "request" in it:
            out.append({"name": name, "request": it["request"]})
    return out


def _extract_product_ids(payload: Any) -> List[str]:
    """Best-effort extraction of product ids from a response body, for the
    diff mode's "missing/extra products" reporting — walks data.products[],
    data.collections[].products[], etc."""
    ids: List[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if "id" in node and isinstance(node.get("id"), str) and len(node) > 1:
                # Heuristic: looks like a product-card-shaped dict.
                if any(k in node for k in ("price", "name", "flean_score")):
                    ids.append(node["id"])
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(payload)
    return ids


def run_collection(collection_path: str, base_url: str, timeout: float = 20.0, label: Optional[str] = None) -> Dict[str, Any]:
    with open(collection_path) as f:
        collection = json.load(f)

    variables: Dict[str, str] = {v["key"]: v["value"] for v in collection.get("variable", [])}
    variables["base_url"] = base_url  # CLI override always wins

    requests_flat = _flatten_items(collection.get("item", []))
    results: List[Dict[str, Any]] = []

    print(f"Running {len(requests_flat)} requests against {base_url} ...\n")

    for entry in requests_flat:
        name = entry["name"]
        req = entry["request"]
        method = req.get("method", "GET")
        url_obj = req.get("url", {})
        raw_url = url_obj.get("raw") if isinstance(url_obj, dict) else url_obj
        url = _resolve_vars(raw_url, variables)

        headers = {h["key"]: _resolve_vars(h["value"], variables) for h in req.get("header", []) if not h.get("disabled")}
        body = req.get("body")
        json_body = None
        if body and body.get("mode") == "raw" and body.get("raw", "").strip():
            raw_body = _resolve_vars(body["raw"], variables)
            try:
                json_body = json.loads(raw_body)
            except json.JSONDecodeError:
                json_body = None

        record: Dict[str, Any] = {"name": name, "method": method, "url": url}
        t0 = time.monotonic()
        try:
            resp = requests.request(method, url, headers=headers, json=json_body, timeout=timeout)
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            record["status_code"] = resp.status_code
            record["latency_ms"] = latency_ms
            record["passed"] = 200 <= resp.status_code < 300
            try:
                payload = resp.json()
                record["response_summary"] = _summarize(payload)
                record["product_ids"] = _extract_product_ids(payload)
            except ValueError:
                record["response_summary"] = {"non_json_body_len": len(resp.text)}
                record["product_ids"] = []
        except requests.RequestException as exc:
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            record["status_code"] = None
            record["latency_ms"] = latency_ms
            record["passed"] = False
            record["error"] = str(exc)

        status = "PASS" if record["passed"] else "FAIL"
        print(f"  [{status}] {method:5} {name:45} {record.get('status_code','ERR'):>5}  {record['latency_ms']:>7.1f}ms")
        results.append(record)

    passed = sum(1 for r in results if r["passed"])
    summary = {
        "label": label or base_url,
        "base_url": base_url,
        "collection": collection_path,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    print(f"\n{passed}/{len(results)} passed.")
    return summary


def _summarize(payload: Any) -> Dict[str, Any]:
    """Small, diff-friendly summary: top-level shape + any 'total'/'engine'
    meta fields, without keeping the full (often large) product list."""
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    meta = payload.get("meta") or {}
    data = payload.get("data") or {}
    summary: Dict[str, Any] = {
        "success": payload.get("success"),
        "meta_total": meta.get("total") if isinstance(meta, dict) else None,
        "meta_engine": meta.get("engine") if isinstance(meta, dict) else None,
        "meta_took_ms": meta.get("took_ms") if isinstance(meta, dict) else None,
    }
    if isinstance(data, dict):
        products = data.get("products")
        if isinstance(products, list):
            summary["returned_count"] = len(products)
        collections = data.get("collections")
        if isinstance(collections, list):
            summary["collection_count"] = len(collections)
            summary["products_per_collection"] = [len(c.get("products", [])) for c in collections]
    return summary


def diff_results(path_a: str, path_b: str) -> Dict[str, Any]:
    with open(path_a) as f:
        a = json.load(f)
    with open(path_b) as f:
        b = json.load(f)

    by_name_a = {r["name"]: r for r in a["results"]}
    by_name_b = {r["name"]: r for r in b["results"]}

    # Fields that are expected to vary run-to-run regardless of behavior
    # (wall-clock timing) — reported separately in "latency_comparison",
    # never used to decide whether a request "has a difference".
    _NOISY_META_KEYS = {"meta_took_ms"}

    diffs: List[Dict[str, Any]] = []
    latency_comparison: Dict[str, Any] = {}
    for name in sorted(set(by_name_a) | set(by_name_b)):
        ra, rb = by_name_a.get(name), by_name_b.get(name)
        if ra is None or rb is None:
            diffs.append({"name": name, "note": "present in only one run"})
            continue

        latency_comparison[name] = {a["label"]: ra["latency_ms"], b["label"]: rb["latency_ms"]}

        entry: Dict[str, Any] = {"name": name}
        if ra["passed"] != rb["passed"]:
            entry["pass_fail_diff"] = f"{a['label']}={ra['passed']} vs {b['label']}={rb['passed']}"
        if ra.get("status_code") != rb.get("status_code"):
            entry["status_code_diff"] = f"{ra.get('status_code')} vs {rb.get('status_code')}"

        ids_a, ids_b = set(ra.get("product_ids", [])), set(rb.get("product_ids", []))
        if ids_a or ids_b:
            missing = ids_a - ids_b
            extra = ids_b - ids_a
            if missing:
                entry["missing_in_b"] = sorted(missing)
            if extra:
                entry["extra_in_b"] = sorted(extra)
            if ids_a and ids_b:
                common = [i for i in ra.get("product_ids", []) if i in ids_b]
                rank_a = {i: idx for idx, i in enumerate(ra.get("product_ids", []))}
                rank_b = {i: idx for idx, i in enumerate(rb.get("product_ids", []))}
                reordered = [i for i in common if rank_a[i] != rank_b[i]]
                if reordered:
                    entry["ranking_differences"] = f"{len(reordered)}/{len(common)} common products reordered"

        sa, sb = ra.get("response_summary", {}), rb.get("response_summary", {})
        for key in set(sa) | set(sb):
            if key in _NOISY_META_KEYS:
                continue
            if sa.get(key) != sb.get(key):
                entry.setdefault("meta_diffs", {})[key] = {a["label"]: sa.get(key), b["label"]: sb.get(key)}

        if len(entry) > 1:
            diffs.append(entry)

    return {
        "a": {"label": a["label"], "base_url": a["base_url"], "passed": a["passed"], "total": a["total"]},
        "b": {"label": b["label"], "base_url": b["base_url"], "passed": b["passed"], "total": b["total"]},
        "requests_with_differences": diffs,
        "requests_identical": sorted(set(by_name_a) & set(by_name_b) - {d["name"] for d in diffs}),
        "latency_comparison": latency_comparison,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", help="Path to the Postman collection JSON")
    parser.add_argument("--base-url", help="Base URL to run the collection against")
    parser.add_argument("--out", default="postman_regression_results.json", help="Where to save run results")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--label", help="Tag for this run (e.g. 'v1', 'v2', 'auto') — required to diff two runs against the same base-url")
    parser.add_argument("--diff", nargs=2, metavar=("RESULTS_A", "RESULTS_B"),
                         help="Instead of running, diff two previously-saved result files")
    args = parser.parse_args()

    if args.diff:
        report = diff_results(*args.diff)
        print(json.dumps(report, indent=2))
        out_path = Path("postman_regression_diff.json")
        out_path.write_text(json.dumps(report, indent=2))
        print(f"\nDiff written to {out_path}")
        return

    if not args.collection or not args.base_url:
        parser.error("--collection and --base-url are required unless using --diff")

    summary = run_collection(args.collection, args.base_url, args.timeout, label=args.label)
    Path(args.out).write_text(json.dumps(summary, indent=2))
    print(f"\nResults written to {args.out}")

    if summary["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
