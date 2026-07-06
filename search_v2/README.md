# Search V2 (ShopBot side)

Search V2 is the current, actively-maintained search engine used by ShopBot.
This document explains how it's wired into ShopBot specifically — the
consumer side. For how the artifacts it reads are *generated* (MongoDB →
OpenSearch indexing, vocabulary/lexicon builders, S3 publishing), see the
companion document in the search repo: `search/search_v2/README.md`. The two
are meant to stay consistent with each other; this one only covers what
lives in *this* repo.

This document covers only `search_v2/` and its integration points
(`search_gateway/`, the relevant routes). Search V1 (`shopping_bot/data_fetchers/es_products.py`'s
`ElasticsearchProductsFetcher`) is described only where it's necessary to
understand how the two coexist today — it is not the subject of this doc.

If you're new to this codebase, read this top to bottom once, then use it as
a reference.

---

## 1. High-level architecture

**Search V1** is the legacy engine: `ElasticsearchProductsFetcher` in
`shopping_bot/data_fetchers/es_products.py`, talking to its own index
(`ELASTIC_INDEX`, default `products_master`) via its own connection code. It
predates Search V2 and still serves most of ShopBot's product-facing routes.

**Search V2** is `search_v2/` + `search_gateway/gateway.py`: a single
`SearchGateway` class that always executes the V2 pipeline (hybrid
lexical+semantic retrieval, business/nutrition ranking, deterministic
no-LLM query understanding) against one unified OpenSearch index
(`products-search-v2`).

**How they currently coexist:** by route, not by shared code. I verified
this directly against every route file in `shopping_bot/routes/` — only
**one** endpoint can ever reach Search V2:

| Route file | Endpoint(s) | Engine |
|---|---|---|
| `unified_search.py` | `POST/GET /rs/v1/search`, `/rs/v1/search/suggest`, `/rs/v2/search/suggest` | **V2, with V1 fallback** (see below) |
| `simple_search.py` | `POST /rs/search` | V1 only (`get_es_fetcher()`) |
| `product_search.py` | `GET/POST /rs/api/v1/products/search`, `/rs/api/v1/products/health` | V1 only |
| `product_api.py` | `/rs/api/v1/product/<id>`, `/rs/api/v1/products`, `/rs/api/v1/catalogue`, `/rs/api/v1/scanner`, alternatives/recommended/PDP-batch/flean-score | V1 only |

