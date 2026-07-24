#!/usr/bin/env python3
"""
dev_category_browsing_cli.py — Category Browsing EXPLORER for ShopBot.

An interactive taxonomy browser: start at the root categories, drill down
level by level, and land on products once a leaf category is reached — no
need to know or guess an ES category path up front. Exercises the same
V2-native path production uses (search_v2.extension.category_browsing.browse)
and, on request, the V1 path (ElasticsearchProductsFetcher) for side-by-side
comparison at any level.

The full category tree (currently 97 distinct paths locally) is small enough
to fetch once via a single terms aggregation on `category_paths` and hold in
memory — every node's doc_count comes directly from that aggregation, since
category_paths already stores every ancestor breadcrumb as a separate array
entry per document (see category_browsing/browse.py's module docstring).

Usage (from shopbot-main/ directory):
  python dev_category_browsing_cli.py
"""
from __future__ import annotations

import io
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

print("Initializing ShopBot...", flush=True)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        from shopping_bot import create_app
        _app = create_app(config_name="lambda")
except Exception as exc:
    print(f"\nStartup failed: {exc}", file=sys.stderr)
    sys.exit(1)

from shopping_bot.data_fetchers.es_products import get_es_fetcher, transform_to_product_card  # noqa: E402
from shopping_bot.routes.product_api import _normalize_filter_aliases  # noqa: E402
from search_v2.config.settings import SETTINGS  # noqa: E402
from search_v2.retrieval.opensearch_client import OpenSearchClient  # noqa: E402
from search_v2.retrieval.filters import SearchFilters  # noqa: E402
from search_v2.extension.category_browsing import browse  # noqa: E402

print("Initializing V1 fetcher...", end=" ", flush=True)
with contextlib.redirect_stdout(io.StringIO()):
    _v1_fetcher = get_es_fetcher()
print("done.")

_client = OpenSearchClient(settings=SETTINGS)

# ── Display-name overrides from the app's own taxonomy config ────────────────
_DISPLAY_NAMES: Dict[str, str] = {}
try:
    _mapping = json.load(open(_HERE / "shopping_bot" / "data" / "home" / "category_mapping.json"))
    for cat in _mapping.get("categories", []):
        if cat.get("es_path"):
            _DISPLAY_NAMES[cat["es_path"]] = cat.get("display_name", "")
        for sub in cat.get("subcategories", []):
            if sub.get("es_path"):
                _DISPLAY_NAMES[sub["es_path"]] = sub.get("display_name", "")
except Exception:
    pass


def _pretty(path: str) -> str:
    leaf = path.rsplit("/", 1)[-1]
    return _DISPLAY_NAMES.get(path) or leaf.replace("_", " ").title()


# ── Category tree ─────────────────────────────────────────────────────────────

@dataclass
class Node:
    path: str
    name: str
    doc_count: int
    children: Dict[str, "Node"] = field(default_factory=dict)


def _build_tree() -> Dict[str, Node]:
    """One terms aggregation over the whole (small) category_paths cardinality,
    then assembled into a tree in memory. `roots` keyed by top-level path."""
    resp = _client.search({
        "size": 0,
        "aggs": {"paths": {"terms": {"field": "category_paths", "size": 500}}},
    })
    buckets = (resp.get("aggregations") or {}).get("paths", {}).get("buckets", [])
    nodes: Dict[str, Node] = {
        b["key"]: Node(path=b["key"], name=_pretty(b["key"]), doc_count=b["doc_count"])
        for b in buckets
    }
    roots: Dict[str, Node] = {}
    for path, node in nodes.items():
        if "/" not in path:
            roots[path] = node
        else:
            parent_path = path.rsplit("/", 1)[0]
            parent = nodes.get(parent_path)
            if parent is not None:
                parent.children[path] = node
    return roots


def _count_all(nodes: Dict[str, Node]) -> int:
    total = len(nodes)
    for n in nodes.values():
        total += _count_all(n.children)
    return total


print("Fetching category taxonomy...", end=" ", flush=True)
_ROOTS = _build_tree()
print(f"done ({_count_all(_ROOTS)} nodes).")


def _os_status() -> str:
    try:
        info = _client._get_client().info()
        return f"Connected  v{info['version']['number']}"
    except Exception as exc:
        return f"ERROR — {exc}"


print(f"""
==================================================
ShopBot Category Browsing Explorer
V1 Index:    {os.getenv("ELASTIC_INDEX", "products_master")}
V2 Index:    {SETTINGS.INDEX_NAME}
OpenSearch:  {_os_status()}
Root categories: {len(_ROOTS)}
==================================================
Navigate with numbers. Commands available at every level:
  p        view products at this level (even if it has children)
  f        set/clear filters (price_range, flean_score, dietary, food_type)
  s        set sort (relevance/price_asc/price_desc/flean_score_desc/...)
  c        toggle V1 vs V2 comparison mode
  b        back a level
  q        quit
""")

