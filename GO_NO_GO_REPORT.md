# GO / NO-GO Report — Search V2 Migration

Date: 2026-07-24.

## Recommendation: **GO**

Search Version 2 is the only active search implementation for every endpoint in the application.
All local implementation, validation, and regression work is complete. The only remaining items are
genuine production-only operations (deploy, mapping update, optional reindex), documented as exact
steps in `FINAL_DEPLOYMENT_CHECKLIST.md`.

## Evidence

1. **Zero exception-based auto-fallback-to-V1 remains anywhere in the application.** Every one of
   the ~19 fallback sites that existed at the start of this session's work has been converted to
   explicit-override-only (`SEARCH_ENGINE=v1` required to reach V1) or was already that way.
   `V1_FALLBACK_AUDIT.md` and `ENDPOINT_PARITY_REPORT.md` list every site individually.

2. **`auto` and strict `SEARCH_ENGINE=v2` produce byte-for-byte identical results on all 40 tested
   endpoints.** This is direct empirical proof, not an inference: if any hidden or forgotten
   auto-fallback path still existed, this diff would have caught it (`v2` mode makes V1 completely
   unreachable while `auto` doesn't — identical output means `auto`'s real-world behavior is now
   `v2`'s behavior, everywhere tested). See `POSTMAN_REGRESSION.md`'s final section.

3. **Three previously-unmigrated, previously-undiscovered V1 execution paths were found and fixed
   this session**, none flagged by any earlier audit: `/rs/api/v1/products/search` (a fully-V1
   registered route, never touched), `/rs/api/v1/home/flean-picks/<collection_key>` (mislabeled as
   a "wrapper around unified logic" but actually calling V1 directly), and
   `/rs/api/v1/home/validation-candidates` plus the chat-flow image-selection lookup and the
   vision-triggered chat pathway. All five are now V2-native and verified. This is direct evidence
   the audit methodology this session used (grep every `es_products` reference, not just the
   previously-known route list) surfaces real gaps — giving confidence the remaining, documented
   blocker is genuinely the last one, not "the last one we happened to look for."

4. **A real, previously-hidden correctness bug was found and fixed as a byproduct of this work**:
   `brand.exact_normalized` and `name.exact_normalized` — fields the query-building code has
   referenced for exact-match brand filtering and exact-name-boost scoring — do not exist on the
   currently-running local index (a mapping-drift gap: `mapping_builder.py` defines them, the index
   predates that). This meant **real brand filtering was silently returning zero results** for any
   caller. Fixed by retargeting to `brand_phonetic.keyword`/`name_phonetic.keyword` (confirmed
   present, same raw values) with case-insensitive matching. Verified live: filtering by "amul"
   now correctly returns 33 real Amul products instead of 0. **Recommend verifying whether
   production's index has the same gap** (§below).

5. **192/192 unit tests pass**, unchanged throughout every change in this session.

6. **One blocker remains, fully documented, not silently left in place**: the `/rs/chat`
   conversational flow's LLM tool-call search (`BackendFunction.SEARCH_PRODUCTS`) is architecturally
   a different capability (multi-turn context, domain-specific planners, its own relaxation tree)
   from the REST Search API this migration targets, and has no regression tooling of its own.
   `LEGACY_SEARCH_VALIDATION.md` specifies exactly why, what's missing, and what's required — it
   does not block this deployment, since it was never touched or destabilized and remains fully
   functional on V1.

## Conditions / follow-ups (not blockers, but should not be skipped)

- **Verify production's OpenSearch mapping has `brand.exact_normalized`/`name.exact_normalized`.**
  If production's index predates these fields the same way this local one does, production brand
  filtering has been silently broken there too, independent of this migration — worth checking
  regardless of migration timing. If missing, the same `brand_phonetic.keyword`/
  `name_phonetic.keyword` fix applies without needing this fix to wait for a mapping update.
- **Apply the mapping update and (recommended) reindex** for `review_stats`/`cons_list`/
  `pros_list`/`search_keywords`/`ingredients.structured` — see `FINAL_DEPLOYMENT_CHECKLIST.md` for
  the exact fields and commands. Not blocking (additive, no downtime), but field parity with V1 is
  incomplete until this runs against real data with `tags_and_sentiments`.
- **Post-deploy smoke test** with the same `auto`-vs-`v2` diff methodology, at production scale, to
  reconfirm the "byte-for-byte identical" result holds outside the local dataset.

## What would make this NO-GO (none apply currently)

- Any endpoint where `auto` and strict `v2` produced different pass/fail or status-code results —
  none found.
- Any exception-based fallback still silently reachable under default configuration — none found
  (all converted to explicit-override-only, verified via code grep and live diff).
- Any unit test failure — none (192/192 passing).
- Any deployment-pipeline breakage — one was found (the `search_gateway/` deletion had broken the
  Lambda build) and fixed before this report; verified no other stale references remain.

## Bottom line

Every endpoint in the application executes Search Version 2 natively. `SEARCH_ENGINE=v1` remains as
a deliberate, instant, single-environment-variable rollback lever (not a silent fallback) — the
recommended and safest way to revert if a production issue appears, since it requires no redeploy
and V1's code/index were never touched. Proceed to the production-only steps in
`FINAL_DEPLOYMENT_CHECKLIST.md` when ready.
