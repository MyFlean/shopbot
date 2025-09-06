## Risk Areas, Hotspots, and Recommendations

This document captures brittle paths, TODO/FIXME hotspots, and security/performance notes with concrete references.

### Security

- Secrets in env
  - Anthropic and ES API keys must be present; validation is strict in `shopping_bot/config.py:L27-L33, L35-L38, L48-L54`.
  - Recommendation: Provide `.env.example` and CI checks for required vars; consider secret managers.

- Legacy routes and key handling
  - Files like `routes/sign_key.py`, `routes/upload_key.py` exist; audit for production usage and access controls.
  - Recommendation: Disable or guard behind admin auth if unused.

### Stability & Correctness

- Redis dependencies
  - Startup fails if Redis unhealthy in `shopping_bot/__init__.py:L42-L61`. Runtime fallbacks exist in `redis_manager.get_context` (L134-L143).
  - Recommendation: Consider lazy init or degraded mode for resilience in non-critical paths.

- Session growth
  - `ctx.session.debug` stores `last_search_params`, `query_explanation`, etc. Risk of unbounded growth across turns.
  - Recommendation: Trim debug payloads and cap history; see `utils/helpers.trim_history` (L54-L60) – integrate for session debug keys.

- ES sync I/O in async endpoint
  - `routes/chat.py` is `async`, but ES calls are executed via `run_in_executor` (L205-L207, L1117-L1120 in fetcher usage). Generally safe but thread pool capacity must be sufficient.
  - Recommendation: Monitor thread usage; consider aiohttp/httpx for async ES calls if throughput grows.

- LLM tool-use fallbacks
  - Multiple places rely on tool-use; missing tool-use falls back to heuristics (`llm_service.py:L896-L1038`, `recommendation.py:L402-L416`).
  - Recommendation: Add metrics for fallback rates; consider circuit breakers or cached defaults.

- Brand carry-over logic
  - Intentional dropping of brand filters for generic follow-ups in `recommendation._normalise_es_params` (L974-L987) and ES builder notes.
  - Risk: May surprise users seeking same-brand alternates; ensure FE indicates brand state.

### Performance

- Anthropic token usage
  - Product prompt can be large (top products + enriched briefs). Max tokens up to 1500 for SPM (L893-L894).
  - Recommendation: Log token estimates and add caps based on traffic; consider summarizing briefs further.

- ES requests
  - Function score and highlights add latency. Ensure `TIMEOUT` (`es_products.py:L34`) suits prod.
  - Recommendation: Add retries/backoff for ES; consider caching recent param→result for short TTL.

- Redis serialization
  - Large sessions serialized frequently; debounce in `redis_manager.py` helps (L51-L124).
  - Recommendation: Increase debounce window or skip-save for read-only turns; add size monitoring.

### Observability

- Logging
  - Smart logger categories exist; ensure `BOT_LOG_LEVEL` set appropriately in prod to avoid verbosity.
  - Recommendation: Emit structured logs (JSON) in production; add IDs for tracing.

- Health/diagnostics
  - Multiple health endpoints exist; ensure monitoring is pointed at `/health` and `/__system_health`.
  - Recommendation: Add synthetic checks that execute a minimal ES request periodically.

### Documentation & Testing Gaps

- Vision flow
  - Referenced in chat route but not documented; dependencies unknown.
  - Action: Document `shopping_bot/vision_flow.py` behavior and external services.

- Tests
  - `shopping_bot/tests/test_chat.py` exists; broader coverage needed:
    - 4-intent classification and UX surface enforcement
    - ES param extraction and brand/dietary/budget normalization
    - Redis session persistence and TTL behavior
    - Error paths: ES timeout, missing tool-use, Redis down

### Quick Wins (High ROI)

- Replace `requests` with `httpx` async client for ES; remove executor shims in critical paths.
- Add rate limiting for Anthropic calls per user/session; cache ES params for identical queries within short windows.
- Centralize debug payload trimming; cap `ctx.session.debug` sizes.
- Emit metrics: fallback counts, ES errors, Redis save skips, token usage.

### Questions & Assumptions

- Are legacy routes still deployed? If not, remove or guard them.
- Is Anthropic model version pinned intentionally (`claude-3-5-sonnet-20241022`)? Plan for upgrades and compatibility.
- Confirm production ES mapping includes `category_paths.keyword` for exact filters.

### Verification Checklist

- ES timeout simulation: lower `TIMEOUT` and confirm graceful error payload in `/chat`.
- Redis down simulation: stop Redis and verify `get_context` returns empty but API returns meaningful error or casual message.
- Tool-use fallback: temporarily force tool-use miss and confirm deterministic fallbacks are returned.

