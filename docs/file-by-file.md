## File-by-File Map – Shopping Bot Backend

For each file: purpose, key symbols, ingress/egress, notable logic, and risks. Code references include file + line anchors for quick lookup.

### Root

- `run.py`
  - Purpose: Flask entrypoint; logging and env validation; starts server.
  - Key: `setup_smart_logging` (L24-L40), `validate_environment` (L43-L60), `create_application` (L62-L77), `main` (L119-L149).
  - Ingress: WSGI/CLI. Egress: `shopping_bot.create_app()`.
  - Notes: Enforces presence/format of `ANTHROPIC_API_KEY`. Prints startup summary.
  - Risks: Hard fail if missing env; depends on Redis availability at startup.

### shopping_bot/

- `__init__.py`
  - Purpose: App factory wiring Redis, core, and routes.
  - Key: `create_app` (L28-L222), `system_health_check` (L110-L159), `user_diagnostics` (L161-L183).
  - Ingress: `run.py`. Egress: registers blueprints; stores `ctx_mgr` and `bot_core` in `app.extensions`.
  - Notes: Validates Redis health; sets version flag; error handlers (L187-L205).
  - Risks: Redis down → raises; onboarding_flow registered best-effort.

- `config.py`
  - Purpose: Central config and env parsing.
  - Key: `BaseConfig` (L13-L55), `get_config` (L70-L80).
  - Ingress: Imported widely. Egress: Config values to services.
  - Notes: Validates Anthropic key presence/prefix; ES envs support legacy names.
  - Risks: Strict key format requirement may block dev usage if not prefixed.

- `bot_core.py`
  - Purpose: Orchestration; assessments; 4-intent flows; fetch and UX wiring.
  - Key: `ShoppingBotCore.process_query` (L55-L91), `_handle_follow_up` (L95-L173), `_start_new_assessment` (L333-L430), `_complete_assessment` (L507-L681).
  - Ingress: `routes/chat.py`. Egress: `LLMService`, data fetchers via `data_fetchers.get_fetcher`, `ux_response_generator`.
  - Notes: Follow-up delta-fetch policy; “no-ask” gate for certain product intents; stores `last_recommendation` snapshot for future turns.
  - Risks: Complex session mutations; depends on LLM to classify accurately; background stub path present but async disabled by default.

- `llm_service.py`
  - Purpose: All LLM interactions and tool schemas.
  - Key: Classification (L617-L660, L662-L701), unified generation (L703-L727), product response (L737-L1021), simple reply (L1040-L1082), follow-up (L1112-L1156), delta assess (L1157-L1226), requirements (L1228-L1299), questions (L1301-L1475), slot selection (L1476-L1532), ES param proxy (L1534-L1543).
  - Ingress: `bot_core`, `data_fetchers/es_products.py` (for ES params). Egress: Anthropic, `recommendation.get_recommendation_service`.
  - Notes: Uses AsyncAnthropic tool-use; product flows enrich top-K via ES `_mget`.
  - Risks: Requires Anthropic key; prompt complexity; potential token costs.

- `ux_response_generator.py`
  - Purpose: Convert LLM answer + 4-intent → UX payload.
  - Key: `UXResponseGenerator.generate_ux_response` (L122-L164), `_call_llm_for_ux` (L165-L233), `generate_ux_response_for_intent` (L447-L509).
  - Ingress: `bot_core`, `routes/chat.py` (image/selection paths). Egress: Anthropic.
  - Notes: Enforces SPM for `is_this_good`, MPM for others; dynamic QR using budget.
  - Risks: LLM reliance; QR labels need i18n/limits; SPM product-id enforcement logic handled upstream.

- `redis_manager.py`
  - Purpose: Durable context store with atomic merges and status lifecycle.
  - Key: `get_context` (L130-L173), `save_context` (L175-L203), `_save_context_pipeline` (L204-L223), `_save_context_cluster_safe` (L224-L252), `merge_fetched_data` (L253-L307), processing status/result getters/setters (L417-L529), `health_check` (L535-L569).
  - Ingress: App init; routes; core. Egress: Redis.
  - Notes: Debounce duplicate saves; TTL management; cluster-safe fallback.
  - Risks: JSON dumps of large sessions; TTL defaults; cluster detection heuristic.

- `models.py`
  - Purpose: Dataclasses for context and responses.
  - Key: `UserContext` (L20-L31), `RequirementAssessment` (L32-L38), `BotResponse` (L198-L208), enhanced UX classes (L40-L149, L152-L196).
  - Ingress: Core/services. Egress: n/a.
  - Notes: Enhanced UX types available; core uses legacy `BotResponse`.
  - Risks: Parallel UX types may confuse consumers if mixed.

- `enums.py`
  - Purpose: Enums for slots, functions, intents, and response types.
  - Key: `UserSlot`, `BackendFunction`, `QueryIntent`, `ResponseType`, UX enums.
  - Ingress: Widespread. Egress: n/a.

