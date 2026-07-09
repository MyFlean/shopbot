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
- **Everything else** (e.g. gunicorn): the gateway is built and `warmup()`-ed *eagerly* at app-create time. Weight preloading (`get_embedding_service(...).preload()`, so forked gunicorn workers inherit an already-loaded model via copy-on-write) only runs when `EMBEDDING_BACKEND=local` — the production default, `EMBEDDING_BACKEND=bedrock` (Amazon Titan Text Embeddings V2, see §1a), has no local weights to preload; `BedrockTitanEmbeddingService.preload()` is a no-op. Gateway/embedding init failure at this stage is caught and logged, not fatal — it just means the first real request pays the init cost instead.

### 1a. Embedding backend: Bedrock Titan (production default)

Query-time embeddings for the semantic/kNN half of hybrid retrieval come from
**Amazon Bedrock Titan Text Embeddings V2** (`amazon.titan-embed-text-v2:0`,
512 dimensions) via `embedding/bedrock_embedding_service.py` — a plain HTTPS
call authenticated with a bearer token, not boto3/IAM. This is a completely
independent service from the search repo's own Bedrock embedding client used
at *indexing* time (`bedrock_embedding_service.py` there generates the stored
`text_vector` for every document); the two share only a contract — same
model id, same 512 dimensions, same OpenSearch mapping — never code or a
runtime dependency.

**Selection** (`config/settings.py`): `EMBEDDING_BACKEND` — `"bedrock"`
(default) or `"local"` (sentence-transformers, dev-only, requires manually
installing `torch`/`sentence-transformers` — removed from `requirements.txt`
by this migration since nothing in the reachable production path needs
them). `get_embedding_service()` is the single factory both backends go
through; callers must invoke it with **no explicit `model_key`** to get the
configured backend — passing one always forces the local path regardless of
`EMBEDDING_BACKEND` (used only by the gunicorn-preload step above).

**Failure behavior is deliberately asymmetric between indexing and runtime**:
a batch-embedding failure at *index time* (search repo) fails loudly and
aborts just the affected batch, so a bad document never gets embedded
"successfully" as a zero-vector. A *query-time* embedding failure here
raises `BedrockEmbeddingError`, which `unified_search.py`'s existing V1
fallback already catches (`SEARCH_ENGINE=auto`) — a Bedrock outage degrades
to V1 lexical search, it does not 500.

