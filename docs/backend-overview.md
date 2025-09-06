## Shopping Bot Backend – Architecture Overview

This document provides a high-level view of the backend architecture, major components, and the lifecycle of a typical user query.

### Core Stack

- **Framework**: Flask app factory (`shopping_bot/__init__.py`) used by `run.py`
- **Primary Orchestration**: `shopping_bot/bot_core.py`
- **LLM Layer**: `shopping_bot/llm_service.py`
- **UX Layer**: `shopping_bot/ux_response_generator.py`
- **Data Access**: `shopping_bot/data_fetchers/` (Elasticsearch, profile/history stubs)
- **State/Session**: Redis via `shopping_bot/redis_manager.py`
- **API Layer**: Flask Blueprints under `shopping_bot/routes/`
- **Scoring/Ranking**: `shopping_bot/scoring_config.py` (ES function_score helpers)
- **Recommendation Param Extraction**: `shopping_bot/recommendation.py`
- **FE Envelope**: `shopping_bot/fe_payload.py`

### Process Topology

1) Server startup
   - `run.py` loads env, configures logging, validates key env vars, and invokes `create_app()`.
     - See `run.py:L24-L40:setup_smart_logging`, `run.py:L43-L60:validate_environment`, `run.py:L119-L149:main`.
   - `shopping_bot/__init__.py:create_app()` initializes Redis, `ShoppingBotCore`, and registers routes.
     - See `shopping_bot/__init__.py:L42-L61` (Redis init), `L63-L75` (bot core), `L77-L105` (routes), `L108-L159` (system health), `L161-L183` (diagnostics).

2) Request handling (Chat)
   - Entry: `shopping_bot/routes/chat.py` blueprint.
   - Validates payload, loads context, and calls `ShoppingBotCore.process_query(...)`.
     - See `shopping_bot/routes/chat.py:L79-L90` (endpoint), `L130-L150` (context load), `L330-L343` (bot processing).
   - Special image paths:
     - `image_url` → vision flow (`shopping_bot/vision_flow.py`) then returns product IDs (MPM UX).
     - `selected_product_id` → ES `mget` and SPM UX.

3) Core orchestration
   - `ShoppingBotCore` governs assessment flow, follow-up handling, 4-intent product logic, data fetching, and response generation.
     - Entry: `shopping_bot/bot_core.py:L55-L91:process_query`.
     - Follow-ups: `shopping_bot/bot_core.py:L93-L173:_handle_follow_up` with delta assessment and fetch.
     - New query: `shopping_bot/bot_core.py:L333-L430:_start_new_assessment` → classify intent; gate 4-intent; compute requirements; ask slots or fetch.
     - Completion path: `shopping_bot/bot_core.py:L507-L681:_complete_assessment`.

4) LLM layer
   - `LLMService` handles:
     - Intent classification (L1/L2/L3 + product-related flag): `shopping_bot/llm_service.py:L617-L660`.
     - Product-intent (4-intent) classification: `shopping_bot/llm_service.py:L662-L701`.
     - Unified response generation:
       - Product paths (uses ES results + top-K enrichment via `_mget`): `shopping_bot/llm_service.py:L737-L1021`.
       - Non-product simple replies: `shopping_bot/llm_service.py:L1040-L1082`.
     - Follow-up detection and delta assessment: `shopping_bot/llm_service.py:L1112-L1156`, `L1157-L1226`.
     - Requirements assessment and contextual questions: `shopping_bot/llm_service.py:L1228-L1299`, `L1301-L1475`.
     - ES parameter extraction via `RecommendationService`: `shopping_bot/llm_service.py:L1534-L1543`.

5) Recommendation & ES
   - `shopping_bot/recommendation.py` builds ES parameters with LLM tooling and deterministic fallbacks (dietary/brand/budget/category mapping & carry-over).
     - Main extractor: `ElasticsearchRecommendationEngine.extract_search_params`: `shopping_bot/recommendation.py:L319-L655`.
     - Category-based scoring and function_score shaping are consumed by ES fetcher.
   - ES fetcher: `shopping_bot/data_fetchers/es_products.py` applies query building, mapping hints, and result transformation; includes `_mget` enrichment.
     - Query builder: `L146-L498`, transform: `L500-L593`, search: `L649-L694`, mget: `L696-L739`.

6) UX layer
   - `shopping_bot/ux_response_generator.py` converts product-intent + answer dict → UX payload (DPL, surface SPM/MPM, quick replies, product_ids).
     - Entry: `generate_ux_response_for_intent`: `shopping_bot/ux_response_generator.py:L447-L509`.
     - LLM tool-call and strict intent→surface enforcement: `L165-L233`.

7) Envelope & response
   - `shopping_bot/fe_payload.py` normalizes content and maps internal response types to FE contract.
     - Type mapping: `L31-L61`, normalization: `L63-L191`, envelope: `L194-L229`.

### External Integrations