Every route except `unified_search.py` imports `get_es_fetcher` and nothing
else — I grepped for `get_search_gateway` across the whole repo and it
appears in exactly `es_products.py` (where it's defined), `unified_search.py`
(the only caller), `shopping_bot/__init__.py` (startup warmup), and
`dev_search_cli.py` (the dev CLI). **`SearchGateway.search()`'s own
docstring currently claims to be "a drop-in replacement... on the
`/rs/api/v1/products/search` route"** — that's stale; verified today, that
route (`product_search.py`) does not call it. Worth fixing the comment or
actually wiring it up — flagged again in §9's gaps.

**Current request flow for `/rs/v1/search`:** `unified_search()` reads
`SEARCH_ENGINE` (env var, default `"auto"`) via its own `_search_engine()`
helper. If not `"v1"` and a query is present, it calls
`get_search_gateway().search(params)`. In `"auto"` mode, any *exception* from
V2 falls back to the V1 fetcher; in `"v2"` mode, a V2 exception is raised
directly with no fallback; a V2 call that succeeds with **zero results is
not treated as a failure** and does not trigger fallback. Whichever engine
produced `products`, they're passed through the *same*
`transform_to_product_card()` (from `es_products.py`) and the same
in-stock/CTA enrichment before the response is returned — see §7.

**Current deployment model** (verified in `shopping_bot/__init__.py`): two
modes, branching on `config_name == 'lambda'`:
- **Lambda**: the gateway is *not* built at app-create time — `app.extensions["search_gateway"] = None`, and `get_search_gateway()`'s own double-checked-locking builds it lazily on the first request that needs it.
- **Everything else** (e.g. gunicorn): the gateway is built and `warmup()`-ed *eagerly* at app-create time, and the embedding model's weights are explicitly preloaded (`get_embedding_service(...).preload()`) in that same (master) process — so that under `gunicorn --preload`, forked workers inherit the already-loaded ~500MB model via copy-on-write instead of each loading their own copy. Gateway/embedding init failure at this stage is caught and logged, not fatal — it just means the first real request pays the init cost instead.

---

## 2. Search pipeline — complete execution order

For a request that reaches `/rs/v1/search` and is routed to V2, this is the
exact call sequence (traced through `unified_search.py` → `gateway.py` →
`search_v2/`):

1. **`unified_search()`** (`shopping_bot/routes/unified_search.py`) parses the request, resolves sort/filters, and calls `gateway.search(gw_params)`.
2. **`SearchGateway.search()`** → **`gateway.py`'s `_search(params)`** closure (built once by `_build_search()`):
   1. **`_map_filters(params)`** → `SearchFilters.from_dict(params)` (explicit filters from the caller — price, category, brand, etc.).
   2. **`process_search_request(...)`** (`query_processing/query_pipeline.py`) — the full query-understanding pipeline, itself several steps:
      - NL filter extraction (`nl_filter_extractor.py`) — strips price/dietary/macro phrases ("under 300 calories") out of the raw text into structured filters.
      - Text normalization (`text_normalization.py`) + **typo correction** (`typo_correction.py`'s `VocabularyCorrector`, built from **vocabulary.json** — see §3).
      - A second NL-filter pass on the corrected text (catches a misspelled dietary/macro phrase that only becomes recognizable after correction).
      - **Product Intent Identification** (`product_intent_extractor.py`'s `ProductIntentExtractor.extract()`), which itself checks, in order:
        1. **Fresh Produce Identification** (`canonical_produce.py`) — exact match, then a small edit-distance fuzzy match, of the *entire* cleaned query against the curated alias map built from **produce_synonyms.json** (see §3). A hit resolves immediately at confidence 1.0/tier `"high"` and sets `fresh_produce_ids` — a hard, authoritative catalog-id allowlist.
        2. If no produce match: the catalog-statistics lexicon lookup against **product_type_lexicon.json** (`resolve_head_term()`), producing a confidence score → tier `"high"` (hard filter) / `"medium"` (soft boost) / `"low"`/`"none"` (no signal).
      - Merges explicit + NL-extracted + Product-Intent filters into one final `SearchFilters` (explicit filters win on conflict).
   3. **`hybrid_search(...)`** (`retrieval/hybrid_search_orchestrator.py`) — builds and runs the lexical query (`lexical_query_builder.py`) and, if enabled, the semantic/kNN query (`semantic_query_builder.py`), fuses them (RRF by default, `retrieval/fusion.py`), and — if Product Intent set a hard filter that returned zero results — relaxes it and retries once (never for a Fresh-Produce id-restriction or when `STRICT_ZERO_RESULTS`/`ENABLE_PRODUCT_INTENT_RELAXATION` say not to).
   4. **`apply_business_ranking(...)`** (`ranking/business_ranking.py`) — multiplies each candidate's relevance score by a bounded (`BUSINESS_MIN_MULTIPLIER`–`BUSINESS_MAX_MULTIPLIER`, default 0.85–1.15) Flean/nutrition/ratings/etc. multiplier, then two-tier sorts (exact product-type match ahead of everything else) when a `product_type` was resolved.
   5. Pagination (`offset`/`size` slice) and **`_to_v1_product()`** per item — maps the ranked V2 item into the flat dict shape every downstream consumer expects (deliberately mirrors V1's own output shape — see §7).
3. Back in **`unified_search()`**: each raw product dict is passed through **`transform_to_product_card()`** (`es_products.py`) — the *same* transformer used for V1 results — plus per-request in-stock derivation and PDP CTA resolution, producing the final response.

---

## 3. Runtime artifacts

| Artifact | Comes from | Generated by | Consumed by (here) | Generated or manual? | In the S3 pipeline? |
|---|---|---|---|---|---|
| `query_processing/vocabulary.json` | Search repo's MongoDB scan | `search/search_v2/query_processing/vocabulary_builder.py` (search repo) | `query_processing/typo_correction.py`'s `VocabularyCorrector`, built once in `gateway.py`'s `_build_search()` | Generated | **Yes** |
| `query_processing/product_type_lexicon.json` | Search repo's MongoDB scan (catalog n-gram statistics) | `search/search_v2/indexing/product_type_lexicon_builder.py` (search repo) | `query_processing/product_intent_extractor.py`, built once in `_build_search()` | Generated | **Yes** |
| `query_processing/produce_synonyms.json` | A human, externally | Nobody — no pipeline generates this file | `query_processing/canonical_produce.py`, built once in `_build_search()` | **Manually maintained — intentionally** | **No** |

**`produce_synonyms.json` is explicitly and permanently excluded from the S3
publishing pipeline.** It is the curated, authoritative source of truth for
vernacular/fruit/vegetable produce aliases; expanding it is a deliberate,
occasional human edit, not a build artifact that goes stale between deploys
the way the other two do. It gets no generator, no regeneration trigger, and
no S3 key — see §5.

Both of the *generated* artifacts fail safe when missing: `load_vocabulary()`
and `load_product_type_lexicon()` return an empty dict/falls back to
`seed_vocabulary()`, and `gateway.py` wraps each in its own `try/except` — a
missing or corrupt vocabulary file only disables typo correction; a missing
lexicon only disables Product Intent Identification (including Fresh
Produce). Neither failure takes down the other or the rest of the request
pipeline.

---

## 4. Interaction with the Search repository

- **What it generates**: `vocabulary.json` and `product_type_lexicon.json`, both from `search/search_v2/indexing/index_v2.py`.
- **When**: every time that script runs in `all`, `incremental`, or `sync` mode (never for the partial `f_and_b`/`personal_care` modes — those explicitly skip regeneration).
- **How they reach ShopBot today**:
  - **Local development**: `index_v2.py` copies both files directly into this repo — `query_processing/vocabulary.json` / `query_processing/product_type_lexicon.json` — if `SEARCH_V2_SHOPBOT_REPO_ROOT` (set in the search repo's own `.env`) points at this repo's checkout path. This is a plain `shutil.copy2`, not a build step; failures are logged as warnings and don't abort the indexing run.
  - **Production**: as of today, **the same local-copy mechanism is the only path** — there is no automated production deployment of these two files into ShopBot yet. The search repo now *publishes* both to S3 as part of the same indexing run (see §5), but ShopBot does not yet fetch from there — that consumer doesn't exist. Until it does, keeping ShopBot's production copies current is a manual/deployment-process concern, not something this repo automates.
- **`produce_synonyms.json`** never comes from the search repo at all — see §3.

---

## 5. Amazon S3 publishing (search-repo side)

The search repo's indexer now publishes `vocabulary.json` and
`product_type_lexicon.json` to S3 as part of the same `all`/`incremental`/
`sync` runs that regenerate them. This section documents that flow from
ShopBot's perspective — for the implementation itself, see
`search/search_v2/README.md`.

**Flow, in this exact order** (per artifact, inside the search repo's
`index_v2.py`):

```
generate artifact (MongoDB scan)
        ↓
write locally (search repo's own query_processing/ directory)
        ↓
upload to S3
        ↓
copy into ShopBot (SEARCH_V2_SHOPBOT_REPO_ROOT — local dev only)
```

**Why this ordering**: S3 is the authoritative publish step; the ShopBot
copy is a separate, local-dev-only convenience. Publishing before copying
means a fail-closed publish failure aborts *before* the local ShopBot copy
runs — a failed publish can never leave ShopBot with a locally-copied
artifact that never actually made it to S3.

**Environment variables** (set in the search repo, not here):

| Variable | Purpose |
|---|---|
| `SEARCH_V2_ARTIFACTS_S3_BUCKET` | Shared S3 bucket for both artifacts |
| `SEARCH_V2_VOCAB_S3_KEY` | Object key for `vocabulary.json` |
| `SEARCH_V2_PRODUCT_TYPE_LEXICON_S3_KEY` | Object key for `product_type_lexicon.json` |
| `SEARCH_V2_S3_FAIL_OPEN` | `true` = warn and continue indexing on upload failure; `false` (default) = abort |

**Fail-open vs. fail-closed**: fail-closed (default) treats a publish
failure exactly like a generation failure — it aborts the indexing run, so
the generated and published artifact can never silently diverge. Fail-open
prints a warning and continues, useful for local indexing runs without S3
credentials configured. Either way, publishing itself is opt-in — leaving
the bucket/key unset makes it a clean no-op, and indexing is unaffected.

**Important, current-state fact**: as of today, **ShopBot does not fetch
anything from S3**. `gateway.py`'s `_build_search()` still reads
`vocabulary.json` and `product_type_lexicon.json` exclusively from this
repo's local `query_processing/` directory, exactly as described in §3/§4.
The S3 publish step is real and running on the search-repo side; a ShopBot
consumer that reads from S3/an API instead of the local file is planned but
**not implemented** — do not assume it exists when debugging a stale
artifact in production.

---

## 6. Running Search V2

**Prerequisites** (this repo): a `.env` with `SEARCH_V2_ES_URL` (or `ES_URL`
as fallback) pointing at a reachable OpenSearch cluster/index
(`SEARCH_V2_INDEX_NAME`, default `products-search-v2`), and network access
to download the embedding model on first use (or a warm HuggingFace cache).
ShopBot itself never connects to MongoDB — that's exclusively the search
repo's concern (§4).

**Indexing (full / incremental / sync)**: these commands live in the
**search repo**, not here — ShopBot is a pure consumer of their output.
From the search repo:
```
python -m search_v2.indexing.index_v2 all           # full rebuild
python -m search_v2.indexing.index_v2 incremental --upsert-ids ID [ID ...] --delete-ids ID [ID ...]
python -m search_v2.indexing.index_v2 sync           # catch-up diff, both directions
```
See `search/search_v2/README.md` §5 for the full command reference.

**Developer CLI** (`dev_search_cli.py`, this repo's root) — an interactive
REPL exercising the *real* production routing (`_search_engine()` from
`unified_search.py`, so it always matches what `/rs/v1/search` actually
does):
```
python dev_search_cli.py
Search > greek yogurt size:5
Search > protein bars brand:getmymettle
```
Prints, per result: name, id, brand, price, the V2 ranking `score`, and
`flean_score` as the real user-facing `X/10` value (via
`transform_to_product_card()`, not the raw stored percentile).

**Debugging tools** (this repo's root, temporary/diagnostic — not imported
by production code):
- `debug_product_intent.py "query"` — traces Product Intent Identification end-to-end for one or more queries: resolved product type/confidence/tier, Fresh Produce ids if any, the actual built OpenSearch query bodies, hit counts before/after cascading relaxation, and the top results with their own indexed `product_type`/category fields.
- `debug_flean_ranking.py "query"` — runs the real `process_search_request()`/`hybrid_search()`/`apply_business_ranking()` and prints a rank-before/rank-after table so Flean/business-ranking reordering is directly observable rather than inferred from the formula.

---

## 7. Search V1 compatibility

**How they coexist**: routing only, decided per-request by
`unified_search.py`'s `_search_engine()` (reads `SEARCH_ENGINE`) — see §1's
table. No other route can reach V2 at all today.

**Shared components**:
- The **output shape contract**: `gateway.py`'s `_to_v1_product()` deliberately mirrors V1's own `_transform_results()` dict shape (same field names — `id`, `name`, `brand`, `price`, `nutritional_breakdown`, `flean_score`, `review_stats`, etc.) precisely so that everything downstream of either engine — `transform_to_product_card()`, in-stock derivation, PDP CTA resolution, all in `unified_search.py`/`es_products.py` — works identically regardless of which engine produced the raw list.
- The **Flask app/blueprint wiring** — both routes are registered the same way, under the same `/rs` prefix, in `shopping_bot/__init__.py`.
- `indexing_es_client.py` (this repo's root) — but only as a *convention*, not a runtime dependency between the two: V2's `search_v2/retrieval/opensearch_client.py` reuses it for connection/auth signing; V1's `es_products.py` has its own, separate, independent connection code and does not use it.

**Independent components**: everything else. V1 and V2 query **different
OpenSearch/Elasticsearch indices** entirely (`ELASTIC_INDEX`, default
`products_master`, vs. `SEARCH_V2_INDEX_NAME`, default `products-search-v2`)
via **different client connection code**, with **entirely separate**
query-building, ranking, and query-understanding logic. There is no shared
retrieval or ranking code path between them.

---

## 8. Directory walkthrough

```
search_v2/
├── config/
│   └── settings.py            SearchV2Settings — every V2 feature flag/tunable,
│                               each overridable via its own env var
├── embedding/
│   ├── embedding_service.py    Loads whichever model SETTINGS.EMBEDDING_MODEL_KEY
│   │                           names; preload() supports the gunicorn-preload
│   │                           deployment model (§1)
│   └── model_registry.py       Candidate embedding models + metadata
├── query_processing/           Deterministic query understanding — no LLM
│   ├── query_pipeline.py       Orchestrates every step in §2 into one SearchRequest
│   ├── text_normalization.py   Lowercase/punctuation/whitespace normalization
│   ├── typo_correction.py      damerau_levenshtein-based VocabularyCorrector
│   ├── vocabulary_builder.py   load_vocabulary() / VOCABULARY_PATH — reads
│   │                           the artifact described in §3, does not generate it
│   ├── nl_filter_extractor.py  Regex extraction of price/dietary/macro filters
│   ├── product_intent_extractor.py
│   │                           ProductIntentExtractor — reads product_type_lexicon.json;
│   │                           checks Fresh Produce first (see canonical_produce.py)
│   └── canonical_produce.py    Fresh Produce Identification — union-find family
│                               grouping over produce_synonyms.json, plus the
│                               exact/fuzzy alias-match entry point
├── retrieval/
│   ├── opensearch_client.py    Thin client wrapper (reuses indexing_es_client.py)
│   ├── lexical_query_builder.py   BM25 + phrase/prefix/fuzzy dis_max query
│   ├── semantic_query_builder.py  kNN query against text_vector
│   ├── hybrid_search_orchestrator.py
│   │                           hybrid_search() — the single retrieval entry point;
│   │                           fusion + cascading relaxation live here
│   ├── fusion.py                RRF (default) / weighted-sum fusion — pure math
│   ├── filters.py               SearchFilters + build_filter_clauses() — the
│   │                           ONE place any filter dimension becomes an ES clause
│   ├── hybrid_query_builder.py   OpenSearch-native "hybrid" query (alt. fusion strategy)
│   ├── aggregations.py          Brand aggregation/suggestion queries
│   └── sorting.py               Sort clause builder
├── ranking/
│   └── business_ranking.py      apply_business_ranking() — Flean/nutrition-aware
│                               bounded multiplier + two-tier exact-match sort
└── __init__.py

search_gateway/
└── gateway.py                   SearchGateway — the single class every route
                                  goes through to reach V2; _build_search() wires
                                  every piece above together once per process
```

Notably **absent** from this repo (search-repo-only, by design): `indexing/`,
`synonyms/`, `playground/`, `benchmarking/`, `tools/`. ShopBot is a pure
runtime consumer of what those produce — it has no indexing pipeline of its
own. This repo's dev/debug tooling (`dev_search_cli.py`,
`debug_product_intent.py`, `debug_flean_ranking.py`) lives at the repo root
instead, not inside `search_v2/`.

---

## 9. Operational notes

**Adding a new *generated* runtime artifact** (mirroring vocabulary.json /
product_type_lexicon.json): the generator belongs in the search repo
(indexing-side); the loader belongs in this repo's `query_processing/` (or
wherever it's consumed), following the existing `load_X(path=DEFAULT_PATH)`
→ empty-safe-default pattern; wire it into `gateway.py`'s `_build_search()`
inside its own `try/except` block so its failure can't take down unrelated
features; add its S3 env vars (bucket/key) on the search-repo side if it
should be published (see §5); update both this README and the search repo's.

**Adding a new *manually maintained* artifact** (mirroring
produce_synonyms.json): no generator, no S3 key, no regeneration trigger.
Just a loader here, following the same safe-empty-default pattern, and clear
documentation that it's hand-edited — see `canonical_produce.py`'s own
module docstring for the reasoning to reuse.

**Updating vocabulary.json / product_type_lexicon.json**: run `sync` mode
from the search repo (cheapest — diffs and republishes only what changed);
`all` for a full rebuild. Either copies the fresh files into this repo
locally if `SEARCH_V2_SHOPBOT_REPO_ROOT` is configured there.

**Updating produce_synonyms.json**: edit
`query_processing/produce_synonyms.json` directly, in this repo. No build
step, no indexing run, no restart-triggering regeneration — just restart
(or wait for the next `_build_search()` call) to pick up the change.

**Rebuilding runtime artifacts locally without touching the index**: not
directly supported as a standalone ShopBot-side command — this is inherent
to the search repo owning generation; `sync` mode there is the closest
equivalent (it rebuilds+republishes the artifacts every time it runs,
regardless of whether the index diff itself finds anything to add/remove).

**Common debugging workflow**: for "why did this query resolve this way,"
start with `debug_product_intent.py "the query"` — it traces the exact
pipeline stage (typo correction → Fresh Produce → statistical lexicon →
filters → built query body → real hit counts) and will tell you, with real
cluster data, whether the issue is in query understanding or in retrieval.
For "why did this ranking order come out this way," use
`debug_flean_ranking.py` instead. Both hit your real configured cluster —
there's no mocking layer to second-guess.

**Developer tips**:
- `SearchFilters` (`retrieval/filters.py`) is the single source of truth for every filter dimension — if you're adding a new filter, it goes there, not into a query builder directly.
- Every `ENABLE_*` flag in `config/settings.py` is there so you can isolate one feature at a time when debugging (`ENABLE_PRODUCT_INTENT=false`, `ENABLE_BUSINESS_RANKING=false`, etc.) rather than reasoning about the whole pipeline at once.
- A missing/corrupt artifact is designed to degrade one feature, never crash the gateway — if something's silently not working, check the gateway startup logs for `"failed to build ... — ... disabled"` first.

---

## Architectural inconsistencies / gaps found while writing this

- `SearchGateway`'s own class docstring claims to be a "drop-in replacement for `ElasticsearchProductsFetcher.search()` on the `/rs/api/v1/products/search` route" — verified today that route (`product_search.py`) only ever calls `get_es_fetcher()`. Either the comment is stale or that route was meant to be migrated and wasn't.
- ShopBot's production path for keeping `vocabulary.json`/`product_type_lexicon.json` current has no automation today — it relies on the same local-copy mechanism local dev uses, gated by an env var (`SEARCH_V2_SHOPBOT_REPO_ROOT`) set in a *different repo*. The S3 publish step (§5) is a real step toward closing this gap, but the consumer half doesn't exist yet.
- No test suite exists in this repo for `search_v2/` (unlike the search repo, which has 300+ tests) — verification throughout this project has relied on the debug CLIs against a live cluster rather than unit tests.