**Required environment variables** (this repo's `.env`): `AWS_BEARER_TOKEN_BEDROCK`
(the *same* variable `shopping_bot/config.py` already reads for the chat
LLM — one token covers both), `BEDROCK_REGION` (default `ap-south-1`),
`BEDROCK_EMBEDDING_MODEL_ID` (default `amazon.titan-embed-text-v2:0`),
`SEARCH_V2_EMBEDDING_DIM` (must be `512` — must match whatever dimension the
index was actually built with).

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
  - **Production**: the search repo publishes both artifacts to S3 as part of the same indexing run (see §5), and this repo's gateway fetches them over HTTPS at startup (`SEARCH_V2_VOCAB_URL` / `SEARCH_V2_PRODUCT_TYPE_LEXICON_URL`) — no manual deployment step required once those URLs are configured. The `SEARCH_V2_SHOPBOT_REPO_ROOT` local-copy mechanism above is a local-dev convenience only; it plays no role in production.
- **`produce_synonyms.json`** never comes from the search repo at all — see §3.

---

## 5. Amazon S3 publishing (search-repo side) and ShopBot's runtime fetch

The search repo's indexer publishes `vocabulary.json` and
`product_type_lexicon.json` to S3 as part of the same `all`/`incremental`/
`sync` runs that regenerate them. ShopBot then fetches both, once, at
gateway startup. This section documents the full round trip from ShopBot's
perspective — for the search-repo-side implementation, see
`search/search_v2/README.md`.

**Full flow, in this exact order**:

```
generate artifact (MongoDB scan, search repo)
        ↓
write locally (search repo's own query_processing/ directory)
        ↓
upload to S3 (search repo, boto3)
        ↓
copy into ShopBot's local checkout (SEARCH_V2_SHOPBOT_REPO_ROOT — local dev only, optional, in addition to S3)
        ↓
ShopBot gateway startup: HTTPS GET from SEARCH_V2_VOCAB_URL / SEARCH_V2_PRODUCT_TYPE_LEXICON_URL
        ↓
on success: atomically overwrite this repo's local query_processing/*.json
        ↓
load_vocabulary() / load_product_type_lexicon() read whatever is now on disk
```

**Search-repo side environment variables** (set in the search repo, not here):

| Variable | Purpose |
|---|---|
| `SEARCH_V2_ARTIFACTS_S3_BUCKET` | Shared S3 bucket for both artifacts. Blank = publishing skipped, indexing unaffected |
| `SEARCH_V2_VOCAB_S3_KEY` | Object key for `vocabulary.json` |
| `SEARCH_V2_PRODUCT_TYPE_LEXICON_S3_KEY` | Object key for `product_type_lexicon.json` |
| `SEARCH_V2_S3_FAIL_OPEN` | `true` = warn and continue indexing on upload failure; `false` (default) = abort |

**Fail-open vs. fail-closed** (search-repo upload): fail-closed (default)
treats a publish failure exactly like a generation failure — it aborts the
indexing run, so the generated and published artifact can never silently
diverge. Fail-open prints a warning and continues, useful for local
indexing runs without S3 credentials configured.

**ShopBot-side environment variables** (this repo's `.env`):

| Variable | Purpose |
|---|---|
| `SEARCH_V2_VOCAB_URL` | HTTPS URL `vocabulary.json` is fetched from at gateway startup. Blank (default) = fetch skipped entirely |
| `SEARCH_V2_PRODUCT_TYPE_LEXICON_URL` | Same, for `product_type_lexicon.json` |
| `SEARCH_V2_ARTIFACT_FETCH_TIMEOUT_SEC` | Fetch timeout in seconds (default `2.0`) |

**This is a plain `requests.get()`, not the S3 SDK** — whatever URL you put
here must serve the object over HTTPS directly (a public-read S3 object
URL, a presigned URL, or something like CloudFront/API Gateway in front of
the bucket). `gateway.py`'s `_fetch_and_overwrite_artifact()` never raises:
an empty URL, network error, timeout, non-2xx status, or a payload that
fails validation are all treated identically — log a warning and leave the
existing local file untouched. `load_vocabulary()`/`load_product_type_lexicon()`
never know or care whether the file they're reading was just fetched or was
already there. This means gateway startup is never blocked by S3/the fetch
URL being unavailable, and the local files checked into (or previously
fetched into) this repo are always the fallback of last resort.

---

## 6. Running Search V2

**Prerequisites** (this repo): a `.env` with `SEARCH_V2_ES_URL` (or `ES_URL`
as fallback) pointing at a reachable OpenSearch cluster/index
(`SEARCH_V2_INDEX_NAME`, default `products-search-v2`), and a valid
`AWS_BEARER_TOKEN_BEDROCK` with Bedrock Titan Text Embeddings V2 access in
`BEDROCK_REGION` (see §1a) — semantic/hybrid search raises without it,
degrading to V1 lexical only if `SEARCH_ENGINE=auto`. ShopBot itself never
connects to MongoDB — that's exclusively the search repo's concern (§4).

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
`transform_to_product_card()`, not the raw stored percentile). This is the
primary tool for tracing query understanding and ranking against a real
cluster — the banner shows engine/index/OpenSearch connection status, and
the `[V2 exception — falling back to V1: ...]` message (auto mode) surfaces
the exact underlying error whenever V2 doesn't handle a query.

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
│   ├── embedding_service.py    get_embedding_service() factory — routes to
│   │                           Bedrock (default) or, given an explicit model_key,
│   │                           the local sentence-transformers path (see §1a)
│   ├── bedrock_embedding_service.py
│   │                           BedrockTitanEmbeddingService — query-time Titan
│   │                           embeddings via a plain bearer-token HTTPS call (§1a)
│   └── model_registry.py       Candidate local embedding models + metadata
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
own. This repo's dev tooling (`dev_search_cli.py`) lives at the repo root
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
`dev_search_cli.py` (§6) hits your real configured cluster with no mocking
layer — the printed engine label and any `[V2 exception — falling back to
V1: ...]` message will tell you whether the issue is in query understanding,
retrieval, or an upstream failure (e.g. a Bedrock auth error). For deeper
tracing of one specific query's product-type resolution or ranking, a
one-off script directly instantiating `ProductIntentExtractor` /
`process_search_request()` (as done throughout this session's
investigation) is the simplest way to inspect intermediate state.

**Developer tips**:
- `SearchFilters` (`retrieval/filters.py`) is the single source of truth for every filter dimension — if you're adding a new filter, it goes there, not into a query builder directly.
- Every `ENABLE_*` flag in `config/settings.py` is there so you can isolate one feature at a time when debugging (`ENABLE_PRODUCT_INTENT=false`, `ENABLE_BUSINESS_RANKING=false`, etc.) rather than reasoning about the whole pipeline at once.
- A missing/corrupt artifact is designed to degrade one feature, never crash the gateway — if something's silently not working, check the gateway startup logs for `"failed to build ... — ... disabled"` first.

---

## 10. Advanced Search V2 Configuration

Everything below is a `config/settings.py` code default — deliberately **not**
exposed in `env.production.template` (that file is deployment/infrastructure
config only: cluster URLs, credentials, index names). Every field here is
still overridable via its own env var listed below if you have a specific,
deliberate reason to tune it — just set it directly in your shell/task
definition rather than adding it to the shared template.

**Top-level capability switches** (all default `true` unless noted) — isolate
one feature at a time when debugging:
`ENABLE_LEXICAL`, `ENABLE_SEMANTIC`, `ENABLE_VECTOR_SEARCH`, `ENABLE_HYBRID`,
`ENABLE_AUTOCOMPLETE`, `ENABLE_SYNONYMS`, `ENABLE_FUZZY`,
`ENABLE_TYPO_CORRECTION`, `ENABLE_ROMAN_HINDI_NORMALIZATION`,
`ENABLE_QUERY_EXPANSION`, `ENABLE_BUSINESS_RANKING`, `ENABLE_RERANKER`,
`ENABLE_DERIVATIVE_DEMOTION`, `SEARCH_V2_ENABLE_NL_FILTERS`.

**Leaf-category boost**: `SEARCH_V2_ENABLE_LEAF_CATEGORY_BOOST` (default
`true`), `SEARCH_V2_LEAF_CATEGORY_COMMODITY_BOOST` (default `8.0`) — exact
query-vs-`leaf_category` match boost.

**Cluster internals**: `SEARCH_V2_PIPELINE_NAME` (default
`search-v2-hybrid-pipeline`) — OpenSearch hybrid pipeline name.

**Semantic/fusion**: `SEARCH_V2_SEMANTIC_MIN_SCORE` (default `0.0` = off) —
minimum raw kNN score to admit a hit into fusion. `SEARCH_V2_FUSION_STRATEGY`
(default `rrf`; also `weighted` | `native_hybrid`), `SEARCH_V2_FUSION_WEIGHTS`
(default `[0.6, 0.4]`, lexical/semantic — only used by `weighted`),
`SEARCH_V2_RRF_RANK_CONSTANT` (default `60`).

**Retrieval window sizes**: `SEARCH_V2_RETRIEVAL_K` (default `75`),
`SEARCH_V2_RERANK_TOP_N` (default `40`), `SEARCH_V2_DEFAULT_RESULT_SIZE`
(default `10`).

**Fuzzy/typo**: `SEARCH_V2_FUZZINESS` (default `AUTO`),
`SEARCH_V2_TYPO_MAX_EDIT_DISTANCE` (default `2`).

**Business ranking** (see `ranking/business_ranking.py` — do not widen the
multiplier bounds without re-deriving them the same way, see that file's
comments on the incident that set them): `SEARCH_V2_BUSINESS_MIN_MULTIPLIER`
(default `0.85`), `SEARCH_V2_BUSINESS_MAX_MULTIPLIER` (default `1.15`),
`SEARCH_V2_BUSINESS_RULE_WEIGHTS` (JSON object, default
`{"ratings_rule":0.0,"review_count_rule":0.0,"stock_rule":0.0}`),
`SEARCH_V2_DERIVATIVE_DEMOTION_FACTOR` (default `0.85` — e.g. apple vs. apple
juice), `SEARCH_V2_CATEGORY_PRIORITY_BOOSTS` (JSON object, default `{}`).

**NL filter macro-constraint thresholds** (per 100g):
`SEARCH_V2_MACRO_HIGH_PROTEIN_G` (`15.0`), `SEARCH_V2_MACRO_LOW_SUGAR_G`
(`5.0`), `SEARCH_V2_MACRO_LOW_FAT_G` (`3.0`), `SEARCH_V2_MACRO_LOW_CAL_KCAL`
(`100.0`), `SEARCH_V2_MACRO_HIGH_FIBER_G` (`6.0`),
`SEARCH_V2_MACRO_LOW_SODIUM_MG` (`140.0`), `SEARCH_V2_MACRO_LOW_CARBS_G`
(`15.0`).

**Product Intent Identification** (see
`query_processing/product_intent_extractor.py`): `SEARCH_V2_ENABLE_PRODUCT_INTENT`
(default `true`), `SEARCH_V2_PRODUCT_INTENT_HIGH_CONFIDENCE` (default
`0.55`), `SEARCH_V2_PRODUCT_INTENT_LOW_CONFIDENCE` (default `0.25`),
`SEARCH_V2_ENABLE_PRODUCT_INTENT_RELAXATION` (default `true` — retry once
with the product-type filter relaxed on zero results),
`SEARCH_V2_STRICT_ZERO_RESULTS` (default `false` — business-policy override:
`true` makes a zero-result product-type-gated query return empty instead of
retrying, see `hybrid_search_orchestrator.py`), `SEARCH_V2_PRODUCT_INTENT_MAX_POOL_SIZE`
(default `300` — retrieval pool ceiling when a high-confidence product-type
filter is active).

---

## Known gaps

- Every route except `unified_search.py` (§1) still uses `ElasticsearchProductsFetcher` directly rather than `SearchGateway` — this is the current, intentional migration boundary, not an oversight, but it means V1 remains the only engine for most product-facing routes until (if) they're migrated too.
- `search_v2/tests/` covers the query-understanding layer (typo correction, Product Intent Identification, category fallback, the Bedrock embedding service) with 37 unit tests; retrieval/ranking (`lexical_query_builder.py`, `hybrid_search_orchestrator.py`, `business_ranking.py`) has no unit tests yet and is verified against a live cluster via `dev_search_cli.py` instead.