- **Redis**: `shopping_bot/redis_manager.py`
  - Context load/save with debounce and cluster-safe writes; atomic fetched-data merge; background processing status lifecycle.
  - Key APIs: `get_context` `L130-L173`, `save_context` `L175-L203`, `merge_fetched_data` `L253-L307`, `set_processing_status` `L417-L466`.

- **Elasticsearch**: `shopping_bot/data_fetchers/es_products.py`
  - Uses `ELASTIC_*` envs; function_score driven ranking via `shopping_bot/scoring_config.py`.
  - Enrichment via `_mget` for top products to power UX persuasion.

- **Anthropic** (LLM): `shopping_bot/llm_service.py`, `shopping_bot/ux_response_generator.py`, `shopping_bot/recommendation.py`
  - All use `anthropic.AsyncAnthropic` and tool-use API.

### Lifecycle of a Typical User Query

1. HTTP POST `/chat` with `{user_id, session_id?, message, wa_id?, channel?}`.
2. Context load via Redis (`get_context`).
3. `ShoppingBotCore.process_query`:
   - Continue assessment if in-progress; else classify intent; for serious product L3 intents, classify 4-intent.
   - Assess requirements → either ask user (QUESTION) or run backend fetchers.
4. For product intents:
   - Extract ES parameters (recommendation engine) → `data_fetchers.es_products.search`.
   - LLM `generate_response` builds product answer; UX generator adds DPL/QR/surface.
5. Build FE envelope and return JSON.

### Components and Responsibilities

- `shopping_bot/routes/chat.py`: transport adapter; validates input; handles vision/selection short-circuits; builds envelopes.
- `shopping_bot/bot_core.py`: session flow control, missing-slot detection, fetch orchestration, UX generation wiring.
- `shopping_bot/llm_service.py`: all LLM logic; prompts & tool schemas; product/non-product generation; follow-ups; slot questions; ES params glue.
- `shopping_bot/recommendation.py`: robust ES param extraction, normalization, and category mapping; brand/dietary/budget carry-over policy.
- `shopping_bot/data_fetchers/es_products.py`: ES I/O, enriched transformation, and function_score ranking integration.
- `shopping_bot/scoring_config.py`: per-subcategory scoring config; builds ES function_score functions.
- `shopping_bot/ux_response_generator.py`: DPL/UX surface/QRs; strict SPM/MPM intent mapping.
- `shopping_bot/redis_manager.py`: durable user context; atomic merges; status lifecycle; health.
- `shopping_bot/fe_payload.py`: consistent FE contract.

### Health & Observability

- Startup logging in `run.py` prints architecture mode and health (`run.py:L98-L116`).
- Built-in health checks:
  - `/__system_health` (app-level): `shopping_bot/__init__.py:L110-L159`.
  - `/health` (blueprint): `shopping_bot/routes/health.py:L23-L31`.
  - `/chat/health`: `shopping_bot/routes/chat.py:L535-L569`.
- Smart logger with levels and structured categories: `shopping_bot/utils/smart_logger.py`.

### Configuration & Secrets

- `shopping_bot/config.py` reads env for Redis, Anthropic, and Elasticsearch; validates `ANTHROPIC_API_KEY` format and presence.
- ES base/index/API key may be set via `ES_URL`, `ES_API_KEY` (fallback to legacy `ELASTIC_*`).

### Assumptions & Notes

- `vision_flow.py` is referenced for image flows; considered part of the new architecture selector path but may rely on external vision services (not reviewed here).
- Legacy “flow” systems are deprecated; the simplified stack routes through `bot_core.py` + `llm_service.py` + `ux_response_generator.py` only.

### Verification Checklist

- Start app locally with valid env:
  - `ANTHROPIC_API_KEY`, `REDIS_HOST`, `ES_URL`, `ES_API_KEY`, `ELASTIC_INDEX`.
- Smoke test health:
  - `curl http://localhost:8080/__system_health | jq .status`
  - `curl http://localhost:8080/health | jq .status`
- Chat smoke tests:
  - `curl -X POST http://localhost:8080/chat -H 'Content-Type: application/json' -d '{"user_id":"u1","message":"show me protein bars"}'`
  - Expect `response_type=final_answer` and `content.ux_response` for product intents.
- Redis check:
  - `curl http://localhost:8080/__diagnostics/u1 | jq .components.redis.healthy`
- ES I/O check (logs): ensure `DEBUG: ES query found ...` lines appear on search.

### Questions & Assumptions

- Are there any additional non-Flask routes or async background processors in use (the simplified architecture suggests none)?
- Is `vision_flow.py` production-ready, and what are its external dependencies?
- Are any PEM/key-based signing flows (e.g., `routes/sign_key.py`) still active in production?
- Confirm that all traffic to product search goes through `BackendFunction.SEARCH_PRODUCTS` → ES.

### Questions & Assumptions

- Are there any additional non-Flask routes or async background processors in use (the simplified architecture suggests none)?
- Is `vision_flow.py` production-ready, and what are its external dependencies?
- Are any PEM/key-based signing flows (e.g., `routes/sign_key.py`) still active in production?
- Confirm that all traffic to product search goes through `BackendFunction.SEARCH_PRODUCTS` → ES.


