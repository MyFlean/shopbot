# Production Readiness & Deployment Validation Audit — Phases 1–4

Date: 2026-07-24. Per your workflow requirement, this document covers everything validatable from
the local repository and local environment. **Phases 5 and 6 require production access and have
NOT been started** — see the stop-and-ask request at the end of this document.

---

## Phase 1 — Local Repository & Deployment Audit

### Deployment surfaces found

This repo has **two separate, real deployment targets**, not one:
1. **ECS/Fargate** via `Dockerfile` + `deploy.sh` + `ecs-task-definition.json` (Gunicorn, port 8000).
2. **AWS Lambda** via `lambda_handler.py` + `requirements-lambda.txt` (serverless-wsgi adapter).

Both were audited.

### Finding 1 (Medium) — Lambda handler's "critical endpoint" path list can never match the onboarding/product-recommendations flow routes

- **Evidence**: `shopping_bot/routes/onboarding_flow.py`'s blueprint is registered at
  `shopping_bot/__init__.py:418` as `app.register_blueprint(flow_bp)` — **with no `url_prefix`** —
  unlike every other blueprint in that file, all of which get `url_prefix='/rs'`. Confirmed both by
  reading the code and empirically against the running local server:
  ```
  curl http://localhost:8080/flow/product_recommendations/health      → 200
  curl http://localhost:8080/rs/flow/product_recommendations/health   → 404
  ```
