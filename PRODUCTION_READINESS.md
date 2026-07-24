# Production Readiness — Search V2 Migration

Date: 2026-07-23. Everything completable locally has been done and verified. This document lists
what changed, what was verified, and — separately, clearly marked — the production-only operations
that remain (deployment, index mapping update, reindex) since they cannot be run from this local
environment per the engagement's standing policy (no production/AWS operations performed directly).

## 1. What was found broken and fixed (local, verified)

Deleting `search_gateway/` (see `MIGRATION_STATUS.md` / `FINAL_MIGRATION_REPORT.md` §12) left three
stale references that would have broken the *next* deployment. Found during this review, fixed now:

| File | Problem | Fix |
|---|---|---|
| `deployment/lambda/Dockerfile.build` | `COPY search_gateway/ /build/package/search_gateway/` — copies a directory that no longer exists; would fail every future Lambda build | Line removed; comment updated to explain why (`search_v2/extension/search/core.py` now covers the same logic, already included via the `search_v2/` COPY) |
| `deployment/lambda/build-local.sh` | `cp -r search_gateway "$PACKAGE_DIR/"` — same failure mode for the no-Docker local build path | Line removed |
| `.github/workflows/deploy-lambda.yml` | `paths: [..., 'search_gateway/**', ...]` — a CI trigger glob for a path that no longer exists (harmless, but stale and misleading) | Removed |

**Verified**: `deployment/lambda/Dockerfile.build` no longer references any nonexistent path;
`grep -rl search_gateway` across the repo now only matches two explanatory comments (in
`search_v2/extension/search/core.py` and the Dockerfile itself) and nothing executable.

## 2. A real Lambda cold-start gap, found and fixed

`lambda_handler.py`'s `is_critical_endpoint` check (which endpoints must wait for AWS
Secrets-Manager-sourced `ES_URL`/`ES_API_KEY` before serving) matched `/rs/api/v1/products`
(plural) but **not** `/rs/api/v1/product/<id>` (singular) — meaning PDP, Alternatives, Recommended,
Batch PDP, Flean Score, and Catalogue could all execute before secrets finished loading on a cold
Lambda start, before this migration ever touched these routes (a pre-existing gap, not something
this migration introduced — but it now affects six routes that are freshly V2-native, so worth
closing). Fixed: added `/rs/api/v1/product` (singular), `/rs/api/v1/catalogue`,
`/rs/api/v1/flean-score` to the critical-endpoint list.

## 3. Requirements / dependencies

No new third-party dependency was introduced anywhere in this migration — every new module
(`search_v2/extension/*`, the two dev CLIs, the Postman regression runner) uses only what's already
in `requirements.txt` / `requirements-lambda.txt` (Flask, opensearch-py, boto3/botocore, requests,
python-dotenv) plus the standard library. **No changes needed here.**

## 4. Environment variables

No new environment variable was introduced. Everything this migration's code reads
(`SEARCH_ENGINE`, `SEARCH_V2_*`, `ES_URL`/`ES_API_KEY`, `BEDROCK_*`) was already present in
`deployment/lambda/lambda-env.json` and already has a sensible coded default in
`search_v2/config/settings.py` for anything not explicitly set. **Confirmed, no changes needed.**

One clarification worth recording: this local dev machine's own `.env` sets `SEARCH_ENGINE=v2`
(strict), which is *not* what `lambda-env.json` sets for production (`SEARCH_ENGINE=auto`). This
difference was the root cause of a real test regression caught and fixed during
`V1_FALLBACK_AUDIT.md`'s work — see that document's Method §4. Production's `auto` setting is
correct and unaffected.

## 5. OpenSearch mapping & indexing pipeline (in the `search` repo)

Already changed and locally validated this migration (see `FINAL_MIGRATION_REPORT.md` §1):
`search_v2/indexing/document_transformer.py`'s `ALLOWLIST_INDEX_FIELDS` gained `review_stats`,
`cons_list`, `pros_list`, `search_keywords`, and structured `ingredients.{ingredients,additives}`;
`mapping_builder.py` gained explicit mappings for the new keyword/text fields. Verified via a
disposable local test index — never applied to the shared local index or any production index.

