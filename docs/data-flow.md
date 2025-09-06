## Data & State Flow – Shopping Bot

This document traces how data moves across the API, `ShoppingBotCore`, LLM services, data fetchers, Redis context, and the FE envelope.

### High-Level Flow

1) Ingress (HTTP → Flask)
   - Endpoint: `shopping_bot/routes/chat.py:/chat` (L79-L448)
   - Payload: `{user_id, message, session_id?, wa_id?, channel?, image_url?, selected_product_id?}`
   - Loads Redis context: `current_app.extensions["ctx_mgr"].get_context(user, session)` → `shopping_bot/redis_manager.py:L130-L173`

2) Session state (Redis)
   - Context buckets: `permanent`, `session`, `fetched_data`.
   - Keys: `user:{user_id}:permanent`, `session:{session_id}`, `session:{session_id}:fetched`.
   - Save/merge: `save_context` (L175-L203) and `merge_fetched_data` (L253-L307) atomically update Redis with TTL.
   - Debounce/TTL: prevents duplicate writes and ensures expiration windows.

3) Orchestration (ShoppingBotCore)
   - Entry: `bot_core.py:ShoppingBotCore.process_query` (L55-L91)
     - Continue assessment → `_continue_assessment` (L434-L503)
     - New or follow-up → `_start_new_assessment` (L333-L430) or `_handle_follow_up` (L95-L173)
   - Assessment state stored in `ctx.session["assessment"]` (L401-L409); missing-data and priority-order guide next actions.
   - When no user slots pending, compute backend fetchers and run via `data_fetchers.get_fetcher` in async.

4) Data fetchers
   - Primary: `BackendFunction.SEARCH_PRODUCTS` → `shopping_bot/data_fetchers/es_products.py:search_products_handler` (L1103-L1135)
   - Build ES params:
     - Defaults and session → `_extract_defaults_from_context` (L845-L928)
     - LLM-based normalization → `LLMService.extract_es_params` → `RecommendationService.extract_es_params` (see `shopping_bot/recommendation.py:L319-L655`)
     - Final params: `_normalize_params` (L930-L963) + heuristics (L1017-L1064, L1066-L1081)
   - Execute ES:
     - Search → `ElasticsearchProductsFetcher.search` (L649-L694) using `_build_enhanced_es_query` (L146-L498)
     - Transform results → `_transform_results` (L500-L593)
     - Optional enrichment → `_mget_products` (L696-L739) for top-K briefs
   - Persist fetched data into `ctx.fetched_data["search_products"]` and save to Redis.

5) LLM generation and UX
   - Product path:
     - `LLMService.generate_response` (L703-L727) → `_generate_product_response` (L737-L1021)
     - Enrichment via ES `_mget` for top-K (L767-L791), build brief payloads (L792-L861), prompt with strict instructions (L877-L926)
     - For SPM (is_this_good), clamp to single product and propagate `product_ids` (L903-L961)
   - UX layer:
     - `generate_ux_response_for_intent` (L447-L509) calls `UXResponseGenerator._call_llm_for_ux` (L165-L233)
     - Enforces surface: is_this_good → SPM; others → MPM (L221-L228)
     - Quick replies may include budget-aware options (L274-L280)

6) Envelope
   - `fe_payload.build_envelope` (L194-L229) maps internal `ResponseType` to FE `response_type` and normalizes content.
   - MPM: keeps `summary_message`, `ux_response.product_ids/quick_replies`, and `product_intent` (L147-L171).
   - SPM: clamps to a single product if present (L108-L113).

### State Model (Session)

- `ctx.session` fields used by core and services:
  - `assessment`: phase, original_query, missing_data, priority_order, currently_asking
  - `intent_l1/l2/l3`, `is_product_related`, `product_intent`
  - `debug.last_search_params`, `debug.current_user_text`, `query_explanation`
  - `last_recommendation` (snapshot of last products for follow-ups)
  - `budget`, `preferences`, `dietary_requirements`, `brands`, `category_group`, `category_path(s)`
  - `canonical_query`, `last_query`, `wa_id`

### Data Contracts

- ES result shape (post-transform): `{"meta": {...}, "products": [{id, name, brand, price, flean_percentile, bonus_percentiles, penalty_percentiles, image, ...}]}` (`shopping_bot/data_fetchers/es_products.py:L500-L593`).
- Product answer (SPM): `{"response_type":"final_answer","summary_message":str,"products":[{id,text,description,price,special_features}],"product_intent":"is_this_good"}` (`shopping_bot/llm_service.py:L902-L961`).
- UX response: `{"ux_response": {"dpl_runtime_text": str, "ux_surface": "SPM|MPM", "quick_replies": [..], "product_ids": [..]}, "product_intent": str}` (`shopping_bot/ux_response_generator.py:L223-L229`, `L493-L509`).

### Error Handling & Fallbacks

- Redis down → `get_context` returns empty context but processing continues (`shopping_bot/redis_manager.py:L134-L143`).
- ES timeout/error → returns empty product list with error meta (`shopping_bot/data_fetchers/es_products.py:L686-L694`).
- LLM tool-use missing → deterministic fallbacks for ES params and product responses (`shopping_bot/recommendation.py:L402-L416`, `shopping_bot/llm_service.py:L1022-L1038`).
- CLI/test channel with flow content → CLI fallback text builder (`shopping_bot/routes/chat.py:L454-L529`).

### Questions & Assumptions

- Are any other `BackendFunction` handlers actively used besides `search_products`? Others appear stubbed.
- Confirm production Redis TTL expectations (`config.REDIS_TTL_SECONDS`) and memory usage patterns.
- Confirm that ES index mapping has `category_paths.keyword` in production to enable exact term filters.

### Verification Checklist

- Redis persistence
  - Trigger `/chat` and verify `session:<session_id>` and `session:<session_id>:fetched` keys exist and TTL set.
- ES query composition
  - Enable logs and check `DEBUG: Enhanced ES Query Structure` and `DEBUG: ES query found ...` messages.
- SPM vs MPM behavior
  - SPM path: send `selected_product_id` or query like "is this good?"; check single product in `content.products` and `ux_response.ux_surface == SPM`.
  - MPM path: query like "show me options under 100"; expect `product_ids` in `ux_response`.