- `fe_payload.py`
  - Purpose: FE contract normalization and envelope creation.
  - Key: `map_fe_response_type` (L31-L61), `normalize_content` (L63-L191), `build_envelope` (L194-L229).
  - Ingress: `routes/chat.py`. Egress: n/a.
  - Notes: Derives `ux_type` even if minimal UX payload; clamps SPM to one product.

- `recommendation.py`
  - Purpose: ES parameter extraction and normalization (LLM + rules), service facade.
  - Key: `ElasticsearchRecommendationEngine.extract_search_params` (L319-L655), `_normalise_es_params` (L764-L997), `_extract_category_and_signals` (L1439-L1517), `RecommendationService` (L1552-L1574).
  - Ingress: `llm_service`. Egress: Anthropic.
  - Notes: Carries category/budget; drops brands on generic follow-ups; dietary normalization.
  - Risks: Prompt and token cost; environment taxonomy overrides.

- `scoring_config.py`
  - Purpose: Function score config and builders for ES queries.
  - Key: `build_function_score_functions` (L221-L280).
  - Ingress: ES fetcher. Egress: n/a.
  - Notes: Category-sensitive bonuses/penalties; base flean multiplier.

- `intent_config.py`
  - Purpose: Intent taxonomy, slot hints, and function TTLs.
  - Key: `INTENT_MAPPING` (L23-L125), `SLOT_QUESTIONS` (L146-L252), `FUNCTION_TTL` (L285-L290).
  - Ingress: LLM service, core. Egress: n/a.

- `utils/helpers.py`
  - Purpose: Generic utilities.
  - Key: `parse_budget_range` (L15-L21), `extract_json_block` (L29-L36), `unique` (L43-L50), `trim_history` (L54-L60), `safe_get` (L62-L66).

- `utils/smart_logger.py`
  - Purpose: Structured logging with levels and helper decorators.
  - Key: `SmartLogger` API (L19-L206), `configure_logging` (L308-L339).
  - Ingress: `run.py`, core, routes. Egress: Python logging.

### shopping_bot/routes/

- `chat.py`
  - Purpose: Main chat API with simplified architecture.
  - Key: `/chat` (L79-L448), `/chat/health` (L535-L569), CLI fallback (L454-L529), test/UX endpoints.
  - Ingress: HTTP. Egress: `ctx_mgr`, `bot_core`, ES fetcher, `LLMService`, `ux_response_generator`, `fe_payload`.
  - Notes: Special image paths; duplicate-processing guard; persists `wa_id`.
  - Risks: Endpoint is async but runs sync ES via executor; careful with event loop.

- `health.py`
  - Purpose: Readiness probe and echo.
  - Key: `/health` (L23-L31), `/health_check` (L33-L38).
  - Ingress: HTTP. Egress: Redis ping.

- Others (`onboarding_flow.py`, `reset.py`, `sign_key.py`, `upload_key.py`, `test_encryption.py`, `test123.py`)
  - Purpose: Legacy or utility routes; not central to simplified flow.
  - Risks: Key handling and legacy flows should be audited/disabled in production if unused.

### shopping_bot/data_fetchers/

- `es_products.py`
  - Purpose: ES search/mget and result shaping.
  - Key: `_build_enhanced_es_query` (L146-L498), `_transform_results` (L500-L593), `ElasticsearchProductsFetcher.search` (L649-L694), `mget_products` (L696-L739), `build_search_params` (L964-L1091), async handlers (L1103-L1166).
  - Ingress: `bot_core` via `get_fetcher`, `llm_service` product response enrichment, routes image-selection path.
  - Notes: Mapping hints for `category_paths.keyword`; strict brand enforcement only when hints exist.
  - Risks: Prints debug; relies on ES env; timeouts.

- `product_details.py`, `product_inventory.py`, `product_reviews.py`
  - Purpose: Mock/stub fetchers for details, inventory, and reviews.
  - Ingress: Registered via `register_fetcher`.
  - Risks: Stubs only; ensure not relied on for production.

### Tests

- `shopping_bot/tests/test_chat.py`
  - Purpose: Chat route tests (not reviewed in detail here).
  - Suggestion: Expand tests to cover 4-intent flows, ES param extraction, UX payload shapes, and Redis context persistence.

### Risks & Notes (pervasive)

- **Secrets**: Anthropic and ES keys must be set; `config.py` validation is strict.
- **Network**: ES calls use `requests` (sync) wrapped in executors; ensure worker threads are sufficient.
- **State**: Redis JSON size growth; TTL handling; potential session bloat in `debug`.
- **LLM**: Tool-use reliance; careful with max_tokens and temperatures; cost controls recommended.

### Questions & Assumptions

- Are non-chat routes (`onboarding_flow.py`, signing/upload routes) active in prod?
- Is there any background worker consuming “processing” states, or is it intentionally disabled?
- Confirm that only `BackendFunction.SEARCH_PRODUCTS` is used for search (others are stubs).


