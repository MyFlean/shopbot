# Baseline Record — Phase 1

Date: 2026-07-22
Commit: `d48c3f46` ("Resolve dynamic filter merge issues and fix infinite loop") — working tree
confirmed clean, `git status --short` and `git diff --stat HEAD` both empty. This is the exact
state you personally verified; nothing from prior sessions (production-readiness fixes, latency
fix, fresh-produce fix, or any report files) is present. **No changes were made in this phase.**

Environment: local Docker stack only — `mongo-product-scripts-local`, `redis`, `opensearch-2-15`,
all confirmed `Up` via `docker ps` before testing. App started via `./venv/bin/python run.py`
(config `production`, not `lambda`), local `.env` (not inspected).

Full test suite: `pytest search_v2/tests/ shopping_bot/tests/` → **192 passed, 1 skipped, 0
failed.**

---

## Per-query results (all via direct `gateway.search()` calls — same method used in every prior
investigation this session, so numbers are comparable)

| Query | Routing decision | Intent source | Confidence | Results | Dynamic filter groups | `took_ms` (single call) |
|---|---|---|---|---|---|---|
| apple | LEXICAL_ONLY | fresh_produce | 1.00 | 3 | 2 | 64 |
| curd | LEXICAL_ONLY | head_term | 0.70 | 3 | 5 | 27 |
| protein bar | LEXICAL_ONLY | head_term | 0.73 | 3 | 5 | 42 |
| greek yogurt | LEXICAL_ONLY | head_term | 0.53 | 3 | 5 | 23 |
| dhaniya | LEXICAL_ONLY | fresh_produce | 1.00 | 2 | 3 | 11 |
| kothambir | LEXICAL_ONLY | fresh_produce | 1.00 | **2** | 3 | 52 |
| angur | LEXICAL_ONLY | fresh_produce | 1.00 | **0** | 0 | 26 |
| atta | HYBRID | none | 0.00 | 3 | 5 | 348 |
| coconut oil | HYBRID | none | 0.00 | 3 | 5 | 309 |

## Repeated-call latency (HTTP endpoint, `meta.took_ms`, 4 consecutive calls per query — warm)

```
apple:        18, 8, 8, 8 ms
curd:         19, 18, 19, 19 ms
protein bar:  49, 35, 54, 33 ms
atta:         304, 280, 288, 269 ms
coconut oil:  351, 358, 337, 139 ms
```

## Correctness notes

- **Lexical warm latency (apple, curd, protein bar, greek yogurt): 8–54ms** — inside/near your
  stated 30–60ms historical baseline, several queries even faster.
- **Hybrid latency (atta, coconut oil): 139–358ms** — inside your stated "generally under ~400ms"
  baseline.
- **`kothambir` unexpectedly returns correct results (2) on this local index**, identical to
  `dhaniya` — both resolve to the same `fresh_produce`/coriander family at confidence 1.0. This is
  notable: the `minimum_should_match: 1`-unconditional code (no fix applied — confirmed via `grep
  -n "has_authoritative_id_filter" search_v2/retrieval/lexical_query_builder.py` returning
  nothing, i.e. the original code is active) is the same code that produced 0 results for
  `kothambir` against the remote testing/production environment in the prior investigation. Here
  it doesn't fail. This means the failure mode traced earlier (id-filter present, but the free-text
  `should` clause scores zero against the vernacular alias, so `minimum_should_match: 1` excludes
  an otherwise-allowlisted doc) is real and reproducible, but **whether it actually manifests
  depends on the specific index's document text** — this local index's coriander documents
  apparently score nonzero against "kothambir" (via some field/clause I have not re-diagnosed in
  this phase, since Phase 1 is baseline-recording only, not investigation) where the remote
  environment's did not. Flagging this discrepancy rather than glossing over it — it does not
  change the underlying code-level finding from the regression investigation, only which queries
  are observed to trip it in which environment.
- **`angur` returns 0 results here too**, consistent with the remote environment — most likely the
  same stale-curated-id issue found earlier (32/89 fully-stale families), but I have not
  re-verified the specific ids against this local index in this phase.
- Dynamic filters populate correctly (2–5 filter groups depending on query) for every query that
  returns results.
- Business ranking runs without error on every query (no exceptions, consistent with the
  `apply_business_ranking`/`promote_lab_tested` calls inside `_search()`).

---

## This is the reference baseline for Phase 2 onward

Any regression introduced by reintroducing a production fix will be measured against the numbers
above — not against the "testing/production" remote-environment numbers from the earlier
investigation (different index/data, as just demonstrated by the `kothambir` discrepancy), and not
against any of my own earlier optimization attempts (parallelization, warm-up forcing,
`minimum_should_match` fix) — none of which are present in this baseline by design.

Waiting for confirmation before Phase 2 (identifying which previously-found production-blocking
changes to reintroduce).
