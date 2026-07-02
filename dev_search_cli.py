#!/usr/bin/env python3
"""
dev_search_cli.py — Search V2 developer CLI for ShopBot.

Initializes the application through the same path as production:
  load_dotenv() → create_app() → SearchGateway singleton → warmup()

The Flask HTTP server is never started. Every search query exercises the
full production retrieval path: SearchGateway → process_search_request →
hybrid search (BM25 + kNN) → RRF fusion → business ranking → result mapping.

Usage (from shopbot/ directory):
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

# ── Step 1: load .env before any local import (mirrors run.py:25) ─────────────
from dotenv import load_dotenv
load_dotenv()

# ── Step 2: ensure shopbot root is on sys.path ────────────────────────────────
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── Step 3: initialize exactly as production (Redis → Gateway → warmup) ───────
# create_app() is the same factory run.py calls. It does not start a server —
# Flask only binds a port when app.run() / gunicorn is invoked. By stopping
# here we get identical startup (Redis health-check, SearchGateway singleton,
# synchronous embedding warmup) without an HTTP listener.
print("Initializing ShopBot (Redis → SearchGateway → Search V2)...", flush=True)

import io, contextlib

_init_buf = io.StringIO()
try:
    # Redirect stdout to suppress es_products.py's module-level debug prints.
    # Errors still propagate as exceptions — startup failures are not silenced.
    with contextlib.redirect_stdout(_init_buf):
        from shopping_bot import create_app
        _app = create_app()
except Exception as exc:
    # Re-raise so the CLI fails exactly as production would.
    print(f"\nStartup failed: {exc}", file=sys.stderr)
    sys.exit(1)

# ── Step 4: obtain the singleton that production routes also use ───────────────
from shopping_bot.data_fetchers.es_products import get_search_gateway
from search_v2.config.settings import SETTINGS
from search_v2.embedding.embedding_service import get_embedding_service

_gateway = get_search_gateway()

# Pre-load embedding model weights now so every REPL query is fast.
# create_app() → warmup() builds the EmbeddingService object but does not
# call _get_model() — weights load lazily on first embed_query(). We force
# that here so the 7–8 s model load happens before the prompt appears.
print("Pre-loading embedding model weights...", end=" ", flush=True)
_emb_svc = get_embedding_service(SETTINGS.EMBEDDING_MODEL_KEY)
_emb_svc.preload()
print("done.")

# ── Step 5: verify OpenSearch connectivity ────────────────────────────────────
def _os_status() -> str:
    try:
        from search_v2.retrieval.opensearch_client import OpenSearchClient
        info = OpenSearchClient(settings=SETTINGS)._get_client().info()
        return f"Connected  v{info['version']['number']}"
    except Exception as exc:
        return f"ERROR — {exc}"

_os_info = _os_status()

# ── Banner ────────────────────────────────────────────────────────────────────
BANNER = f"""
==================================================
ShopBot Search V2 CLI
Engine:     {os.getenv('SEARCH_ENGINE', 'auto')}
Index:      {SETTINGS.INDEX_NAME}
OpenSearch: {_os_info}
Model:      {SETTINGS.EMBEDDING_MODEL_KEY}
==================================================
Inline filters: brand:X  cat:X  min_price:N  max_price:N  size:N
Type 'quit' or 'exit' to stop.
"""
print(BANNER)


# ── Filter parser ─────────────────────────────────────────────────────────────

_FILTER_RE = re.compile(r'\b(brand|cat|min_price|max_price|size):(\S+)')

def _parse(raw: str) -> tuple[str, dict]:
    """Split key:value tokens out of the raw input; return (query, extra_params)."""
    params: dict = {}
    def _absorb(m: re.Match) -> str:
        k, v = m.group(1), m.group(2)
        if k in ("min_price", "max_price"):
            try:
                params[k] = float(v)
            except ValueError:
                return m.group(0)          # keep as query text if not numeric
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


# ── Result printer ────────────────────────────────────────────────────────────

def _print(result: dict, raw_q: str) -> None:
    meta     = result.get("meta", {})
    products = result.get("products", [])

    filters_applied = {
        k: v for k, v in result.get("applied_filters", {}).items()
        if v not in (None, [], {}, "")
    } if result.get("applied_filters") else {}

    print(f"\n── {raw_q!r} ──────────────────────────────")
    print(f"  engine={meta.get('engine')}  "
          f"returned={meta.get('returned')}  "
          f"total_hits={meta.get('total_hits')}  "
          f"took={meta.get('took_ms')} ms")
    if filters_applied:
        print(f"  filters: {filters_applied}")

    if not products:
        print("  (no results)\n")
        return

    print()
    for i, p in enumerate(products[:10], 1):
        name   = (p.get("name") or "—")[:72]
        brand  = p.get("brand") or "—"
        price  = p.get("price")
        score  = p.get("score")
        pid    = p.get("id") or "—"
        cats   = p.get("category") or "—"
        rating = p.get("avg_rating")
        print(f"  {i:>2}. {name}")
        print(f"      id={pid}  brand={brand}  price=₹{price}  "
              f"score={score}  rating={rating}  category={cats}")
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

    params = {"q": query, "size": extra.pop("size", 10), **extra}

    try:
        result = _gateway.search(params)
        _print(result, raw)
    except Exception as exc:
        import traceback
        print(f"\n  ERROR: {exc}")
        traceback.print_exc()
