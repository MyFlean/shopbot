# Endpoint Migration Matrix

Source: full Flask route table (`app.url_map`, 43 routes, enumerated directly — not assumed) cross-
referenced against the supplied Postman collection (13 unique requests,
`Flean_HomePage_Search_APIs.postman_collection.json`). Postman coverage marked explicitly — see
§6 of `SEARCH_V2_MIGRATION_ARCHITECTURE.md` for why the collection is treated as a confirmed-
important subset, not the complete contract.

Excluded as out-of-scope for the *search* migration (chat/LLM flow, not search-dependent in the
sense this program targets): `/rs/chat/*`, `/flow/*`, `/__diagnostics/*`, `/static/*`,
`/rs/api/v1/admin/cards-config/reload`. These consume search indirectly via `bot_core`, not
through the fetchers this migration touches.

| Endpoint | Method | In Postman? | Current impl | V2 plan | Regression status |
|---|---|---|---|---|---|
| `/rs/health` | GET | No | N/A (health check) | No change | N/A |
| `/rs/search` | POST | **Yes** | `simple_search.py` → `get_es_fetcher()` (V1) | Native V2 via `search/` extension | Not started |
| `/rs/v1/search` | GET,POST | No (gap — see §6) | `unified_search.py` → gateway when query present, V1 fallback otherwise | **Already V2-native** for the query-driven case; subcategory-only case is V1 (see category_browsing) | Baseline established this session (multiple queries, both local & prod) |
| `/rs/v1/search/suggest` | GET,POST | No (gap) | `get_es_fetcher().search_suggestions()` (V1, flat shape) | `suggestions/` extension via `SearchGateway.suggest()` | Not started — recommended Phase 1 |
| `/rs/v2/search/suggest` | GET,POST | No (gap) | Same V1 call, grouped-by-brand response shape | Same gateway method, different response shaping | Not started — recommended Phase 1 |
| `/rs/api/v1/home/banners` | GET | **Yes** | `home_page.py`, static JSON + `@lru_cache` | No search dependency — no migration needed | N/A |
| `/rs/api/v1/home/categories` | GET | **Yes** | `home_page.py`, static JSON | No search dependency | N/A |
| `/rs/api/v1/home/best-selling` | GET | **Yes** | `home_page.py` → `es_products.py` V1 aggregation | `bestsellers/` extension | Not started |
| `/rs/api/v1/home/supplements` | GET | No (gap) | Same pattern as best-selling | `bestsellers/` extension (shared logic) | Not started |
| `/rs/api/v1/home/curated` | GET,POST | **Yes** | `home_page.py` → V1 `_search_curated_with_filters` | `curated/` extension | Not started |
| `/rs/api/v1/home/curated/all` | GET,POST | **Yes** | Same | `curated/` extension | Not started |
| `/rs/api/v1/home/flean-picks` | GET,POST | No (gap) | `home_page.py` → V1 `_unified_flean_picks_logic` | `flean_picks/` extension | Not started |
| `/rs/api/v1/home/flean-picks/<collection_key>` | GET | No (gap) | Same | `flean_picks/` extension | Not started |
| `/rs/api/v1/home/why-flean` | GET | **Yes** | Static content | No search dependency | N/A |
| `/rs/api/v1/home/collaborations` | GET | **Yes** | Static content | No search dependency | N/A |
| `/rs/api/v1/home/validation-candidates` | GET | No (gap) | `home_page.py`, Redis validation cache | Engine-agnostic (operates on IDs) | N/A |
| `/rs/api/v1/home/unified` | GET,POST | No (gap) | `home_page.py`, combines multiple V1 sections | Depends on completion of `bestsellers/curated/flean_picks` | Not started |
| `/rs/api/v1/home/health` | GET | **Yes** | Health check | N/A | N/A |
| `/rs/api/v1/home/refresh` | POST | **Yes** | Clears `@lru_cache` | No search dependency | N/A |
| `/rs/api/v1/home/reload` | POST | No (gap) | Similar to refresh | No search dependency | N/A |
| `/rs/api/v1/product/<id>` | GET | **Yes** | `product_api.py` → V1 `transform_to_pdp` | `pdp/` extension (needs 3-field indexing allowlist addition first) | Not started |
| `/rs/api/v1/product/<id>/alternatives` | GET | No (gap) | `product_api.py` (V1) | `recommendations/` extension | Not started |
| `/rs/api/v1/product/<id>/recommended` | GET | No (gap) | `product_api.py` (V1) | `recommendations/` extension | Not started |
| `/rs/api/v1/products/pdp/batch` | POST | No (gap) | `product_api.py` (V1) | `pdp/` extension (batch variant) | Not started |
| `/rs/api/v1/scanner` | POST | **Yes** | `product_api.py` (V1, ID lookup) | Likely no migration needed (ID-fetch, not search) — confirm during Phase planning | Not started |
| `/rs/api/v1/catalogue` | GET | **Yes** | `product_api.py` (V1) | `category_browsing/` extension (shared with subcategory logic) | Not started |
| `/rs/api/v1/catalogue/mapping` | GET | No (gap) | `product_api.py` | Likely static/config — confirm before migrating | Not started |
| `/rs/api/v1/flean-score` | GET | No (gap) | `product_api.py` (V1, ID lookup) | Likely no migration needed (ID-fetch) | Not started |
| `/rs/api/v1/products` | GET,POST | No (gap) | `product_api.py` (V1) | Confirm exact capability before assigning an extension module | Not started |
| `/rs/api/v1/products/search` | GET,POST | No (gap) | `product_api.py` (V1) | Likely folds into `search/` extension | Not started |
| `/rs/api/v1/products/health` | GET | No (gap) | Health check | N/A | N/A |

**Postman coverage gap, stated plainly:** 8 of the 13 Postman requests map to endpoints that have
no search dependency at all (banners, categories, why-flean, collaborations, health, refresh) —
meaning the *search-relevant* Postman coverage is thin (best-selling, curated ×2, PDP, scanner,
catalogue, basic `/rs/search`). The endpoints doing the most business-critical, V1-dependent work
(flean-picks, alternatives, recommended, suggestions, subcategory browsing) are **not** in the
supplied collection. Recommend either an updated Postman collection or explicit sign-off that
manual/code-level regression is acceptable for those endpoints.
