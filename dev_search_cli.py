#!/usr/bin/env python3
"""
dev_search_cli.py — Search developer CLI for ShopBot.

Exercises the full production retrieval path depending on SEARCH_ENGINE:

  v1   — V1 ElasticsearchProductsFetcher only. Search V2 pipeline not warmed up.
  v2   — search_v2.extension.search only. No V1 fallback.
  auto — V2 first; V1 fallback on genuine exceptions. Zero-result V2 responses
         do NOT trigger fallback. Identical to the production /rs/v1/search
         endpoint behaviour.

Routing is driven by the same _search_engine() helper used by /rs/v1/search
so CLI and HTTP endpoint always behave identically.

Usage (from shopbot-main/ directory):
  python dev_search_cli.py

Inline filter syntax:
  protein bars brand:getmymettle size:5
  gluten free chips min_price:50 max_price:200
  cat:snacks apple
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# ── Step 1: load .env before any local import (mirrors run.py) ───────────────
from dotenv import load_dotenv
load_dotenv()

# ── Step 1b: fail immediately if any critical var still holds a placeholder ───
_REQUIRED = {
    "ES_URL":             "V1 OpenSearch endpoint (also V2 fallback when SEARCH_V2_ES_URL is unset)",
    "SEARCH_V2_ES_URL":   "V2 OpenSearch endpoint",
    "SEARCH_V2_INDEX_NAME": "V2 index name (default: products-search-v2)",
}
_bad = {
    k: os.getenv(k, "")
    for k in _REQUIRED
    if os.getenv(k, "").lstrip().startswith("<")
}
if _bad:
    print("\nERROR: The following environment variables still contain placeholder values.", file=sys.stderr)
    print("       Replace them in shopbot-main/.env before running the CLI.\n", file=sys.stderr)
    for k, v in _bad.items():
        print(f"  {k}={v}", file=sys.stderr)
        print(f"    Purpose: {_REQUIRED[k]}\n", file=sys.stderr)
    print("Real values for local testing (localhost:9200):", file=sys.stderr)
    print("  Source: shopbot/.env  or  search/docker/.env.example", file=sys.stderr)
    sys.exit(1)

# ── Step 2: ensure shopbot-main root is on sys.path ──────────────────────────
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── Step 3: initialize Flask app (lambda mode defers Redis) ──────────────────
# 'lambda' config skips Redis eager-init so the CLI works without a running
# Redis instance. Search V2 pipeline and V1 fetcher are initialized below.
print("Initializing ShopBot...", flush=True)

import io, contextlib

_init_buf = io.StringIO()
try:
    with contextlib.redirect_stdout(_init_buf):
        from shopping_bot import create_app
        _app = create_app(config_name='lambda')
except Exception as exc:
    print(f"\nStartup failed: {exc}", file=sys.stderr)
    sys.exit(1)

# ── Step 4: import routing helper from the production HTTP endpoint ───────────
# _search_engine() reads SEARCH_ENGINE at call time — same as /rs/v1/search.
# Importing from the production module ensures CLI and HTTP are always in sync.
from shopping_bot.routes.unified_search import _search_engine  # noqa: E402

# Same product-card transform /rs/v1/search applies to every result before it
# reaches the client (see unified_search.py's unified_search()) — used below
# to display the real user-facing Flean score, not gateway.py's raw 0-100
# adjusted_score.
from shopping_bot.data_fetchers.es_products import transform_to_product_card  # noqa: E402

# ── Step 5: initialize engines according to SEARCH_ENGINE ────────────────────
_engine_setting = _search_engine()

_gateway = None
_v1_fetcher = None
_settings = None

if _engine_setting != "v1":
    # V2 or auto: warm up Search V2 pipeline + embedding model
    from search_v2.extension.search import search as _v2_search, warmup as _v2_warmup
    from search_v2.config.settings import SETTINGS as _settings
    from search_v2.embedding.embedding_service import get_embedding_service

    print("Warming up Search V2 pipeline...", end=" ", flush=True)
    _v2_warmup()
    print("done.")
    _gateway = True  # sentinel: Search V2 available (see _search() below)

    # No explicit model_key here, deliberately: get_embedding_service() picks
    # Bedrock Titan vs. the local sentence-transformers path based on
    # SETTINGS.EMBEDDING_BACKEND. Passing _settings.EMBEDDING_MODEL_KEY
    # explicitly (as this used to) always selects the local path regardless
    # of that setting — silently breaking this file's own stated contract
    # ("CLI and HTTP endpoint always behave identically"), since production
    # (search_v2/extension/search/core.py) calls this the same way.
    # Wrapped in try/except, matching the same tolerance
    # shopping_bot/__init__.py's ECS/non-Lambda init path already has for
    # this exact scenario: a missing AWS_BEARER_TOKEN_BEDROCK must not
    # prevent the CLI from starting — semantic search will fail per-query
    # (caught below in _search(), same "auto" fallback production uses) and
    # V1/lexical remain fully usable for local development without Bedrock
    # credentials configured yet.
    print("Pre-loading embedding model weights (no-op for Bedrock backend)...", end=" ", flush=True)
    try:
        _emb_svc = get_embedding_service()
        _emb_svc.preload()
        print("done.")
    except Exception as _pe:
        print(f"skipped ({_pe})")

if _engine_setting != "v2":
    # V1 or auto: initialize V1 ElasticsearchProductsFetcher
    # Suppress its banner (lots of emoji debug output on init).
    from shopping_bot.data_fetchers.es_products import get_es_fetcher
    print("Initializing V1 fetcher...", end=" ", flush=True)
    with contextlib.redirect_stdout(io.StringIO()):
        _v1_fetcher = get_es_fetcher()
    print("done.")


# ── Step 6: verify OpenSearch connectivity ────────────────────────────────────
def _os_status() -> str:
    try:
        from search_v2.retrieval.opensearch_client import OpenSearchClient
        info = OpenSearchClient(settings=_settings)._get_client().info()
        return f"Connected  v{info['version']['number']}"
    except Exception as exc:
        return f"ERROR — {exc}"


_os_info = _os_status() if _gateway else "N/A (v1 mode)"

# ── Banner ────────────────────────────────────────────────────────────────────
_v1_index = os.getenv("ELASTIC_INDEX", "products_master")
_v2_index = _settings.INDEX_NAME if _settings else "N/A"
_model_key = _settings.EMBEDDING_MODEL_KEY if _settings else "N/A"

BANNER = f"""
==================================================
ShopBot Search CLI
Engine:      {_engine_setting.upper()}
V1 Index:    {_v1_index}
V2 Index:    {_v2_index}
OpenSearch:  {_os_info}
Model:       {_model_key}
==================================================
Inline filters: brand:X  cat:X  min_price:N  max_price:N  size:N
Type 'quit' or 'exit' to stop.
"""
print(BANNER)


# ── Filter parser ─────────────────────────────────────────────────────────────

_FILTER_RE = re.compile(r'\b(brand|cat|min_price|max_price|size):(\S+)')


def _parse(raw: str) -> tuple[str, dict]:
    """Split key:value tokens out of raw input; return (query, extra_params)."""
    params: dict = {}

    def _absorb(m: re.Match) -> str:
        k, v = m.group(1), m.group(2)
        if k in ("min_price", "max_price"):
            try:
                params[k] = float(v)
            except ValueError:
                return m.group(0)
        elif k == "size":
            try:
                params["size"] = int(v)
            except ValueError:
                return m.group(0)
        elif k == "cat":
            params["subcategory"] = v
        else:
            params[k] = v
        return ""

    query = _FILTER_RE.sub(_absorb, raw).strip()
    return query, params


# ── Core search: mirrors production /rs/v1/search routing ────────────────────

def _search(query: str, size: int = 10) -> tuple[dict, str]:
    """
    Execute search using the same routing as /rs/v1/search.

    Returns (result, engine_label) where engine_label is one of:
      "V2"              — SEARCH_ENGINE=v2 succeeded
      "V1"              — SEARCH_ENGINE=v1 used
      "AUTO -> V2"      — SEARCH_ENGINE=auto, V2 succeeded
      "AUTO -> V1"      — SEARCH_ENGINE=auto, V2 raised, fell back to V1
    """
    result = None
    engine_label = "V1"

    # ── V2 path (mirrors: if _search_engine() != "v1" and query) ─────────────
    if _search_engine() != "v1" and query:
        try:
            gw_result = _v2_search({"q": query, "size": size})
            result = gw_result
            engine_label = "V2" if _search_engine() == "v2" else "AUTO -> V2"
        except Exception as exc:
            if _search_engine() == "v2":
                raise RuntimeError(f"Search V2 failed (no fallback in v2 mode): {exc}") from exc
            # auto: log and fall through to V1
            print(f"  [V2 exception — falling back to V1: {exc}]")
            engine_label = "AUTO -> V1"

    # ── V1 path (mirrors: if result is None) ──────────────────────────────────
    if result is None:
        v1_result = _v1_fetcher.search_products_unified(
            query=query, size=size, sort_by="relevance"
        )
        # Normalize V1 meta to match V2 shape expected by _print()
        v1_meta = v1_result.get("meta", {}) or {}
        v1_meta.setdefault("total_hits", v1_meta.get("total", 0))
        v1_meta.setdefault("returned", len(v1_result.get("products", [])))
        v1_meta["engine"] = "v1"
        result = {**v1_result, "meta": v1_meta}

    return result, engine_label


# ── Result printer ────────────────────────────────────────────────────────────

def _print(result: dict, raw_q: str, engine_label: str) -> None:
    meta = result.get("meta", {})
    products = result.get("products", [])

    print(f"\n── {raw_q!r} ──────────────────────────────")
    print(f"  Engine: {engine_label}")
    print(f"  returned={len(products)}  "
          f"total_hits={meta.get('total_hits', meta.get('total', '?'))}  "
          f"took={meta.get('took_ms')} ms")

    pi = meta.get("product_intent")
    if pi:
        print(f"  ProductIntent: {pi['primary_product']!r}  confidence={pi['confidence']}  tier={pi['tier']}")

    hi = meta.get("health_intent")
    if hi:
        goal_ids = hi.get("goal_diet_ids") or hi.get("goal_ids") or []
        phrases = hi.get("matched_phrases") or []
        print(f"  HealthIntake: goal_diet_ids={goal_ids}  matched_phrases={phrases}")

    routing = meta.get("routing")
    if routing:
        print(f"  RoutingContext: source={routing['product_intent_source']}  "
              f"confidence={routing['product_intent_confidence']}  is_compound={routing['is_compound']}  "
              f"goal_diet_detected={routing.get('goal_diet_detected', routing.get('health_intent_detected'))}")
        if routing.get("retrieval_mode"):
            print(f"  Retrieval mode: {routing['retrieval_mode']}")
        print(f"  Router decision: {routing['decision']}")

    if not products:
        print("  (no results)\n")
        return

    print()
    for i, p in enumerate(products[:10], 1):
        name  = (p.get("name") or "—")[:72]
        brand = p.get("brand") or "—"
        price = p.get("price")
        score = p.get("score")
        # The user-facing Flean score (X/10) — NOT p["flean_score"], which is
        # the raw 0-100 adjusted_score gateway.py attaches. The production
        # app never shows that raw number: /rs/v1/search (unified_search.py)
        # pipes every result through transform_to_product_card() before it
        # reaches the client, and THAT function is what actually derives the
        # displayed 0-10 score (prefers a stored badge label, else
        # adjusted_score/10, half-up rounded — see es_products.py). Calling
        # the real function here (not reimplementing its formula) guarantees
        # this always matches the app, including if that logic ever changes.
        card = transform_to_product_card(p)
        flean = card.get("flean_score") if card else None
        flean_display = f"{flean}/10" if flean is not None else "None"
        pid   = p.get("id") or "—"
        cats  = p.get("category") or "—"
        rating = p.get("avg_rating")
        print(f"  {i:>2}. {name}")
        print(f"      id={pid}  brand={brand}  price=₹{price}  "
              f"score={score}  flean_score={flean_display}  rating={rating}  category={cats}")
    print()


# ── REPL ──────────────────────────────────────────────────────────────────────

while True:
    try:
        raw = input("Search > ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nBye.")
        break

    if not raw:
        continue
    if raw.lower() in ("quit", "exit"):
        print("Bye.")
        break

    query, extra = _parse(raw)
    if not query:
        print("  (no query text after parsing filters)")
        continue

    size = extra.pop("size", 10)

    try:
        result, engine_label = _search(query, size=size)
        _print(result, raw, engine_label)
    except Exception as exc:
        import traceback
        print(f"\n  ERROR: {exc}")
        traceback.print_exc()
