# Search V1 Removal Plan

**Nothing in this file has been deleted or modified. This is a living tracking document only,
updated as each phase completes. Deletion happens only after full migration is verified and
explicitly approved by you.**

Status: All planned capabilities implemented (Suggestions, Category Browsing, Taxonomy, Dynamic
Filters, Best Sellers/Supplements, Curated/Flean Picks, Recommendations/Alternatives, PDP, Scanner,
Flean Score, Catalogue, Products, Batch PDP, Products/search, chat-flow image-selection lookup).
`search_gateway/` has been deleted — its logic now lives in `search_v2/extension/search/`.

A full **fallback audit** (`V1_FALLBACK_AUDIT.md`) removed exception-based auto-fallback-to-V1 for
every deterministic, fully-enumerable-input capability — those rows below now say **"V1 reachable
only via `SEARCH_ENGINE=v1`"**, not "fallback on error". It was deliberately kept for every
free-text-query-driven capability (main search, filters-only search, suggestions, Simple Search,
Products/search query branch, Scanner) — those rows still say "fallback on error", a disclosed,
evidence-based choice, not an oversight.

| V1 file / function | Purpose | Business capability | Current status | Replacement module | Still imported? | Still required? | Safe to remove? |
|---|---|---|---|---|---|---|---|
| `shopping_bot/data_fetchers/es_products.py` | Legacy ES/OpenSearch client + all V1 query/transform logic | Everything — search, PDP, home, best-selling, picks, alternatives, suggestions, category browsing, catalogue, scanner, flean-score, products, batch PDP, chat image-selection | Fallback-only (query-driven endpoints) or explicit-`SEARCH_ENGINE=v1`-only (deterministic endpoints) for every migrated endpoint | Split across every `search_v2/extension/*` module | Yes — every route file | **Yes — `transform_to_product_card()`/`transform_to_pdp()` (shared, engine-agnostic transforms live here) + the disclosed fallback/override paths** | No |
| `shopping_bot/routes/simple_search.py` | `/rs/search` endpoint | Basic search (Postman "Basic Search") | **Migrated** — V1 fallback only on error (free-text query, fallback kept) | `search_v2/extension/search/` | Yes — fallback only | Fallback only | No |
| `shopping_bot/routes/product_search.py` | `/rs/api/v1/products/search` endpoint | Filtered product search API | **Found fully unmigrated and migrated this session** — was 100% V1, never touched by any prior phase. V1 fallback only on error (free-text query) | `search_v2/extension/search/` | Yes — fallback only | Fallback only | No |
| `shopping_bot/routes/product_api.py` — PDP, Flean Score, Batch PDP, Alternatives, Recommended, Catalogue | Product detail + lookup + category-listing endpoints | **Migrated; fallback audit removed exception-based auto-fallback** — V1 reachable only via `SEARCH_ENGINE=v1` (PDP/Flean Score also unconditionally double-check V1 on a clean "not found", a data-completeness check, not an error fallback) | `search_v2/extension/{pdp,recommendations,category_browsing}/` | Yes — explicit-override or not-found-check only | Explicit-override / not-found-check only | No |
| `shopping_bot/routes/product_api.py` — Scanner, Products (query branch) | Image lookup, unified products query search | V1 fallback only on error (free-text/OCR-derived query, fallback kept) | `search_v2/extension/search/` | Yes — fallback only | Fallback only | No |
| `shopping_bot/routes/product_api.py` — Products (subcategory branch) | Unified products category browsing | **Fallback audit removed exception-based auto-fallback** — V1 reachable only via `SEARCH_ENGINE=v1` | `search_v2/extension/category_browsing/` | Yes — explicit-override only | Explicit-override only | No |
| `shopping_bot/routes/home_page.py`'s `_get_best_selling_data`/`_get_supplements_data`/`_search_curated_with_filters`/`_unified_flean_picks_logic` | Home sections | Best Sellers, Supplements, Curated, Flean Picks | **Fallback audit removed exception-based auto-fallback** — V1 reachable only via `SEARCH_ENGINE=v1` (Flean Picks also via `FLEAN_PICKS_FORCE_LEGACY`, an explicit operator lever, unchanged) | `search_v2/extension/{bestsellers,curated,flean_picks}/` | Yes — explicit-override only | Explicit-override only | No |
| `shopping_bot/routes/unified_search.py` — suggest routes | `/rs/v1/search/suggest`, `/rs/v2/search/suggest` | Suggestions/autocomplete | **Migrated** — V1 fallback only on error (free-text partial-typing input, fallback kept) | `search_v2/extension/suggestions/` | Yes — fallback only | Fallback only | No |
| `shopping_bot/routes/unified_search.py` — search route, query/filters-only branch | `/rs/v1/search` | Query search, filters-only browsing | V1 fallback only on error (free-text, fallback kept) | `search_v2/extension/search/` | Yes — fallback only | Fallback only | No |
| `shopping_bot/routes/unified_search.py` — search route, subcategory branch | `/rs/v1/search` | Category/subcategory browsing | **Fallback audit removed exception-based auto-fallback** — V1 reachable only via `SEARCH_ENGINE=v1` or an unresolvable bare subcategory id (taxonomy-resolution gap, not an error fallback) | `search_v2/extension/category_browsing/` | Yes — explicit-override / unresolvable-input only | Explicit-override / unresolvable-input only | No |
| `shopping_bot/routes/chat.py` — image-selection product lookup | `/chat` conversational flow, "user selected a product from image suggestions" | **Found and migrated this session** — was a direct, unmigrated `fetcher.mget_products()` call inside the chat flow | `search_v2/extension/pdp.fetch_products_batch()` | Yes — fallback only | Fallback only | No |
| `shopping_bot/vision_flow.py` | Vision-triggered chat pathway (OCR → product match) | **Not migrated — genuine blocker** | Depends on `fetcher.suggest_brand()`, which has no V2-native equivalent built in this migration; see `V1_RUNTIME_INVENTORY.md` §D | Yes | Yes | No |
| `es_products.py`'s `search_by_subcategory`/`best_selling_by_category_paths_agg`/`search_by_category_paths`/`mget_products_batch` | Underlying V1 aggregation/query/batch methods for the above | Category browsing, Best Sellers, Batch PDP, chat image-selection | No longer called by the primary path for any migrated endpoint — only reachable via the disclosed fallback/override paths above | See above | Yes — fallback/override only | Fallback/override only | No |
| ~~`search_gateway/gateway.py`~~ | ~~`SearchGateway` class~~ | ~~Backed the query-driven half of `/rs/v1/search`~~ | **Deleted this session.** Logic moved to `search_v2/extension/search/core.py` as plain module-level functions (`search()`, `warmup()`) — no wrapping class, per explicit instruction not to depend on a gateway abstraction | `search_v2/extension/search/` | No — package removed | N/A | **Done — removed** |
| `search_v2/retrieval/lexical_query_builder.py`'s `build_suggest_query()` | Completion-suggester query builder | Suggestions | **Fixed during Phase 1** (mandatory-contexts bug), now in active use | N/A — V2 code | Yes | Yes | N/A |
| `search_v2/retrieval/lexical_query_builder.py`'s `build_query()` | Main lexical/hybrid query builder | All query-driven and filters-only search | **Extended this session**: empty-query text now produces a filter-only bool query instead of an unsatisfiable `minimum_should_match` — closes the last `unified_search.py` V1 dependency | N/A — V2 code | Yes | Yes | N/A |

