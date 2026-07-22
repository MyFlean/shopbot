# Incremental Production Fix Integration — Validation Log

Date: 2026-07-22
Baseline: `BASELINE_RECORD.md` (commit `d48c3f46`, clean working tree, local Docker environment).
All three groups below applied one at a time, each validated against that baseline before moving
to the next. No optimizations, refactors, or the earlier regression-investigation fixes
(warm-up forcing, `minimum_should_match`, parallelization) were reintroduced — those are excluded
per your instruction to bring back only genuine production blockers.

## Group A — Lambda packaging

**Files:** `deployment/lambda/Dockerfile.build`, `deployment/lambda/build-local.sh`
**Change:** added `COPY indexing_es_client.py` to the Docker build; fixed `build-local.sh`'s
stale `requirements.txt` reference (now `requirements-lambda.txt`) and missing
`search_v2/`/`search_gateway/`/`indexing_es_client.py` copies.
**Why a blocker:** `search_v2/retrieval/opensearch_client.py` imports `indexing_es_client` at
runtime; without it the package raises `ModuleNotFoundError` on every Search V2 request in Lambda.

**Validation:**
- Built the real Docker package (`build-docker.sh`) → 26M zip, confirmed `indexing_es_client.py`,
  `search_v2/__init__.py`, `search_gateway/__init__.py` all present.
- `pytest`: 192 passed, 1 skipped, 0 failed (unchanged).
- Live search (apple/curd/atta/coconut oil): `took_ms` and result totals unchanged from baseline
  (apple=14, curd=76, atta=76, coconut oil=105).
- **No regression.**

## Group B — Environment sync files

**Files:** `deployment/lambda/lambda-env.json` (new), `deployment/lambda/update-lambda-env.sh`
(new), `deployment/lambda/update-lambda-env-aoss.sh` (now a thin wrapper),
`.github/workflows/deploy-lambda.yml` (env-sync step now unconditional, calls the new script).
**Why a blocker:** these exist on `origin/main` (added there for exactly this purpose) and were
entirely absent here; without them there's no committed manifest of the production Lambda
environment variables and CI's env-sync step depended on a script (`update-lambda-env-aoss.sh`)
that silently skipped the merge whenever the `ES_URL` secret wasn't set.
**Note:** `lambda-env.json`'s `SEARCH_ENGINE` is set to `"auto"` (V2 first, automatic V1
fallback) rather than `origin/main`'s `"v1"` — matching your explicit instruction from the
original production-readiness pass; every other key matches `origin/main` unchanged.

**Validation:**
- `bash -n` on both shell scripts: syntax OK.
- `lambda-env.json`: valid JSON.
- `pytest`: 192 passed, 1 skipped, 0 failed (unchanged) — these are pure deployment/CI files, no
  Python runtime path touches them.
- **No regression** (none possible from this change — no runtime code path).

## Group C — Lambda critical-endpoint list

**File:** `lambda_handler.py`
**Change:** `is_critical_endpoint` now also matches `/rs/v1/search`, `/rs/v2/search`,
`/rs/api/v1/home` (previously only `/rs/chat`, `/rs/search`, `/rs/api/v1/products`, `/rs/flow`,
`/rs/api/v1/scanner`).
**Why a blocker:** these are the paths that reach Search V2 and the home APIs; without them in
the critical list, a cold Lambda container could dispatch a search/home request before secrets
finish loading (5s timeout), instead of waiting as intended.

**Validation:**
- `ast.parse`: syntax OK.
- `pytest`: 192 passed, 1 skipped, 0 failed (unchanged).
- Full 9-query regression suite re-run end to end:

| Query | took_ms | engine | total (vs. baseline) |
|---|---|---|---|
| apple | 39 | v2 | 14 (=) |
| curd | 33 | v2 | 76 (=) |
| protein bar | 42 | v2 | 136 (=) |
| greek yogurt | 22 | v2 | 75 (=) |
| dhaniya | 13 | v2 | 2 (=) |
| kothambir | 55 | v2 | 2 (=) |
| angur | 25 | v2 | 0 (=) |
| atta | 366 | v2 | 76 (=) |
| coconut oil | 318 | v2 | 105 (=) |

Every result total is identical to `BASELINE_RECORD.md`; every latency is within the range
already observed in Phase 1. **No regression.**

---

## Outcome

All three groups applied, validated individually, no STOP conditions triggered (no higher
latency, no incorrect routing, no incorrect results, no fresh-produce or typo-correction
regressions). Current working tree = baseline + these three groups only.

Files changed this phase:
```
 M .github/workflows/deploy-lambda.yml
 M deployment/lambda/Dockerfile.build
 M deployment/lambda/build-local.sh
 M deployment/lambda/update-lambda-env-aoss.sh
 M lambda_handler.py
?? deployment/lambda/lambda-env.json
?? deployment/lambda/update-lambda-env.sh
```
Nothing committed or pushed — local working-tree changes only.
