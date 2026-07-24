# V1 Runtime Inventory — Complete

Date: 2026-07-23. Every remaining reference to `shopping_bot/data_fetchers/es_products.py`
(`ElasticsearchProductsFetcher`, `get_es_fetcher`, `transform_to_product_card`, `transform_to_pdp`)
anywhere in the runtime, not limited to `search_v2/extension/`. Classified as one of:

- **Must remain** — genuinely V1-specific, no V2 equivalent exists or is planned in this migration's scope.
- **Migrate into V2** — should move, not yet done; blocker noted.
- **Shared platform component** — engine-agnostic, correctly lives where it is regardless of engine.
- **Safe to remove** — nothing currently depends on it.

## Method

`grep -rl "es_products\|ElasticsearchProductsFetcher"` across the entire repo (both `shopbot-main`
and, separately, confirmed zero hits in `search/`), excluding `venv/` and test files, then read
every match to determine whether it's a functional import or a comment/docstring reference.

## A. Search API surface (fully migrated this session — see `V1_FALLBACK_AUDIT.md`)

| File | What's still V1 | Classification |
|---|---|---|
| `unified_search.py`, `product_api.py`, `home_page.py`, `simple_search.py`, `product_search.py` | `get_es_fetcher()` fallback path (exception-based, kept only for free-text query endpoints — see `V1_FALLBACK_AUDIT.md`); `transform_to_product_card()`/`transform_to_pdp()` post-processing (used on BOTH engines' output) | Fallback: **must remain** (deliberate, disclosed). Transforms: **shared platform component** |

`product_search.py` (`/api/v1/products/search`) was found **fully unmigrated** during this audit
— not touched by any prior phase — and has been migrated this session (V2-first, same pattern as
`simple_search.py`). This closes the last fully-V1 Search API endpoint.

## B. Shared platform components (correctly NOT engine-specific)

| Component | File | Why it's correctly shared, not "V1 code" |
|---|---|---|
| `transform_to_product_card()` | `es_products.py` | Pure function: raw `_source`/pre-transformed dict → flat card. Takes no engine-specific input; called on V1 AND V2 output throughout every route |
| `transform_to_pdp()` | `es_products.py` | Same — pure transform, reused directly by V2's PDP path rather than reimplemented (see `FINAL_MIGRATION_REPORT.md` §10) |
| `_resolve_pdp_cta()`, `_extract_lab_report_url()`, `_has_palm_oil_ingredient()`, `_derive_in_stock_from_availability()` | `product_api.py` | Pure functions operating on a raw source dict; no query/client dependency |
| `_v1_filters_to_gw_params()`, `_resolve_subcategory_es_path()` | `unified_search.py` | Despite the name, `_v1_filters_to_gw_params` converts a validated-filters dict (used identically by both engines' request parsing) into V2's `SearchFilters.from_dict()` shape — it's a V1→V2 *translator*, not V1 logic itself. Reused (not duplicated) by `product_api.py`, `product_search.py`, `simple_search.py` via local imports |

**Recommendation, not yet acted on**: these currently live inside `es_products.py`/`product_api.py`
(files named after or dominated by V1 logic) purely for historical reasons. They block
`es_products.py` from ever being fully deletable even after every V1 fallback is gone. Moving them
to a genuinely engine-neutral module (e.g. `shopping_bot/product_transforms.py`) is the one concrete
prerequisite to eventually removing `es_products.py` — noted here, not done in this pass (pure
refactor with no functional change, deferred to avoid unnecessary churn this late in the migration
unless requested).

## C. The V1 fetcher itself

| Component | Classification | Why |
|---|---|---|
| `ElasticsearchProductsFetcher` / `get_es_fetcher()` | **Must remain** | Backs every disclosed fallback in `V1_FALLBACK_AUDIT.md`; deletable only after V1 removal is explicitly approved per standing policy |

## D. Conversational / LLM bot_core flow — genuinely out of this migration's scope

This migration's stated objective (every prior instruction) has been the **Search API surface**:
`/rs/v1/search`, `/rs/search`, `/rs/api/v1/*` product/catalogue/scanner/PDP endpoints. The
WhatsApp/chat conversational flow (`bot_core.py`, `llm_service.py`, the `BackendFunction` registry
in `data_fetchers/__init__.py`) is a **different feature surface** that happens to also call ES,
via its own dispatch table, independent of the Search API. Found during this audit, not previously
migrated, and not touched in this pass:

| File | What it does | Classification | Why not migrated now |
|---|---|---|---|
| `shopping_bot/data_fetchers/__init__.py` | `BackendFunction` registry dispatching LLM tool-calls (`search_products`, skin/hair search, etc.) to `es_products.py` handlers | **Migrate into V2** (eventually) | Large, separate surface (4-intent classification, multi-turn conversation state, skin/hair-specific query builders with no V2 equivalent yet) — migrating it is a project-sized effort of its own, not a drop-in swap like the Search API routes were |
| `shopping_bot/routes/chat.py` | Main `/chat` conversational endpoint; one narrow direct `get_es_fetcher()` usage — `mget_products([selected_product_id])` for the "user selected a product from image suggestions" pathway | **Migrated this session** | Simple, deterministic id lookup — migrated to `extension.pdp.fetch_products_batch()` with V1 fallback, same low-risk pattern as PDP/Batch PDP. Verified: 192/192 pytest |
| `shopping_bot/vision_flow.py` | Vision-triggered chat pathway: builds a custom ES query from OCR-extracted product/brand text, calls `fetcher.suggest_brand()` for brand canonicalization, then `fetcher.search()` | **Migrate into V2 — blocked** | `suggest_brand()` (canonical brand-name resolution against the index's actual brand vocabulary) has **no V2-native equivalent** built in this migration. The query-building and search portions could reuse `extension.search.search()` today (same pattern as Scanner), but brand canonicalization is a real, distinct capability gap — migrating this cleanly means either building a V2-native `suggest_brand()`-equivalent first, or accepting degraded brand-matching quality on this one pathway. Left on V1 rather than either silently regressing brand matching or scope-creeping into a new capability build without being asked |

## E. Developer tooling (not runtime, but references V1 intentionally)

| File | Classification | Why |
|---|---|---|
| `dev_search_cli.py`, `dev_category_browsing_cli.py` | **Must remain — by design** | These are *comparison* tools; they deliberately keep a live V1 path so `compare`/`c` mode can run V1 and V2 side by side. Removing V1 from these would remove the tool's actual purpose |

## F. Search repo (`search/`, indexing pipeline)

Confirmed zero functional V1 references — the `search` repo's `search_v2/` package doesn't import
anything from `shopbot-main`. The legacy indexers (`index_v1.py`, `index.v3.py` through `.v6.py`,
etc.) are standalone historical scripts, not imported by anything active; not part of this
inventory since they're not "runtime" — they're one-shot indexing tools, already superseded by
`search_v2/indexing/index_v2.py`.

## Summary

| Classification | Count | Items |
|---|---|---|
| Must remain (deliberate fallback, disclosed) | 5 endpoint groups | Main search, Products (query), Simple search, Products/search, Scanner — all free-text-query-driven |
| Must remain (fetcher itself) | 1 | `ElasticsearchProductsFetcher`/`get_es_fetcher()` |
| Must remain (dev tooling) | 2 | `dev_search_cli.py`, `dev_category_browsing_cli.py` |
| Must remain (out of scope, distinct feature) | 1 | `data_fetchers/__init__.py` BackendFunction registry (chat/LLM flow) |
| Shared platform component (correctly not V1-specific) | 6 functions | `transform_to_product_card`, `transform_to_pdp`, CTA/lab-report/stock/filter-translation helpers |
| Migrated this session | 3 | `product_search.py` (was 100% unmigrated), `chat.py`'s image-selection lookup, plus all Tier-A fallback removals in `V1_FALLBACK_AUDIT.md` |
| Migrate into V2 — blocked | 1 | `vision_flow.py`'s `suggest_brand()` dependency — no V2 equivalent exists |
| Safe to remove | 0 | Nothing is unreferenced; `search_gateway/` (the one thing that qualified) was already removed |
