# Legacy Search Validation — Final Pass

Date: 2026-07-23. Every place that previously depended on Version 1 search, checked individually:
migrated, replaced, or documented blocker. No hidden Version 1 search execution paths remain.

## Method

`grep -rl "es_products\|ElasticsearchProductsFetcher" --include="*.py"` across the entire repo
(both `shopbot-main` and, separately, confirmed zero hits in the `search` indexing-pipeline repo),
then every match read individually to classify as: functional import (needs disposition) vs.
comment/docstring reference (no disposition needed, historical context only).

## Every file that imports `es_products` / `ElasticsearchProductsFetcher`

| File | Nature of reference | Disposition |
|---|---|---|
| `shopping_bot/data_fetchers/es_products.py` | The file itself | **Must remain** — hosts `get_es_fetcher()` (the explicit-override-only path for every migrated endpoint) and the shared `transform_to_product_card()`/`transform_to_pdp()` transform layer used by both engines |
| `shopping_bot/routes/unified_search.py` | `get_es_fetcher()` | **Migrated** — reachable only via explicit `SEARCH_ENGINE=v1` (or, for the subcategory branch, an unresolvable bare category id) |
| `shopping_bot/routes/home_page.py` | `get_es_fetcher()` (best-selling, supplements, curated, flean-picks, flean-picks/&lt;key&gt;, validation-candidates, `_fetch_products_by_ids`) | **Migrated** — every one of these now V2-native, explicit-override-only. Three of these (flean-picks/&lt;key&gt;, validation-candidates, `_fetch_products_by_ids`) were found unmigrated during this final pass and fixed |
| `shopping_bot/routes/product_api.py` | `get_es_fetcher()` (PDP, Flean Score, Batch PDP, Alternatives, Recommended, Scanner, Catalogue, Products) | **Migrated** — all V2-native, explicit-override-only (PDP/Flean Score also keep an unconditional not-found→V1 double-check, a data-completeness check, not an error fallback) |
| `shopping_bot/routes/product_search.py` | `get_es_fetcher()` | **Migrated this session** (found fully unmigrated at the start of this session's work) — explicit-override-only |
| `shopping_bot/routes/simple_search.py` | `get_es_fetcher()` | **Migrated this session** — explicit-override-only |
| `shopping_bot/routes/chat.py` | `get_es_fetcher()` (image-selection product lookup) | **Migrated this session** — explicit-override-only |
| `shopping_bot/vision_flow.py` | `get_es_fetcher()`, `fetcher.suggest_brand()` | **Migrated this final pass** — required building a new `search_v2.extension.brand.suggest_brand()` wrapper first (see below); explicit-override-only |
| `shopping_bot/llm_service.py` | Comment only (`# Update _build_skin_es_query in es_products.py`) | No disposition needed |
| `shopping_bot/__init__.py` | Imports `transform_to_pdp` etc. at startup for warmup | Shared transform layer, not V1-specific logic |
| `shopping_bot/data_fetchers/__init__.py` | `from . import es_products` — triggers `es_products.py`'s `register_fetcher()` calls | **Documented blocker** — see below |
| `dev_search_cli.py`, `dev_category_browsing_cli.py` | V1 fetcher used deliberately, for V1-vs-V2 comparison mode | **Must remain by design** — these are comparison tools; removing V1 here would remove the tool's purpose |
| `search_v2/extension/*`, `search_v2/retrieval/*`, `search_v2/embedding/*` | Comments/docstrings only (confirmed via individual read of every match) | No disposition needed — zero functional V1 dependency anywhere in `search_v2/` |

## The one documented, unmigrated blocker

**`shopping_bot/data_fetchers/__init__.py`'s `BackendFunction.SEARCH_PRODUCTS` registration** →
`search_products_handler()` / `build_search_params()` in `es_products.py` — the tool-call search
used by the `/rs/chat` WhatsApp/conversational flow's "search for products" intent.

- **Why it still exists**: this is architecturally a different capability from the REST Search API.
  `build_search_params()` branches into domain-specific (food & beverage vs. personal-care/skin)
  planners driven by multi-turn conversational state (`ctx.session`, slot-filling, LLM-extracted
  intent carried across turns) — none of which the REST API's request/response model has to deal
  with. `search_products_handler()` also implements its own 6-step zero-result relaxation tree
  (price-any → drop-hard-soft-keep-category → sibling-category variants → category-drop),
  independent of and more elaborate than Search V2's own product-intent relaxation (which is
  designed for single-shot REST requests, not multi-turn chat context).
- **Exactly what functionality is missing**: a V2-native equivalent of the conversational-context
  (session/slots) → search-filter translation layer, covering both the F&B and personal-care
  planners, plus a decision on whether V2's relaxation logic should replace the chat-specific
  6-step tree or the two should coexist.
- **What blocks migration**: no regression tooling exists for the conversational flow itself. This
  session's regression work (Postman runner, live `SEARCH_ENGINE=v2` testing) validates the REST API
  surface — request in, response out. The chat flow's correctness depends on multi-turn session
  state and slot-filling behavior that a single-request Postman collection cannot exercise.
- **What work is required before it can be removed**: (1) build the context-to-filter translation
  layer for both domain planners, (2) decide the relaxation-tree question, (3) build or acquire
  multi-turn conversational regression tooling, (4) migrate with that tooling in place. Not
  attempted in this pass — flagged explicitly rather than rushed or silently left in place.

## Confirmation: no hidden execution paths

- Every `@bp.route(...)` in the application was enumerated and classified (see
  `ENDPOINT_PARITY_REPORT.md`) — none were found calling V1 search logic through an
  undocumented/indirect path.
- `grep -rn "os.getenv(\"SEARCH_ENGINE\"\|_search_engine()"` across every route file confirms every
  remaining V1-reachable site is gated by an explicit, visible `engine != "v1"` (or equivalent)
  check — no bare/unconditional `get_es_fetcher()` call reaches a search operation without going
  through this gate first, except the one documented blocker above.
- Live regression (`auto` vs strict `SEARCH_ENGINE=v2`, 40/40 requests) produced **zero
  differences** — direct empirical proof that no undocumented V1 path fires under default
  configuration (see `POSTMAN_REGRESSION.md`'s final section).
