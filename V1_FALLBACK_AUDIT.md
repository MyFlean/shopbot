# V1 Fallback Audit

Date: 2026-07-23. Complete inventory of every place Search Version 2 can still reach
Version 1 code at runtime, what triggers it, whether it was exercised during regression,
and what — if anything — was removed as a result.

## Method

1. Grepped every route file for `os.getenv("SEARCH_ENGINE", ...)` / `_search_engine()` — the only
   mechanism anywhere in the codebase that routes a request toward V1.
2. Ran the full pytest suite (192 tests) and a broad set of live local endpoint calls.
3. Ran the same live calls again with `SEARCH_ENGINE=v2` set **strictly** (no auto-fallback
   possible — V1 is not even queried) to get hard evidence of whether V2 alone handles real
   traffic cleanly. Result: **zero exceptions across all 19 endpoints tested** (see the "Evidence"
   column below).
4. A real, important caveat surfaced during this work and is recorded here rather than glossed
   over: this repo's own `.env` sets `SEARCH_ENGINE=v2` explicitly, and
   `search_v2/config/settings.py` lazily calls `load_dotenv(..., override=False)` the first time
   anything imports `search_v2.config.settings` — meaning **`SEARCH_ENGINE` silently becomes `v2`
   partway through any process that touches Search V2 at all**, not `auto`, even though `auto` is
   the coded default. An earlier pass of this audit removed a "V2 returned not-found → double-check
   V1" data-completeness check on PDP/Flean Score gated on `engine != "v2"`, which broke 8 unit
   tests once this was accounted for — restored (see PDP/Flean Score rows below). This is flagged
   explicitly since it affects how "SEARCH_ENGINE=auto" should be read anywhere else in this
   report or the codebase: locally, it is not really "auto" once `search_v2` has been imported once
   in the process, it is `v2`.

## Two categories of "fallback," classified differently

- **Exception-based auto-fallback**: `try: <V2 call> except Exception: <fall back to V1>`. This is
  what "regression proves unneeded" can actually be evaluated against — it either fires because V2
  raised, or it never fires. **Removed everywhere it was safe to remove** (see table).
- **Not-found / unresolvable-input fallback**: V2 runs cleanly, returns a definitive "no such
  product" / "can't resolve this category", and the code checks V1 too before giving up. This is
  a *data-completeness* safety net (a genuine indexing-lag gap between the two indices), not error
  handling — regression cannot "prove it unneeded" the same way, since it isn't about V2 failing.
  **Kept everywhere it existed**, and not removed.

## Table

