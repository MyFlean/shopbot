# Post-Reindex Validation Report

Date: 2026-07-24. Local Search V2 index (`products-search-v2`) deleted and fully rebuilt via the
repository's existing indexing pipeline (`search/search_v2/indexing/index_v2.py all`), using the
real AWS Bedrock embedding backend per your explicit authorization. This report covers the
post-index validation you requested.

## 1. Document count

| Check | Count |
|---|---|
| OpenSearch document count (final) | **6,510** |
| MongoDB, quality-filtered (indexing-eligible, `build_mongo_filter("all")`) | **6,510** |
| MongoDB, raw/unfiltered `products_master` | 21,470 (not the right comparison — most of these fail the quality filter: missing `flean_score`, `ingredients.raw_text`, nutritional data, or `category_paths`) |

**Match confirmed: 6,510 = 6,510.** The initial `index_v2.py all` run indexed 6,446 and failed 64
due to a transient `HTTP 424` Bedrock service error ("The system encountered an unexpected error
during processing"). All 64 were successfully retried via `index_v2.py incremental --upsert-ids`
(the exact recovery path the tool's own error message recommends), bringing the count to the full
6,510.

## 2 & 3. New fields — presence and mapping types

| Field | Docs populated (of 6,510) | Mapping type | Notes |
|---|---|---|---|
| `review_stats` | 57 | `object` (dynamic subfields — `mapping_builder.py` deliberately maps it as a bare `object`, not fixed subfields, since the real shape varies) | See finding below — this is **not** the `tags_and_sentiments`-derived enrichment; see §4 |
| `cons_list` | 0 | `keyword` | Correctly wired (allowlist + mapping); zero because 0/21,470 local Mongo docs have `tags_and_sentiments` (unchanged from prior sessions' finding) |
| `pros_list` | 0 | `keyword` | Same as above |
| `search_keywords` | 0 | `text` (`v2_text_index_analyzer`) | Same as above |
| `ingredients.structured.ingredients` | 1 (after a real bug fix — see §4) | `object` → `{name: text+keyword}` (dynamic) | Only 1 of 21,470 Mongo docs has a genuinely non-empty structured ingredient list in this local snapshot |
| `ingredients.structured.additives` | 3,381 | `object` → dynamically mapped as `text+keyword` | Populated correctly and directly usable |

## 4. A real bug found and fixed during this validation

**Finding**: `ingredients.structured.ingredients` showed 0/6,510 populated immediately after the
initial reindex, despite `ingredients.structured.additives` showing 3,381/6,510 — an asymmetry that
warranted root-causing per your instruction, not just noting.

**Trace**: the one Mongo document with a genuinely non-empty `ingredients.structured.ingredients`
array (`_01KNCREA1234TINECREAT5678C`, "Optimum Nutrition Micronized Creatine Powder") stores that
array as **plain strings** (`["Creatine Monohydrate"]`), not the `{name, percentage, is_composite,
components}` dict shape `_finalize_structured_ingredients()` was written to expect.
`isinstance(item, dict)` was `False` for a string, so every entry was silently skipped —
`out["ingredients"]` was never set, and the whole key was dropped.

**Root cause**: `search/search_v2/indexing/document_transformer.py`'s `_finalize_structured_ingredients()`
only handled one of two real Mongo shapes for this field.

**Fix**: added a branch handling bare-string ingredient entries (`{"name": item.strip()}`), alongside
the existing dict-shape handling. Verified: `search_v2/tests/test_document_transformer.py` +
`test_mapping_builder.py` (33 tests) still pass; the affected document was re-upserted via
`index_v2.py incremental --upsert-ids _01KNCREA1234TINECREAT5678C` and now correctly shows
`ingredients.structured.ingredients: [{"name": "Creatine Monohydrate"}]` in both OpenSearch and the
live `/rs/api/v1/product/<id>` runtime response.

**Second finding, not a bug — a data-source clarification**: `review_stats` showing 57/6,510
populated looked at first like the `tags_and_sentiments`-derived enrichment was finally working.
Traced instead to: `sanitize_for_es()` only *overwrites* `doc["review_stats"]` when
`tags_and_sentiments` is present as a dict — since 0 Mongo docs have that field (re-confirmed
directly this session), that code path never fires. The 57 populated documents are a **pre-existing,
unrelated raw Mongo field** (`review_stats: {"reviewed_at": <timestamp>}`, no rating data) passing
through untouched, now reaching the index for the first time only because the `cons_list`-focused
allowlist fix from earlier in this migration also happened to allowlist `review_stats` generally.
This is correct, intentional behavior (nothing to fix) — just not evidence that the
`tags_and_sentiments` enrichment itself is populating anything yet.

## 5. Representative product comparison

Product: `01K1B1BNP0YS9QA9A607VJYKA8` ("Alpino Super Muesli Fruit & Nuts Whole Oats & Whole Grain") —
chosen because it has real `ingredients.structured.additives` data (`["Antioxidant"]`) and an empty
`structured.ingredients` array, exercising both the structured-data path and the raw-text fallback.

| Layer | `ingredients.structured` | Notes |
|---|---|---|
| **MongoDB** (source) | `{"ingredients": [], "additives": ["Antioxidant"], "oils": [], "sweetners": []}` | `oils`/`sweetners` are Mongo-only fields with no indexing-side consumer defined — correctly not carried into the index (not part of this migration's field set) |
| **OpenSearch** (indexed) | `{"additives": ["Antioxidant"]}` | Exact match on `additives`; empty `ingredients` array correctly omitted (not indexed as an empty key — matches the "or None" cleanup logic, not a bug) |
| **Runtime** (`GET /rs/api/v1/product/<id>`) | `ingredients: [..., {"name": "Antioxidant"}]` (11 entries) | `transform_to_pdp()` correctly falls back to parsing `raw_text` since `structured.ingredients` was empty, and separately confirmed "Antioxidant" appears via the additives-derived raw text — **nothing lost**, the shared PDP transform's documented "prefer structured, fallback to raw_text" behavior is working exactly as designed |

A second product, `_01KNCREA1234TINECREAT5678C` (real, non-empty `structured.ingredients` after the
§4 fix), was also checked end-to-end: MongoDB → OpenSearch → runtime all show
`[{"name": "Creatine Monohydrate"}]` identically — confirming the structured path (not just the
fallback) also works correctly when real structured data exists.

## 6. Representative searches

- `GET /rs/v1/search?query=creatine` → `engine: v2, total: 74`, correctly surfaces
  `_01KNCREA1234TINECREAT5678C` in the top results (lexical match against `name`/`combined_text`,
  same retrieval path used for every query — the structured-ingredients fix doesn't currently feed
  a dedicated lexical field, so this confirms baseline retrieval is undisturbed, not a new
  ingredients-driven ranking signal).
- `GET /rs/v1/search?query=muesli` → returns real muesli products; `avg_rating` correctly `None` on
  all of them (honest reflection of §4's finding — no real rating data exists locally yet).
- Confirmed via `search_v2/ranking/business_ranking.py` (grep) that `review_stats.avg_rating` and
  `review_stats.total_reviews` **are** read for ranking — the mechanism is wired correctly and will
  activate the moment real rating data exists (production, most plausibly). `cons_list`/`pros_list`/
  `search_keywords` are indexed but **not yet read by any runtime code** (V1 or V2) — confirmed via
  repo-wide grep; `cons_list` is the one exception, read directly by `transform_to_pdp()` for the
  PDP "watch outs" section (`shopping_bot/data_fetchers/es_products.py`, confirmed).

## 7. Validation summary table

| Field | In Mongo? | In OpenSearch? | In runtime response? | Requires production reindex? |
|---|---|---|---|---|
| `review_stats` | Yes (151 docs, `{reviewed_at}` shape only — not the rating enrichment) | Yes (57 docs — some don't survive earlier pipeline steps, not investigated further as it's a pre-existing unrelated field) | Yes (`to_product_card()`, `business_ranking.py`) | **Yes** — the real `avg_rating`/`total_reviews` enrichment still requires production's `tags_and_sentiments` data; the local passthrough seen here is a different, coincidental field |
| `cons_list` | No (0/21,470 — needs `tags_and_sentiments`) | No | Yes, wired in `transform_to_pdp()` (untested locally — no data) | **Yes** |
| `pros_list` | No | No | Not yet read by any runtime code | **Yes** |
| `search_keywords` | No | No | Not yet read by any runtime code | **Yes** |
| `ingredients.structured.ingredients` | Yes (1 real doc, after finding/fixing a plain-string-shape bug) | Yes (1/6,510) | Yes, confirmed via live PDP response | **Optional** — the fix is already proven correct against real (if rare) local data; a production reindex would surface it much more broadly since production's Mongo data is expected to have far more populated structured-ingredient entries |
| `ingredients.structured.additives` | Yes (3,381/21,470-eligible docs) | Yes (3,381/6,510) | Yes, confirmed via live PDP response | **No** — already fully populated and verified working against real local data |

## Regression status

- `search` repo: `test_document_transformer.py` + `test_mapping_builder.py` — 33/33 passing after
  the §4 fix (unrelated `test_v5_indexing.py` collection error confirmed pre-existing —
  `ModuleNotFoundError: tqdm.auto`, an environment issue in an unrelated legacy test file, not
  touched by this change).
- `shopbot-main` repo: live-tested `/rs/v1/search` and `/rs/api/v1/product/<id>` against the rebuilt
  index — both correct, `engine: v2` confirmed, no errors.
