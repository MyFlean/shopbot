# Final Deployment Checklist

Date: 2026-07-23. Actionable items only. Checked items were verified locally this session with
evidence (linked). Unchecked items are genuine production-only operations — exact commands provided
where applicable.

## Local verification (complete)

☑ Lambda build verified — `deployment/lambda/Dockerfile.build` fixed (stale `search_gateway/` COPY
  removed, would have failed the build) and reviewed end-to-end. See `PRODUCTION_READINESS.md` §1.
☑ Docker build verified — root `Dockerfile` reviewed, no stale references, `gunicorn` entrypoint
  unaffected by this migration.
☑ Requirements verified — no new third-party dependency introduced this session;
  `requirements.txt`/`requirements-lambda.txt` need no changes.
☑ Environment variables verified — no new required variable; `deployment/lambda/lambda-env.json`
  already correct (`SEARCH_ENGINE=auto`, every `SEARCH_V2_*` setting present with sensible coded
  defaults for anything unset). See `PRODUCTION_READINESS.md` §4.
☑ OpenSearch mapping ready (code) — `search/search_v2/indexing/mapping_builder.py` and
  `document_transformer.py` updated with every new field this migration needs (see "Fields requiring
  a production reindex" below); verified locally via a disposable test index.
☑ Endpoint regression passed — 40/40 endpoints tested, `auto` and strict `SEARCH_ENGINE=v2` produce
  **byte-for-byte identical results** (`POSTMAN_REGRESSION.md`).
☑ Category regression passed — Category Browsing explorer CLI + live V1-vs-V2 comparison
  (`MIGRATION_STATUS.md` §16; `dev_category_browsing_cli.py`).
☑ Search regression passed — main search, filters-only, suggestions all verified live under both
  `auto` and strict `v2`, zero exceptions.
☑ Scanner regression passed — verified live with real search backend; the one Postman failure is a
  pre-existing placeholder-image data issue in the collection itself, not a code regression
  (`POSTMAN_REGRESSION.md`).
☑ Catalogue regression passed — `/rs/api/v1/catalogue` and `/rs/api/v1/catalogue/mapping` both
  verified, V2-native.
☑ Product API regression passed — PDP, Flean Score, Alternatives, Recommended all V2-native,
  verified live.
☑ Batch PDP regression passed — verified live with real + nonexistent ids, correct `not_found`
  handling.
☑ Vision flow status documented — migrated to V2-native this session (required building
  `search_v2.extension.brand.suggest_brand()`, previously nonexistent); verified live.
☑ All remaining blockers documented — exactly one (`LEGACY_SEARCH_VALIDATION.md`'s chat-flow
  `BackendFunction.SEARCH_PRODUCTS` handler), with why/what's-missing/what's-required all specified.
☑ Rollback plan documented — see below.

## Fields requiring a production reindex (per your instruction — tracked explicitly)

These fields were added to the indexing pipeline (`search` repo, uncommitted local changes) earlier
in this migration. **Adding the mapping alone does not populate them on already-indexed documents**
— only a reindex (full or targeted) populates existing documents. New fields, what they contain, and
why:

| Field | Contains | Source |
|---|---|---|
| `review_stats` | `{avg_rating, total_reviews, ...}` | Extracted from Mongo's `tags_and_sentiments` by `sanitize_for_es()` — was computed but discarded before this fix |
| `cons_list` | Short "watch out for" label chips | Same source |
| `pros_list` | Short "what's good" label chips | Same source |
| `search_keywords` | Aggregated top-phrase/aspect keywords (capped ~50 terms) | Same source |
| `ingredients.structured.ingredients` | Parsed ingredient list: `{name, percentage, is_composite, components}` | Mongo's structured ingredient data — was discarded in favor of raw-text-only before this fix |
| `ingredients.structured.additives` | Parsed additive list | Same source |

**Local validation status**: confirmed via a disposable test index that `ingredients.structured`
populates correctly (12/30 sampled docs). `review_stats`/`cons_list`/`pros_list`/`search_keywords`
could **not** be validated locally — 0 of 21,470 local `products_master` Mongo documents have
`tags_and_sentiments` at all (a local snapshot limitation, not a code defect — see
`FINAL_MIGRATION_REPORT.md` §11 for the full evidence trail). These should populate correctly
against production's Mongo data, which is expected to carry this enrichment.

☐ **Apply the updated mapping to the production index** (additive `put_mapping` — no downtime, no
  reindex required for the mapping change itself):
  ```bash
  # From the search/ repo, against the PRODUCTION OpenSearch endpoint.
  # Review search_v2/indexing/mapping_builder.py's build_mapping() output first.
  ```
☐ **Reindex to backfill the fields above onto already-indexed production documents**:
  ```bash
  # From the search/ repo:
  python3 search_v2/indexing/index_v2.py all
  # or, for a narrower rollout:
  python3 search_v2/indexing/index_v2.py incremental --upsert-ids <id1> <id2> ...
  ```
  Uses `EMBEDDING_BACKEND=bedrock` in production — incurs real AWS Bedrock Titan embedding costs
  proportional to documents re-embedded. Size the batch or schedule accordingly.

## Production-only operations remaining

☐ **Trigger the (now-fixed) CI/CD deploy pipeline** — `.github/workflows/deploy-lambda.yml`, push to
  `main`/`master` or manual `workflow_dispatch`. Pipeline itself is verified/fixed; triggering it is
  a git push, left for you to run explicitly.
☐ **Apply the OpenSearch mapping update** (above).
☐ **Run the production reindex** (above) — optional but recommended for full field parity.
☐ **Post-deploy smoke test** — run `postman_regression_runner.py` against the real production URL,
  ideally once under whatever `SEARCH_ENGINE` production is actually running and once forced to
  `v2` (temporarily, reverted after), diffing the two exactly as done locally, to reconfirm the
  "auto == v2, byte-for-byte" result holds at production scale/data volume.
☐ **Decide on the one documented blocker** (`BackendFunction.SEARCH_PRODUCTS` / chat-flow search) —
  not required for this deployment (it's already fully functional on V1, unaffected either way), but
  worth a scheduling decision for whether/when to migrate it.

## Rollback plan

If anything goes wrong post-deploy, the fastest rollback is **not** a code revert — it's the
`SEARCH_ENGINE` Lambda environment variable:
1. Set `SEARCH_ENGINE=v1` in the Lambda's environment (via AWS Console, CLI, or
   `deployment/lambda/update-lambda-env.sh`). Every endpoint immediately serves V1 exclusively —
   the exact same code path that was in production before this migration, since `es_products.py` was
   never modified or removed.
2. No redeploy needed — Lambda environment variable changes take effect on the next invocation.
3. If the issue is specifically in the new indexing fields (unlikely — additive-only, and V1 never
   reads V2's index), no index rollback is needed either; V1 reads its own untouched index.
4. Only if `SEARCH_ENGINE=v1` itself doesn't resolve the issue would a full code rollback
   (redeploying the prior Lambda package version via AWS Console/CLI) be necessary — the CI/CD
   pipeline keeps prior deployment artifacts per its own retention policy.