| # | Capability | File | Code path | Type | Triggers when | Executed during regression? | Removed? | Why / blocker |
|---|---|---|---|---|---|---|---|---|
| 1 | Main search (query) | `unified_search.py` | `unified_search()`, query branch (~L505-550) | Exception-based | `SEARCH_ENGINE=auto`(local default `v2`) and V2 raises | No — 0/0 in strict-V2 testing across varied queries | **Kept** | Free-text query input space is not exhaustively testable; a genuine transient V2 failure (embedding backend hiccup) has real production value being caught here rather than 500ing the app's primary search box |
| 2 | Main search (filters-only) | `unified_search.py` | same function, filters-only branch | Exception-based | same | No | **Kept** | Same rationale as #1 — shares the branch |
| 3 | Category/subcategory browsing | `unified_search.py` | same function, subcategory branch (~L552-573) | Exception-based | V2 raises | No — 0/0 across every tested category path | **Removed** | Deterministic, fully-enumerable input (97 real category paths, confirmed via the taxonomy explorer). Now V2-only unless `SEARCH_ENGINE=v1` |
| 3b| — same — | `unified_search.py` | same function | Not-found/unresolvable | `_resolve_subcategory_es_path()` can't map a bare subcategory id | Rare — only unrecognized bare ids | **Kept** | V1's `search_by_subcategory()` has a more permissive wildcard match; this is a taxonomy-resolution gap, not a proven-unnecessary error handler |
| 4 | Suggestions (`/v1/search/suggest`, `/v2/search/suggest`) | `unified_search.py` | `_fetch_flat_suggestions()` (~L368-407) | Exception-based | V2 raises | No | **Kept** | Free-text input (partial user typing), same rationale as #1 |
| 5 | Simple search (`/rs/search`) | `simple_search.py` | `simple_search()` | Exception-based | V2 raises | No | **Kept** | Free-text query, same rationale as #1. (This endpoint was **fully unmigrated** before this session — now V2-first with this fallback, closing the one remaining 100%-V1 endpoint) |
| 6 | PDP (`/api/v1/product/<id>`) | `product_api.py` | `get_product_detail()` (~L345-359) | Exception-based | V2 raises | No — 0/0, deterministic id lookup | **Removed** | Exceptions now propagate |
| 6b| — same — | `product_api.py` | same function | Not-found | V2 lookup cleanly returns no document | Not observed with real ids | **Kept — unconditionally, not gated on engine** | Real indexing-lag gap between the two indices; restored to unconditional after breaking `test_pdp_stock_fallback.py` (8 tests) when gated on `engine != "v2"` — see Method §4 |
| 7 | Flean Score (`/api/v1/flean-score`) | `product_api.py` | `get_flean_score()` | Exception-based | V2 raises | No | **Removed** | Same as #6 |
| 7b| — same — | `product_api.py` | same function | Not-found | same as #6b | Not observed | **Kept — unconditionally** | Same as #6b |
| 8 | Batch PDP (`/api/v1/products/pdp/batch`) | `product_api.py` | `get_product_details_batch()` | Exception-based | V2 raises | No | **Removed** | Individual not-found ids already surface via the response's `not_found` list without needing a fallback branch |
| 9 | Alternatives (`/api/v1/product/<id>/alternatives`) | `product_api.py` | `get_healthier_alternatives()` | Exception-based | V2 raises | No | **Removed** | Not-found was already handled as a direct 404 inside the V2 branch (not a fallback), unchanged |
| 10 | Recommended (`/api/v1/product/<id>/recommended`) | `product_api.py` | `get_recommended_products()` | Exception-based | V2 raises | No | **Removed** | Same as #9 |
| 11 | Catalogue (`/api/v1/catalogue`) | `product_api.py` | `get_catalogue()` | Exception-based | V2 raises | No — 0/0 across tested category paths | **Removed** | Deterministic category paths |
| 12 | Products (`/api/v1/products`, query branch) | `product_api.py` | `get_products_unified()` (~L1374-1410) | Exception-based | V2 raises | No | **Kept** | Shares the query-driven branch with #1 |
| 13 | Products (`/api/v1/products`, subcategory branch) | `product_api.py` | same function (~L1412-1422) | Exception-based | V2 raises | No | **Removed** | Same rationale as #3 |
| 14 | Best Sellers | `home_page.py` | `_get_best_selling_data()` | Exception-based | V2 raises | No — 0/0, fixed category paths | **Removed** | Deterministic, fixed `BEST_SELLING_CATEGORY_PATHS` |
| 15 | Supplements | `home_page.py` | `_get_supplements_data()` | Exception-based | V2 raises | No | **Removed** | Same as #14 |
| 16 | Curated | `home_page.py` | `_search_curated_with_filters()` | Exception-based | V2 raises | No — 0/0 across filter combinations tested | **Removed** | Filter-shaped, not free text; every filter combination tested cleanly |
| 17 | Flean Picks | `home_page.py` | `_unified_flean_picks_logic()` | Exception-based | V2 raises | No | **Removed** | Same as #16. `FLEAN_PICKS_FORCE_LEGACY` env override kept as-is — that's a deliberate operator lever, not an auto-fallback |
| 18 | Scanner (`/api/v1/scanner`) | `product_api.py` | `scanner_lookup()` | Exception-based | V2 raises | No | **Kept** | Feeds on OCR/vision-extracted free text — same unbounded-input rationale as #1, arguably even less predictable (model-extracted text, not user-typed) |

## What "removed" means precisely

For rows marked **Removed**, the `try/except Exception: log.warning(...); if engine == "v2": raise`
wrapper was deleted. The V2 call now runs unwrapped: under `SEARCH_ENGINE=auto` or `v2`, an
exception propagates to the route's outer handler and returns a real error (500) instead of being
silently swallowed and retried against V1. **`SEARCH_ENGINE=v1` is unaffected and still works
exactly as before** — that's a deliberate, explicit operator override, not the kind of fallback this
audit is about, and none of it was touched.

**Nothing was deleted from `es_products.py` itself.** Every V1 code path removed-from-the-fallback-position above still exists in
`es_products.py`, still reachable via `SEARCH_ENGINE=v1`, per the standing policy that V1 code is
only removed after full migration is verified and explicitly approved.

## Verification

- Full pytest suite: 192 passed, 1 skipped, 0 failed — both before and after every removal above.
- Live local regression with `SEARCH_ENGINE=v2` (strict) across all 11 removed-fallback endpoints
  plus Batch PDP: every one returned `200`, zero exceptions logged.
- The one real regression this audit caused (over-tightening PDP/Flean Score's not-found check) was
  caught by the pytest suite itself and reverted before being called done — see Method §4.