**This is uncommitted, local-only work in the `search` repo** (confirmed via `git status` —
2 files modified, not committed, not pushed), consistent with the standing "no git operations"
policy for this engagement.

## 6. Feature flags

`SEARCH_ENGINE` is the only feature flag in this system: `auto` (V2-first, V1 fallback where
`V1_FALLBACK_AUDIT.md` still keeps one — free-text query paths only), `v2` (strict, no V1 at all,
V2 exceptions surface as real errors), `v1` (V1-only, V2 never called). Production
(`lambda-env.json`) is already set to `auto`, which is the correct steady-state value — no change
recommended. `v1` remains available as an instant, single-env-var emergency rollback if ever
needed; `v2` is useful for a pre-deploy smoke test (exactly what this session's regression used).

## 7. Production-only operations — cannot be completed locally, documented here as exact steps

These are the only remaining items. Each requires production AWS access this environment
deliberately does not have.

### 7a. Deploy `shopbot-main` to Lambda

The existing CI/CD pipeline (`.github/workflows/deploy-lambda.yml`, triggered on push to
`main`/`master`, or manually via `workflow_dispatch`) builds via
`deployment/lambda/build-docker.sh` → `deployment/lambda/Dockerfile.build` → uploads
`shopbot.zip` via AWS CLI. **No changes needed to the trigger mechanism** — the pipeline is fixed
(see §1) and ready; running it just requires pushing this branch's commits (a git operation,
correctly left for you to trigger explicitly).

### 7b. Update the production OpenSearch mapping

The new fields (`review_stats`, `cons_list`, `pros_list`, `search_keywords`,
`ingredients.structured`) need to be added to the production index's mapping. OpenSearch allows
adding new fields to an existing mapping without downtime or reindexing existing documents (a
"put mapping" is additive-only; existing documents simply have these fields as null/absent until
next touched). From the `search` repo, using its own `indexing_es_client.py` /
`search_v2/indexing/mapping_builder.py`:

```bash
# From the search/ repo, against the PRODUCTION OpenSearch endpoint —
# review connection details in indexing_env_defaults.py / your production .env first.
python3 -c "
from search_v2.indexing.mapping_builder import build_mapping
from indexing_es_client import get_es_client, create_index_with_mapping
# Inspect build_mapping()'s output, then apply only the NEW field mappings via
# the client's indices.put_mapping() — do not recreate the index (that would
# require a full reindex+alias-swap, unnecessary for an additive change).
"
```

### 7c. Backfill existing production documents (optional, recommended)

Adding the mapping alone does **not** populate `review_stats`/`cons_list`/etc. on documents already
indexed — only documents indexed or re-indexed *after* the mapping update will carry them (assuming
the production Mongo source actually has `tags_and_sentiments` — confirmed likely true in
production per `FINAL_MIGRATION_REPORT.md` §11, unlike this local snapshot). To backfill:

```bash
# From the search/ repo, against production Mongo + production OpenSearch:
python3 search_v2/indexing/index_v2.py all
# Or, for a narrower/safer rollout, incremental mode against a specific id batch:
python3 search_v2/indexing/index_v2.py incremental --upsert-ids <id1> <id2> ...
```

This uses `EMBEDDING_BACKEND=bedrock` in production (confirmed), so this incurs real AWS Bedrock
Titan embedding API costs proportional to the number of documents re-embedded — size the batch
accordingly, or run during a low-traffic window if doing a full `all` pass.

### 7d. Post-deploy smoke test

After 7a-7c, run this repo's Postman regression runner (see `POSTMAN_REGRESSION.md`, new this
session) against the production API base URL with `SEARCH_ENGINE=v2` temporarily forced (via the
Lambda env var, reverted after), to prove V2 alone handles production traffic cleanly — the same
methodology `V1_FALLBACK_AUDIT.md` used locally, now against real production data volume.

## 8. What is explicitly NOT a blocker

- `shopping_bot/data_fetchers/es_products.py` remains in place, fully functional, providing every
  disclosed V1 fallback — no code deletion is required before deploying the V2 migration itself.
- The `vision_flow.py` / `suggest_brand()` gap (`V1_RUNTIME_INVENTORY.md` §D) is an isolated,
  narrow conversational-flow pathway, not part of the Search API surface — does not block deploying
  the Search V2 migration.
