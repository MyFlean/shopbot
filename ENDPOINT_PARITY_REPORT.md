# Endpoint Parity Report

Date: 2026-07-23 (final pre-production pass). Every registered `@bp.route(...)` in the application,
its current implementation, engine, fallback status, and production readiness.

**Headline result**: every search-dependent endpoint now executes Search V2 natively under the
default (`auto`) configuration, with zero exception-based auto-fallback-to-V1 remaining anywhere.
Live regression under `auto` and strict `SEARCH_ENGINE=v2` produced **byte-for-byte identical
results on all 40 tested requests** (see `POSTMAN_REGRESSION.md`) — the strongest available
evidence that V1 is not silently executing anywhere in the default path. `SEARCH_ENGINE=v1` remains
available everywhere as an explicit, deliberate operator override (not a "fallback" in the sense
this report tracks) — flipping it makes every row below execute V1 instead, by design.

## Search-dependent endpoints

| Endpoint | Current implementation | V1 or V2 | Fallback present | Fallback triggered during regression | Production ready |
|---|---|---|---|---|---|
| `GET/POST /rs/v1/search` (query) | `search_v2.extension.search.search()` | **V2** (V1 only via explicit `SEARCH_ENGINE=v1`) | No (removed this pass) | N/A — no auto-fallback exists | Yes |
| `GET/POST /rs/v1/search` (subcategory) | `search_v2.extension.category_browsing.browse()` | **V2** (V1 only via explicit override or unresolvable subcategory id) | No | N/A | Yes |
| `GET/POST /rs/v1/search` (filters-only) | `search_v2.extension.search.search()` (empty query, filter-only retrieval) | **V2** | No | N/A | Yes |
| `GET/POST /rs/v1/search/suggest` | `search_v2.extension.suggestions.suggest()` | **V2** | No | N/A | Yes |
| `GET/POST /rs/v2/search/suggest` | `search_v2.extension.suggestions.suggest()` | **V2** | No | N/A | Yes |
| `POST /rs/search` | `search_v2.extension.search.search()` | **V2** | No | N/A | Yes |
| `GET/POST /rs/api/v1/products/search` | `search_v2.extension.search.search()` | **V2** | No | N/A | Yes |
| `GET /rs/api/v1/products/health` | Static health payload | N/A | N/A | N/A | Yes |
| `GET /rs/api/v1/product/<id>` (PDP) | `search_v2.extension.pdp.fetch_product()` | **V2** (not-found still checks V1 — data-completeness, not an error fallback) | No exception-fallback; not-found double-check present | No | Yes |
| `GET /rs/api/v1/flean-score` | `search_v2.extension.pdp.fetch_product()` + shared `transform_to_pdp()` | **V2** (same not-found double-check as PDP) | No exception-fallback; not-found double-check present | No | Yes |
| `POST /rs/api/v1/products/pdp/batch` | `search_v2.extension.pdp.fetch_products_batch()` | **V2** | No | N/A | Yes |
| `GET /rs/api/v1/product/<id>/alternatives` | `search_v2.extension.recommendations.similar_products()` | **V2** | No | N/A | Yes |
| `GET /rs/api/v1/product/<id>/recommended` | `search_v2.extension.recommendations.similar_products()` | **V2** | No | N/A | Yes |
| `POST /rs/api/v1/scanner` | `search_v2.extension.search.search()` | **V2** | No | N/A | Yes (placeholder Postman image data fails base64 decode — pre-existing collection data issue, not a code regression) |
| `GET /rs/api/v1/catalogue` | `search_v2.extension.category_browsing.browse()` | **V2** | No | N/A | Yes |
| `GET /rs/api/v1/catalogue/mapping` | Static JSON read, no ES dependency | N/A | N/A | N/A | Yes |
| `GET/POST /rs/api/v1/products` (query) | `search_v2.extension.search.search()` | **V2** | No | N/A | Yes |
| `GET/POST /rs/api/v1/products` (subcategory) | `search_v2.extension.category_browsing.browse()` | **V2** | No | N/A | Yes |
| `GET /rs/api/v1/home/best-selling` | `search_v2.extension.bestsellers.best_selling()` | **V2** | No | N/A | Yes |
| `GET /rs/api/v1/home/supplements` | `search_v2.extension.bestsellers.best_selling()` | **V2** | No | N/A | Yes |
| `GET/POST /rs/api/v1/home/curated` | `search_v2.extension.curated.curate()` | **V2** | No | N/A | Yes |
| `GET/POST /rs/api/v1/home/curated/all` | `search_v2.extension.curated.curate()` | **V2** | No | N/A | Yes |
| `GET/POST /rs/api/v1/home/flean-picks` | `search_v2.extension.flean_picks.flean_picks()` | **V2** (`FLEAN_PICKS_FORCE_LEGACY` env remains an explicit operator lever, unchanged) | No | N/A | Yes |
| `GET /rs/api/v1/home/flean-picks/<collection_key>` | `search_v2.extension.flean_picks.flean_picks()` | **V2** — **found unmigrated and fixed this pass** (was calling V1's `_fetch_subcategory_products()` directly despite its "wrapper around unified logic" docstring) | No | N/A | Yes |
| `GET /rs/api/v1/home/unified` (GET+POST) | Composes the above (best-selling, curated, flean-picks, `_get_curated_data()`) | **V2** | No | N/A | Yes |
| `GET /rs/api/v1/home/validation-candidates` | New `_fetch_full_catalog_validation_candidates_v2()` — **found unmigrated and fixed this pass** | **V2** | No | N/A | Yes |
| Chat image-selection lookup (internal, `/chat`) | `search_v2.extension.pdp.fetch_products_batch()` — **found unmigrated and fixed this pass** | **V2** | No | N/A | Yes |
| Vision-triggered chat flow (internal, `vision_flow.py`) | `search_v2.extension.search.search()` + new `search_v2.extension.brand.suggest_brand()` — **found unmigrated and fixed this pass** | **V2** | No | N/A | Yes |

## Endpoints with no search dependency (unaffected by this migration, included for completeness)

| Endpoint | Notes |
|---|---|
| `GET /rs/api/v1/home/banners`, `/categories` | Static JSON from `shopping_bot/data/home/` |
| `GET /rs/api/v1/home/why-flean`, `/collaborations` | Static JSON |
| `POST /rs/api/v1/home/refresh`, `/reload` | Cache-clear operations |
| `GET /rs/api/v1/home/health`, `/rs/health`, `/rs/api/v1/products/health` | Static health payloads |
| `GET /rs/__routes`, `/rs/redis-health` | Infra introspection |
| `POST /rs/chat`, `GET /rs/chat/flags`, `/rs/chat/health`, `/rs/chat/debug/<id>` | Conversational flow — see "Legacy search validation" below for the one internal search-touching pathway (already covered above) |
| `POST /rs/chat/stream` | Streaming variant of `/rs/chat` |
| `GET /rs/chat/ui` | Debug chat UI page |
| `POST /rs/reset` | Session reset |
| `POST /rs/flow/onboarding`, `/products`, `/product_recommendations` + healths | Onboarding flow — does not call `es_products`/search directly (confirmed via grep) |
| `POST /rs/api/v1/admin/cards-config/reload` | Admin cache reload |

## Not migrated — documented blocker

| Component | Status | Why it exists | What's missing | What blocks removal | What's required |
|---|---|---|---|---|---|
| `shopping_bot/data_fetchers/__init__.py`'s `BackendFunction.SEARCH_PRODUCTS` handler (`search_products_handler` / `build_search_params` in `es_products.py`) — the LLM tool-call search used by the `/rs/chat` conversational flow's "search for products" intent | **V1, not migrated** | This is a fundamentally different capability from the REST Search API: `build_search_params()` branches into domain-specific (F&B vs. personal-care/skin) planners driven by multi-turn conversational context (`ctx.session`, slot-filling state, LLM-extracted intent across turns), and `search_products_handler()` implements its own independent 6-step zero-result relaxation tree (price-any → drop-hard-soft → sibling-category variants → category-drop), separate from and more elaborate than Search V2's own product-intent relaxation (built for direct REST calls, not multi-turn chat context) | A V2-native equivalent of the conversational-context-to-search-params translation layer, and a decision on whether V2's existing relaxation logic should replace or run alongside the chat-specific 6-step tree | Migrating this safely requires dedicated regression infrastructure for the conversational flow itself (multi-turn session state, slot-filling correctness) that doesn't exist today — the Postman-based regression this pass relied on tests the REST API surface, not multi-turn chat sessions | A project-sized effort: (1) build V2-native context-to-filter translation covering both F&B and personal-care planners, (2) decide fallback/relaxation strategy, (3) build conversational-flow regression tooling before changing this live, user-facing chat path |