# ── Session state ─────────────────────────────────────────────────────────────

_filters: Dict[str, Any] = {}
_sort_by = "flean_score_desc"
_compare_mode = False
_page = 0
_PAGE_SIZE = 10

_VALID_SORTS = {
    "relevance", "price_asc", "price_desc", "protein_desc", "fiber_desc",
    "fat_asc", "flean_score_desc",
}


def _set_filters() -> None:
    global _filters
    print("  Leave blank to keep current value; type 'clear' to remove a filter.")
    new: Dict[str, Any] = dict(_filters)

    pr = input(f"  price_range [{new.get('price_range', '')}] (e.g. below_99, 100_249, 250_499, above_500): ").strip()
    if pr.lower() == "clear":
        new.pop("price_range", None)
    elif pr:
        new["price_range"] = pr

    fs = input(f"  flean_score [{new.get('flean_score', '')}] (e.g. 9_plus, 8_plus, 7_plus, 10): ").strip()
    if fs.lower() == "clear":
        new.pop("flean_score", None)
    elif fs:
        new["flean_score"] = fs

    dt = input(f"  dietary (comma-separated) [{','.join(new.get('dietary', []))}]: ").strip()
    if dt.lower() == "clear":
        new.pop("dietary", None)
    elif dt:
        new["dietary"] = [d.strip() for d in dt.split(",") if d.strip()]

    ft = input(f"  food_type [{new.get('food_type', '')}] (veg/nonveg): ").strip()
    if ft.lower() == "clear":
        new.pop("food_type", None)
    elif ft:
        new["food_type"] = ft

    _filters = new
    print(f"  Filters now: {_filters or '(none)'}")


def _set_sort() -> None:
    global _sort_by
    raw = input(f"  sort_by [{_sort_by}] ({', '.join(sorted(_VALID_SORTS))}): ").strip()
    if raw:
        if raw not in _VALID_SORTS:
            print(f"  Unknown sort '{raw}', keeping '{_sort_by}'.")
        else:
            _sort_by = raw


def _v1_filters_for_node() -> Optional[Dict[str, Any]]:
    return _normalize_filter_aliases(_filters) if _filters else None


def _v2_filters_for_node() -> Optional[SearchFilters]:
    if not _filters:
        return None
    d: Dict[str, Any] = {}
    pr = _filters.get("price_range")
    if pr:
        bounds = {
            "below_99": (None, 99.0), "100_249": (100.0, 249.0),
            "250_499": (250.0, 499.0), "above_500": (500.0, None),
        }.get(pr)
        if bounds is None and "_" in str(pr):
            l, _, r = str(pr).partition("_")
            if l.isdigit() and r.isdigit():
                bounds = (float(l), float(r))
        if bounds:
            if bounds[0] is not None:
                d["price_min"] = bounds[0]
            if bounds[1] is not None:
                d["price_max"] = bounds[1]
    fs = _filters.get("flean_score")
    if fs:
        min_badge = {"10": 10.0, "9_plus": 9.0, "8_plus": 8.0, "7_plus": 7.0}.get(str(fs))
        if min_badge is not None:
            d["min_flean_score"] = min_badge
    if _filters.get("dietary"):
        d["dietary_labels"] = _filters["dietary"]
    if _filters.get("food_type"):
        d["food_type"] = _filters["food_type"]
    return SearchFilters.from_dict(d)