## New V2-native modules introduced (not V1, tracked for completeness)

| Module | Status |
|---|---|
| `search_v2/extension/product/card.py` | Active, shared by 6+ other modules |
| `search_v2/extension/category_browsing/browse.py` | Active — now filter-aware |
| `search_v2/extension/bestsellers/bestsellers.py` | Active |
| `search_v2/extension/curated/curate.py` | Active |
| `search_v2/extension/flean_picks/picks.py` | Active |
| `search_v2/extension/recommendations/similar.py` | Active |
| `search_v2/extension/pdp/pdp.py` | Active — `fetch_product()` + new `fetch_products_batch()` |
| `search_v2/extension/suggestions/suggest.py` | Active |
| `search_v2/extension/search/core.py` | Active — SearchGateway's replacement, backs query search, scanner, filters-only browsing |
| `search_v2/extension/taxonomy/` | Documents existing shared-taxonomy reuse, no new code |
| `search_v2/extension/shop_by_goal/` | Architecture-only, no code |

## Notes

- This table will grow more granular (down to specific *functions* within `es_products.py`, not
  just the whole file) as each phase completes and a specific function's replacement is verified.
- `es_products.py` cannot be deleted yet: it still hosts `get_es_fetcher()` (the fallback/override
  path for every migrated endpoint) and, more importantly,
  `transform_to_product_card()`/`transform_to_pdp()` — the shared, engine-agnostic transform layer
  every V2 capability also calls. These would need to move to a genuinely engine-neutral module
  (not currently planned) before this file could be fully removed; today it remains the correct
  home for them since V1 fallback lives here too.
- Removal of any file requires: (1) its replacement module implemented, (2) regression green for
  every endpoint that used it, (3) your explicit approval. None of these conditions are met yet
  for `es_products.py`. `search_gateway/` is the one file in this plan that met all three and has
  been removed.
- Full per-fallback detail (exact code path, trigger condition, whether it fired during regression,
  why kept or removed) lives in `V1_FALLBACK_AUDIT.md`. Full-runtime dependency classification
  (must remain / migrate into V2 / shared platform component / safe to remove), including
  components outside the Search API surface, lives in `V1_RUNTIME_INVENTORY.md`.