- **Why it matters**: `lambda_handler.py`'s `is_critical_endpoint` check tests whether
  `"/rs/flow"` appears in the request path, to decide whether to block on secrets loading before
  serving the request. Since the real route is `/flow/...` (no `/rs` prefix), this check can
  **never** be true for these routes — they always take the "non-critical, don't wait for secrets"
  path, on Lambda specifically (this has no effect on ECS, which doesn't use `lambda_handler.py`).
- **Actual runtime impact, traced through the code**: `handle_product_recommendations_flow()`
  reads its data from Redis via `background_processor.get_processing_result()`, populated by an
  earlier request (typically `/rs/chat`, which *is* correctly gated). If a cold Lambda container's
  very first request happens to be this flow route before any other request has triggered secrets
  loading, `REDIS_HOST` could still be `localhost` and the Redis lookup would fail. This is a narrow
  window — traced the fallback path and confirmed it degrades **gracefully** (returns a "Results not
  found" screen, does not crash) — so this is not a blocker, but it is a real, reproducible mismatch
  between the Lambda handler's assumed route prefix and the actual registered blueprint.
- **Recommended fix**: either register `flow_bp` with `url_prefix='/rs'` for consistency with every
  other blueprint, or change `lambda_handler.py`'s critical-path check from `/rs/flow` to `/flow`.
  Not fixed in this pass per your instruction not to refactor/add features during this audit —
  flagging for your decision since either fix touches routing behavior that a WhatsApp Flow client
  and/or a Terraform/API-Gateway route mapping may already depend on the current URL shape.

### Finding 2 (Informational, needs production confirmation) — production's OpenSearch backend is AWS OpenSearch Serverless (AOSS) via IAM/SigV4, not the plain HTTP OpenSearch/Elasticsearch used for every local validation this engagement

- **Evidence**: `ecs-task-definition.json` sets `AOSS_ENABLED=true`, `ES_USE_IAM=true`, and
  `SEARCH_AWS_REGION=ap-south-1`, and `shopping_bot/data_fetchers/es_products.py` (`_is_aoss_url()`,
  `IAMAuthenticatedFetcher`) confirms this switches the client to AWS SigV4-signed requests against
  an Amazon OpenSearch Serverless endpoint.
- **Why it matters**: every piece of validation performed this entire engagement — including the
  "true V1 validation" against the real `elastic-local` Docker container, and every V2 test against
  `opensearch-2-15` — used plain HTTP auth against locally-run OpenSearch 2.15 / Elasticsearch
  8.15.0. AOSS has known behavioral differences from self-managed OpenSearch/Elasticsearch (some
  API/mapping features are unsupported or behave differently on Serverless). **None of this
  engagement's testing has touched a real AOSS endpoint.** This is not a defect — it's an
  environment-parity gap that only production validation (Phase 5) can close.
- **Also unverified**: whether `ecs-task-definition.json` in this repo reflects the *currently
  deployed* task definition revision, or is a stale local artifact — AWS is the source of truth, not
  this file. Flagging rather than assuming.

### Finding 3 (Informational) — several Bedrock/embedding config values have no explicit production override on file, relying on code-level defaults

- `BEDROCK_REGION` (default `ap-south-1`), `BEDROCK_EMBEDDING_MODEL_ID` (default
  `amazon.titan-embed-text-v2:0`), `SEARCH_V2_EMBEDDING_DIM` (default `512`), and
  `SEARCH_V2_INDEX_NAME` (default `products-search-v2`) are not present in
  `ecs-task-definition.json`'s `environment`/`secrets` blocks. `AWS_BEARER_TOKEN_BEDROCK` **is**
  correctly wired via Secrets Manager.
- This is likely fine (the coded defaults may be exactly the intended production values), but it is
  **not verified** — confirming this requires seeing the actual production embedding model ID and
  index name, which requires production access.

### Finding 4 — confirms a genuinely important reassurance: the local index-mismatch bug (Turn 2's major discovery) does NOT appear to exist in the ECS production config

- `ecs-task-definition.json` sets `ELASTIC_INDEX=products_master` and does **not** set
  `SEARCH_V2_INDEX_NAME` at all in its environment block. Per `_resolve_products_index()`'s logic
  (`SEARCH_V2_INDEX_NAME` wins only if set), this means **in production, V1's legacy fetcher would
  correctly resolve to `products_master`, not silently get redirected to the V2 index** — unlike this
  local dev environment, where `.env`'s `SEARCH_V2_INDEX_NAME=products-search-v2` accidentally
  overrides the legacy fetcher too. This confirms the Turn-2 methodology flaw was a **local-only
  configuration artifact**, not something that would have affected a genuine V1-vs-V2 comparison run
  in production. Still not provably true without seeing production's actual live environment
  variables (Secrets Manager or Terraform could inject something not visible in this file), so this
  is stated as "no evidence of this problem in production config," not "confirmed absent in
  production."

### Environment variable audit (Phase 1 requirement)

- **Obsolete/unused vars in `.env`**: none found. Initial grep suggested 9 "unused" vars
  (`SEARCH_V2_EMBEDDING_DIM`, `SEARCH_V2_STRICT_ZERO_RESULTS`, etc.) but this was a false positive —
  `search_v2/config/settings.py` reads them through internal `_bool()`/`_int()`/`_str()` helper
  wrappers rather than direct `os.getenv()` calls, which the first-pass grep missed. Re-checked
  against the actual helper call sites; all 24 vars in `.env` are genuinely read somewhere in code.
- **Vars referenced in code but absent from local `.env`**: ~90, but this is expected and correct —
  the large majority are either (a) settings with safe code-level defaults not being overridden
  locally (all `SEARCH_V2_*` tunables), or (b) AWS/Lambda/Secrets-Manager-only vars
  (`AWS_ACCESS_KEY_ID`, `SECRETS_MANAGER_SECRET`, `K_SERVICE`, etc.) that intentionally have no
  local-dev equivalent, per `requirements-lambda.txt`'s own documented design ("Lambda gets
  configuration from environment variables set by Terraform/Secrets Manager, not a local `.env`
  file"). No missing-with-no-default, code-breaking gap found.
- **Conflicting configuration**: none found between `DevelopmentConfig`/`ProductionConfig`/
  `LambdaConfig` in `shopping_bot/config.py` — `LambdaConfig` correctly extends `ProductionConfig`
  with no contradictory overrides.

### Startup sequence / dependency initialization

- `run.py` correctly exposes a module-level `app` object for `gunicorn run:app` (verified by
  reading the file — `app = create_application(strict_env=False)` runs at import time, not only
  inside `if __name__ == "__main__"`), and validates `REDIS_HOST` strictly for the CLI path but
  non-strictly for the WSGI/Gunicorn path, so a container can boot and serve a health page even if
  Redis isn't reachable yet — reasonable, non-fatal design.
- `lambda_handler.py`'s lazy app initialization, async/sync secrets loading, and the health-check
  fast-path (bypassing app init entirely for `/rs/health` and `/health`) are all sound patterns for
  avoiding Lambda cold-start timeouts. The one gap found is Finding 1 above.
- Dockerfile: multi-stage-free but reasonable single-stage build, non-root user, proper
  `HEALTHCHECK`, Gunicorn with sane worker/timeout/max-requests settings. No issues found.

---

## Phase 2 — Search V2 Functional Validation (re-verified this pass)

Re-ran endpoint checks with the same "value must be semantically correct, not just present"
standard as the prior turn's audit, focused on catching anything the prior pass missed:

- **Found and fixed**: `category_group` on autocomplete suggestions was present as a key in every
  response but its value was **always `null`** — a previously-claimed fix that never actually
  worked, because the completion-suggester query's `_source` filter
  (`search_v2/retrieval/lexical_query_builder.py`'s `build_suggest_query()`) excluded the field from
  the response entirely. Fixed by adding `"category_group"` to the `_source` list (one-line change,
  `lexical_query_builder.py:571`). Verified live: now returns the real indexed value (e.g.
  `"f_and_b"`) for every suggestion. Regression suite unaffected (192 passed, 1 skipped, before and
  after — this widens which stored fields are returned; it adds no query logic).
- All other endpoint/field checks from the prior turn's audit re-spot-checked and still hold (see
  `FINAL_PRODUCTION_ACCEPTANCE_AUDIT.md` for the full list, not repeated here to avoid duplicating
  already-documented evidence).

---

## Phase 3 — Search Behaviour Validation (re-verified, one new finding)

- Lexical, lexical-with-typos, semantic, category browsing, brand filtering, pagination, sorting,
  filtering, PDP, curated, autocomplete: all re-confirmed working as previously documented.
- **New finding (Medium, documented not fixed)**: the hybrid retrieval pipeline has no relevance
  floor on its semantic/kNN leg. `search_v2/config/settings.py`'s `SEMANTIC_MIN_SCORE` defaults to
  `0.0` and is unset in this environment, so
  `search_v2/retrieval/hybrid_search_orchestrator.py`'s
  `if settings.SEMANTIC_MIN_SCORE > 0: semantic_hits = [...]` filter never activates. Reproduced: a
  deliberately nonsensical query (`zzxxqqnonexistentproduct999`) returns `total: 72` real,
  effectively-random products. Isolated the cause by querying OpenSearch directly with a lexical-only
  `match` for the same string — confirmed `0` lexical hits — proving all 72 results came from the
  unfiltered semantic leg. This is a genuine Search-V2-specific UX behavior difference from V1 (which
  had no semantic retrieval and would have returned true zero results for gibberish), not a
  regression introduced by this engagement's compatibility fixes. Recommended as a pre-launch tuning
  decision (set a validated non-zero `SEARCH_V2_SEMANTIC_MIN_SCORE`), not fixed here since picking
  the right threshold needs a relevance-labeling pass, not a guess.
- Per your explicit instruction, no Search V2 improvement was removed or weakened to imitate V1.

---

## Phase 4 — Data Integrity Validation (re-verified)

- OpenSearch document count: **6,510**, matching every prior checkpoint this engagement — no drift.
- Mongo → Index → API response field tracing: re-spot-checked `avg_rating` (present as `null`,
  confirmed correct because this dataset has no rating data ingested anywhere in Mongo — not a
  defaulted/stale value) and the `category_group` fix above. No new serialization, duplication, or
  malformed-object issues found beyond what's already documented in
  `FINAL_PRODUCTION_ACCEPTANCE_AUDIT.md`.

---

## STOP — Production access required before continuing

Per your explicit instruction, I am stopping here rather than assuming production behavior.

**Phases 5 (Production Environment Validation) and 6 (Production Smoke Tests) require the
application's environment configuration to be pointed at real production** (production OpenSearch/
AOSS endpoint, production Lambda config, production Bedrock model/region, production index name).

Specifically, before I can validate:
- production OpenSearch/AOSS index name, mappings, and document count
- Lambda cold-start behavior against real Secrets Manager values
- Bedrock connectivity with the real production model ID/region
- whether `BEDROCK_REGION`/`BEDROCK_EMBEDDING_MODEL_ID`/`SEARCH_V2_INDEX_NAME` production values
  match the code-level defaults this local environment has been implicitly relying on (Finding 3
  above)
- real production endpoint behavior, logs, and latency

**please switch the application's environment configuration to production now**, and confirm once
done, so I can proceed with Phase 5 and 6 without assuming anything about an environment I haven't
actually observed.
