# Production Validation — Phases 5–8

Date: 2026-07-24. Performed against real production infrastructure after you switched
`.env` to production values (`ES_URL` = real AWS OpenSearch domain, `SEARCH_V2_INDEX_NAME=products-search-v3`,
real Bedrock region/model). Per your instruction: **production was treated as the thing being
audited, not as ground truth.** No Search V2 code was changed based on anything found in this
section — every discrepancy below is classified, not fixed.

Note on scope: only the OpenSearch/Bedrock endpoints were switched to production; Redis remained the
local Docker instance (this is consistent with "production is for validation only" — the app process
itself is still running locally via `python run.py`, not inside an actual Lambda/ECS runtime).

---

## Phase 5 — Production Environment Validation

### 5.1 Connectivity — confirmed real, not assumed

- **OpenSearch**: `ES_URL=https://search-flean-products-nzem6mojpxpurbgit2z52mwiru.ap-south-1.es.amazonaws.com`.
  This hostname pattern is `*.es.amazonaws.com` — **a classic managed Amazon OpenSearch Service
  domain, not OpenSearch Serverless (AOSS)**, which would be `*.aoss.amazonaws.com`. This corrects a
  speculative statement in the prior audit (`PRODUCTION_READINESS_DEPLOYMENT_AUDIT.md` Finding 2),
  which inferred AOSS from `ecs-task-definition.json`'s `AOSS_ENABLED=true`/`ES_USE_IAM=true` flags
  alone. Now confirmed directly: `ElasticsearchProductsFetcher.__init__()`'s
  `_is_aws_opensearch_url()` auto-detected the `.es.amazonaws.com` pattern and enabled IAM/SigV4
  auth automatically (`use_iam_auth=True`), independent of those env flags. Verified live:
  `OpenSearchClient._get_client()` successfully authenticated and returned real search results.
- **Bedrock**: called `EmbeddingService.embed_query()` directly (not through a search request) and
  confirmed a genuine round-trip to `amazon.titan-embed-text-v2:0` in `ap-south-1`: real 512-dimension
  vector returned, ~328ms latency, plausible float values (not a stub/zero-vector). This is a real,
  live Bedrock call, not a cached or mocked result.

### 5.2 Index name, mappings, document counts

- **Production has at least 3 candidate product-search indices live on the same cluster right now**:
  `products-search-v3` (current `SEARCH_V2_INDEX_NAME`), `products-search-v2` (previous version, not
  currently referenced by `.env`), and `products_master` (V1's legacy index, referenced by
  `ELASTIC_INDEX`). All three exist, are healthy, and are independently queryable.
- **Root document count for `products-search-v3`: 6,471** (`_count` API with `match_all`, and
  confirmed by matching the app's own `/rs/v1/search` result totals). `_cat/indices` and
  `indices.stats()` both report `docs.count: 14280` for the same index — **this is not a data
  problem**: `category_hierarchies` is mapped as `nested`, and nested objects are stored as separate
  hidden Lucene documents, inflating the shard-level Lucene doc count relative to the real
  root-document count. Confirmed by comparing `_count` (correct, root-doc-only) against
  `indices.stats()` (includes nested children) on the same index — the ~2.2x ratio is consistent
  with this explanation, not indicative of duplication or corruption.
- **Embedding coverage: 100%** — 6,471/6,471 documents have a populated `text_vector` field, mapped
  as `knn_vector`, dimension 512, `cosinesimil`/`hnsw` — matching `SEARCH_V2_EMBEDDING_DIM=512` and
  `BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0` exactly. `embedding_model_key`
  aggregation confirms all 6,471 docs were embedded with `amazon.titan-embed-text-v2:0` — no mixed-
  model contamination.
- **Searchable/filterable field mapping, spot-checked against every field this engagement's
  compatibility fixes touched**: `brand_phonetic.keyword`, `name_phonetic.keyword`,
  `category_paths` (keyword), `category_group` (keyword), `name_suggest` (completion, with
  `category_group` context) — all present and correctly typed. No mapping drift found for any
  previously-fixed field.

### 5.3 Finding — variant-collapse data is entirely absent from every current production index

**This is the most significant finding of this phase.**

- `products-search-v3`'s mapping includes `variants` and `parent_id` fields (37 fields → 39 fields
  vs. `products-search-v2`, which lacks both entirely from its mapping) — so `v3` is a genuinely
  newer schema, not just a duplicate reindex of `v2`.
- **However: `0` out of `6,471` documents in `products-search-v3` have `variants` populated**
  (`exists` query on `variants` → `count: 0`). Same for `parent_id`.
- This means: **the variant-collapse feature — verified working correctly in local testing this
  entire engagement (a 7-variant family collapsing correctly) — cannot function in production right
  now**, because there is no variant-grouping data in the index for it to operate on. This is not a
  hypothetical: `search_v2/ranking/business_ranking.py`, `search_v2/extension/product/card.py`,
  `search_v2/retrieval/hybrid_query_builder.py`, `search_v2/retrieval/lexical_query_builder.py`, and
  `search_v2/query_processing/query_pipeline.py` all reference `variants`/`parent_id` — this is live,
  wired-in code waiting on data that isn't there.