def _show_products(path: str, label: str) -> None:
    global _page
    while True:
        t0 = time.monotonic()
        v2_result = browse(path, page=_page, size=_PAGE_SIZE, sort_by=_sort_by, filters=_v2_filters_for_node())
        v2_ms = (time.monotonic() - t0) * 1000
        v2_products = v2_result.get("products", [])
        v2_total = v2_result.get("meta", {}).get("total", 0)

        print(f"\n--- {label} — page {_page + 1} ---")
        print(f"[V2] total={v2_total}  returned={len(v2_products)}  took={v2_ms:.1f}ms  filters={_filters or '(none)'}  sort={_sort_by}")
        for i, p in enumerate(v2_products, 1):
            print(f"  {i:>2}. {(p.get('name') or '—')[:55]:<55} id={p.get('id')}  ₹{p.get('price')}  flean={p.get('flean_score')}")

        if _compare_mode:
            t0 = time.monotonic()
            v1_result = _v1_fetcher.search_products_unified(
                subcategory=path, page=_page, size=_PAGE_SIZE, sort_by=_sort_by,
                filters=_v1_filters_for_node(),
            )
            v1_ms = (time.monotonic() - t0) * 1000
            v1_raw = v1_result.get("products", [])
            v1_cards = [c for c in (transform_to_product_card(p) for p in v1_raw) if c]
            v1_total = v1_result.get("meta", {}).get("total", len(v1_cards))
            print(f"[V1] total={v1_total}  returned={len(v1_cards)}  took={v1_ms:.1f}ms")
            for i, p in enumerate(v1_cards, 1):
                print(f"  {i:>2}. {(p.get('name') or '—')[:55]:<55} id={p.get('id')}  ₹{p.get('price')}  flean={p.get('flean_score')}")

            v2_ids, v1_ids = [p.get("id") for p in v2_products], [p.get("id") for p in v1_cards]
            v2_set, v1_set = set(v2_ids), set(v1_ids)
            print(f"  Comparison — count: V1={len(v1_ids)} V2={len(v2_ids)}")
            missing = v1_set - v2_set
            extra = v2_set - v1_set
            if missing:
                print(f"  Missing from V2: {sorted(missing)}")
            if extra:
                print(f"  Extra in V2: {sorted(extra)}")
            if not missing and not extra:
                print("  Same product set.")
            common = [pid for pid in v2_ids if pid in v1_set]
            if common:
                v1_rank = {pid: i for i, pid in enumerate(v1_ids)}
                v2_rank = {pid: i for i, pid in enumerate(v2_ids)}
                reordered = [pid for pid in common if v1_rank[pid] != v2_rank[pid]]
                print(f"  Ranking differences: {len(reordered)}/{len(common)} common products reordered")

        cmd = input("\n  [n]ext page  [p]rev page  [f]ilters  [s]ort  [c]ompare  [b]ack  [q]uit > ").strip().lower()
        if cmd == "n":
            if (_page + 1) * _PAGE_SIZE < v2_total:
                _page += 1
        elif cmd == "p":
            _page = max(0, _page - 1)
        elif cmd == "f":
            _set_filters()
            _page = 0
        elif cmd == "s":
            _set_sort()
            _page = 0
        elif cmd == "c":
            globals()["_compare_mode"] = not _compare_mode
            print(f"  Compare mode: {'ON' if _compare_mode else 'OFF'}")
        elif cmd == "b":
            _page = 0
            return
        elif cmd == "q":
            print("Bye.")
            sys.exit(0)


def _explore() -> None:
    global _page
    stack: List[Dict[str, Node]] = [_ROOTS]
    path_stack: List[str] = []

    while True:
        level = stack[-1]
        ordered = sorted(level.values(), key=lambda n: n.path)
        breadcrumb = " > ".join(path_stack) or "(root)"
        print(f"\n=== {breadcrumb} ===")
        print(f"  Filters: {_filters or '(none)'}   Sort: {_sort_by}   Compare: {'ON' if _compare_mode else 'OFF'}")
        for i, node in enumerate(ordered, 1):
            marker = " (leaf)" if not node.children else f" ({len(node.children)} subcategories)"
            print(f"  {i:>2}. {node.name:<30} — {node.doc_count} products{marker}")

        cmd = input("\n  [number] enter  [p]roducts here  [f]ilters  [s]ort  [c]ompare  [b]ack  [q]uit > ").strip().lower()

        if cmd == "q":
            print("Bye.")
            return
        if cmd == "b":
            if len(stack) > 1:
                stack.pop()
                path_stack.pop()
            else:
                print("  Already at root.")
            continue
        if cmd == "f":
            _set_filters()
            continue
        if cmd == "s":
            _set_sort()
            continue
        if cmd == "c":
            globals()["_compare_mode"] = not _compare_mode
            print(f"  Compare mode: {'ON' if _compare_mode else 'OFF'}")
            continue
        if cmd == "p":
            current_path = path_stack[-1] if path_stack else None
            if current_path is None:
                print("  Select a category first.")
                continue
            _page = 0
            _show_products(current_path, breadcrumb)
            continue
        if cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(ordered):
                node = ordered[idx]
                if node.children:
                    stack.append(node.children)
                    path_stack.append(node.path)
                else:
                    _page = 0
                    _show_products(node.path, " > ".join(path_stack + [node.path]))
            else:
                print("  Invalid selection.")
            continue
        print("  Unrecognized command.")


if __name__ == "__main__":
    try:
        _explore()
    except (EOFError, KeyboardInterrupt):
        print("\nBye.")