- **Classification: production indexing/data issue, NOT a Search V2 code issue.** The retrieval and
  card-transform code correctly reads and uses these fields when present (proven by this
  engagement's local testing against a properly-populated local index). The absence is upstream —
  either the production indexing pipeline run that built `products-search-v3` predates variant-
  grouping logic being added to the indexer, or the production MongoDB source data itself lacks
  whatever raw field the indexer needs to detect variant families. **Requires re-running the
  indexing pipeline (the sibling `search` repo) against production Mongo data with variant-detection
  enabled — not a Search V2 application code change.**
- Not fixed, not worked around, per your explicit instruction to classify rather than patch around
  production data gaps.

### 5.4 Deployment-configuration verification gap (unresolved — needs your confirmation)

- `ecs-task-definition.json`, as checked into this repository, does not set `SEARCH_V2_INDEX_NAME`
  at all. If this file accurately reflects what's currently deployed to ECS, the running production
  application would fall back to `search_v2/config/settings.py`'s code default,
  `products-search-v2` — **not** `products-search-v3`, which is what your `.env` switch just told me
  is the intended current index.
- Since `products-search-v2` also lacks the `variants`/`parent_id` fields in its mapping entirely
  (not just unpopulated — genuinely absent from the schema), this would make the variant-collapse
  gap even more structural if that's the index actually in use.
- **This cannot be resolved from the local repository alone** — `ecs-task-definition.json` in this
  repo may be stale relative to whatever task definition revision is actually registered in AWS.
  This is a **deployment/configuration verification item for you to confirm directly against AWS**
  (`aws ecs describe-task-definition --task-definition shopbot` or equivalent), not something I can
  determine by further local investigation.

### 5.5 IAM/runtime-role caveat (explicitly not validated)

This validation authenticated to OpenSearch using **this machine's ambient AWS credentials** (local
AWS CLI/SSO session), not the actual IAM role Lambda or ECS would assume at runtime
(`ecsTaskExecutionRole`/`flean-services-ecs-task-role` per `ecs-task-definition.json`, or whatever
Lambda execution role is configured). **IAM permission behavior specific to those roles — whether
they have the exact OpenSearch/Bedrock permissions needed — is NOT validated by this pass.** If this
machine's ambient credentials happen to be broader than the deployed execution role's actual policy,
a real deployment could still fail on a permissions error this test would never surface.

---

## Phase 6 — Production Smoke Tests

All executed against the real production OpenSearch index (`products-search-v3`) and real Bedrock:

| Test | Result |
|---|---|
| Lexical (`milk`) | Real results, `engine: v2`, dynamic price filter correctly recomputed (`Below Rs 500: 341`, etc. — different bucket boundaries than local, consistent with different underlying price distribution) |
| Semantic (`post workout muscle recovery supplement`) | `total: 71`, real Bedrock embedding call confirmed, protein/recovery-relevant products returned (Ritebite/RiteBite Max Protein chips) |
| Autocomplete (`choc`) | Real completions, `category_group: "f_and_b"` correctly populated (confirms this session's fix works against real production data, not just local) |
| Empty-result / gibberish (`zzxxqqnonexistentproduct999`) | `total: 72` — **reproduces identically to local**, confirming the semantic-floor finding from the prior audit is a genuine Search V2 design behavior, not a local-data artifact |
| Home best-selling | Real ranked products returned |
| PDP | Real product detail response returned for a live production product ID |
| `category_group` distribution across the whole index | 100% `f_and_b`, 0% `personal_care` — **this confirms the personal_care gap is a real production data gap, not a local-only testing limitation** as previously assumed |
| Regression suite | 192 passed, 1 skipped — identical result with production config loaded |
| Server logs | No errors, exceptions, or unexpected warnings during any of the above (only the already-understood `PRODUCT_INDEX_MISMATCH` warning, which only affects the V1-fallback code path, not V2, and `SEARCH_ENGINE=v2` is active) |
| Latency | 240–610ms per search request — no anomalies observed |

---

## Phase 7 — Deployment Readiness

Reviewed `deploy.sh`, `Dockerfile`, `ecs-task-definition.json`, `lambda_handler.py`,
`requirements-lambda.txt` again with production connectivity now confirmed real (not hypothetical):

- **Rollback capability**: `deploy.sh rollback` looks up the previous ACTIVE task definition
  revision and switches the ECS service to it — a real, functioning rollback path, not a stub.
- **No blocker found in the deployment scripts themselves.** The only deployment-adjacent gap is
  §5.4 above (unconfirmed whether the checked-in `ecs-task-definition.json` matches what's actually
  registered in AWS) — this is a verification gap, not a proven blocker.

---

## Phase 8 — Final Report

### All issues found, this pass

| # | Issue | Severity | Classification | Evidence |
|---|---|---|---|---|
| 1 | Variant-collapse data (`variants`/`parent_id`) is entirely unpopulated in the current production index | **High** (real feature gap) | Production indexing/data issue | `0/6471` docs have `variants` populated in `products-search-v3`; field absent from `products-search-v2`'s mapping entirely |
| 2 | Unconfirmed whether ECS's actual deployed config sets `SEARCH_V2_INDEX_NAME=products-search-v3`, vs. defaulting to `products-search-v2` | Medium (verification gap, not proven) | Deployment/configuration verification needed | `ecs-task-definition.json` in-repo has no `SEARCH_V2_INDEX_NAME` key |
| 3 | Hybrid search has no relevance floor on the semantic leg — nonsense queries return non-empty results | Medium | Search V2 code/tuning (confirmed pre-existing, reproduces identically in production) | `SEMANTIC_MIN_SCORE=0.0` default; reproduced against real production index |
| 4 | IAM execution-role permissions not validated (only ambient local credentials tested) | Not yet assessed | Infrastructure/deployment verification needed | Explicit scope limitation of this local-machine test |
| 5 | personal_care search quality/ranking — confirmed no data exists in production at all (not just locally) | Informational, not a defect | Production data (content) gap — out of Search V2's control | `category_group` aggregation: 100% `f_and_b`, 0% `personal_care`, on the real production index |

**No genuine Search V2 code defect was found in this production-validation pass.** Every discrepancy
observed traces to production data/indexing state or configuration verification, not to incorrect
application logic — consistent with your instruction to only recommend a code change if the evidence
clearly proves the code itself is wrong (it doesn't, here).

### Confirmations

- Search V2 behavior (ranking, retrieval, query parsing, semantic matching) is **unchanged** between
  local and production — every capability tested behaves identically in kind (only the underlying
  data/counts differ, as expected for a different index).
- This engagement's compatibility fixes (`in_stock`/`currency`/`macro_tags`/`nutrition`/`scheduled`/
  `category_group`) all verified working correctly against real production data, not just local.
- Every endpoint exercised in this phase (search, suggest, home best-selling, PDP) returned
  structurally and semantically correct data against production.
- Regression suite: 192 passed, 1 skipped, both before and after this phase — no regression
  introduced by connecting to production.

### Explicitly NOT validated (stated plainly, not implied)

- Real Lambda cold-start behavior (this ran as a local Flask process, not inside actual Lambda).
- Real ECS task startup (same reason).
- The actual IAM execution role's permissions (only ambient local credentials were used).
- Whether the checked-in `ecs-task-definition.json` matches what's currently registered in AWS.
- Load/scale behavior under concurrent production traffic.
- Any endpoint not explicitly exercised in the smoke-test table above.

---

## Answers to your explicit questions

1. **Is the repository ready to deploy?** Yes — no code defect blocks deployment.
2. **Is the Lambda ready to deploy?** Likely, with one caveat: Finding 1 (`/rs/flow` → `/flow`,
   already fixed) removed the only Lambda-specific code defect found. IAM role permissions for
   Lambda's actual execution role remain unverified (§5.5).
3. **Is the deployment pipeline ready?** Yes, based on static review of `deploy.sh` — no defect
   found; rollback path exists and is real.
4. **Is the production configuration ready?** **Not fully confirmed** — §5.4 is an open
   verification item only you can close (checking AWS directly for the live task definition's env
   vars).
5. **Is the production index ready?** **No, not for the variant-collapse feature** — §5.3 is a real
   gap requiring a reindex run with variant-detection enabled. Everything else about the index
   (document count, embeddings, mappings, phonetic fields, category fields) is healthy and correct.
6. **Is there any deployment blocker?** No proven blocker; §5.4 is a verification gap, not a
   confirmed blocker.
7. **Is there any runtime blocker?** No.
8. **Is there any production blocker?** **Yes, one: variant-collapse data is absent from every
   current production index (§5.3).** Whether this is release-blocking depends on how central
   variant collapsing is to your launch requirements — that's a product decision, not a technical
   one I can make for you, but the underlying data gap is real and confirmed.
9. **What must be completed before pressing Deploy?**
   - Confirm (via AWS directly) which index ECS/Lambda's actual deployed config points at (§5.4).
   - Decide whether variant-collapse must be populated before launch, or can ship as a known gap
     with a follow-up reindex (§5.3).
   - Optionally verify the actual Lambda/ECS execution role has the OpenSearch/Bedrock permissions
     this local test couldn't check (§5.5).
10. **Would you approve this version for production deployment?** **Conditional GO.** The Search V2
    application code itself is production-ready — no defect was found in it during this pass, and
    everything this engagement fixed continues to work correctly against real production
    infrastructure. The blocker, if any, is entirely on the data/infrastructure side (§5.3, and the
    unresolved §5.4 verification), not the code. I would not block deployment of the *code* on this
    evidence, but I would not consider the *system* (code + current production index) fully ready
    until §5.3 and §5.4 are explicitly resolved by you.
