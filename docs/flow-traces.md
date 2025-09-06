## End-to-End Traces – Sample Queries

This document walks through two realistic scenarios: a multi-product exploration (MPM) and a single-product validation (SPM). Each step cites the code location, inputs, outputs, and core reasoning.

### Scenario A: MPM – Category exploration with refinements

- Base query: "show me protein bars"
- Follow-up 1: "under 200"
- Follow-up 2: "no palm oil"

Step 0: Ingress and context
- Endpoint: `shopping_bot/routes/chat.py:/chat` (L79-L448)
  - Input: `{ user_id: "u123", session_id: "u123", message: "show me protein bars" }`
  - Calls Redis: `redis_manager.get_context` (L130-L173) → returns `UserContext` with empty/new session.
  - Persists `current_user_text` into session (L152-L163).

Step 1: Core orchestration – new assessment
- `ShoppingBotCore.process_query` (L55-L91) detects new conversation and calls `_start_new_assessment` (L333-L430).
  - Intent classification: `LLMService.classify_intent` (L617-L660)
    - Input: query="show me protein bars", recent context from session
    - Output: `layer3="Product_Discovery"`, `is_product_related=true`
  - 4-intent classification: `LLMService.classify_product_intent` (L662-L701)
    - Output: `intent="show_me_options"` (exploration), confidence > 0.3
  - Assess requirements: `LLMService.assess_requirements` (L1228-L1299)
    - Output: Missing data likely includes `ASK_USER_BUDGET`, `ASK_DIETARY_REQUIREMENTS` (per `intent_config.INTENT_MAPPING` and `SLOT_QUESTIONS`).
  - Policy gates: no-ask only for certain intents; for "show_me_options" we can ask or fetch depending on missing slots.
  - Session update: stores `assessment` and `intent_*` (L401-L409), may inject `USER_BUDGET` to top of priority (L410-L425).
  - Next: `_continue_assessment` (L434-L503).

Anthropic call log (Turn 1)
- classify_intent → `anthropic.messages.create` with tool `classify_intent`
  - Code: `shopping_bot/llm_service.py:L637-L644`
  - Purpose: Map latest message into L1/L2/L3 and product-related flag
  - Params:
    - model: from config `Cfg.LLM_MODEL` (default "claude-3-5-sonnet-20241022")
    - messages[0].content: `INTENT_CLASSIFICATION_PROMPT` filled with `recent_context` + `query`
    - tools: `[INTENT_CLASSIFICATION_TOOL]`
    - tool_choice: `{type: "tool", name: "classify_intent"}`
    - temperature: 0.2, max_tokens: 150
  - Result: tool_use → `{layer1, layer2, layer3, is_product_related}` (parsed at L650-L659)

- classify_product_intent → `anthropic.messages.create` with tool `classify_product_intent`
  - Code: `shopping_bot/llm_service.py:L678-L685`
  - Purpose: Map serious product query into one of 4 intents
  - Params:
    - model: `Cfg.LLM_MODEL`
    - messages[0].content: `PRODUCT_INTENT_CLASSIFICATION_PROMPT` with `query`, `context`
    - tools: `[PRODUCT_INTENT_TOOL]`
    - tool_choice: `{type: "tool", name: "classify_product_intent"}`
    - temperature: 0.1, max_tokens: 200
  - Result: tool_use → `{intent, confidence, reasoning}` (L687-L700)

- assess_requirements → `anthropic.messages.create` with dynamic tool `assess_requirements`
  - Code: `shopping_bot/llm_service.py:L1252-L1259`
  - Purpose: Decide missing ASK_* slots and FETCH_* functions
  - Params:
    - model: `Cfg.LLM_MODEL`
    - messages[0].content: `REQUIREMENTS_ASSESSMENT_PROMPT` with `query`, `intent`, `layer3`, and session keys
    - tools: `[build_assessment_tool()]` (dynamic enum of slots + functions)
    - tool_choice: `{type: "tool", name: "assess_requirements"}`
    - temperature: 0.1, max_tokens: 500
  - Result: tool_use → `{missing_data:[{function,rationale}], priority_order:[...]}` (L1261-L1299)

Step 2: Ask user budget (first turn)
- `_continue_assessment` sees user slots pending (L453-L477) and returns QUESTION.
  - Output: `BotResponse(ResponseType.QUESTION, q)` → `fe_payload.build_envelope` later produces `response_type="ask_user"`.
  - Envelope: `fe_payload.normalize_content` (L72-L101) formats options (from `SLOT_QUESTIONS`).

Follow-up 1: "under 200"

Step 3: Core reasoning – follow-up classification and delta
- Ingress same as Step 0; context contains `assessment` and last turn.
- `ShoppingBotCore.process_query` → `_handle_follow_up` (L95-L173)
  - `classify_follow_up` (L1112-L1156) returns `is_follow_up=true` with patch (budget slot partly filled); `_apply_follow_up_patch` (L310-L321).
  - `assess_delta_requirements` (L1157-L1226): sees price constraint; outputs `[BackendFunction.SEARCH_PRODUCTS]`.

Anthropic call log (Turn 2)
- classify_follow_up → `anthropic.messages.create` with tool `classify_follow_up`
  - Code: `shopping_bot/llm_service.py:L1126-L1133`
  - Purpose: Determine if the new message is a follow-up and emit a patch
  - Params:
    - messages[0].content: `FOLLOW_UP_PROMPT_TEMPLATE` with `last_snapshot`, `current_slots`, `query`
    - tools: `[FOLLOW_UP_TOOL]`, tool_choice: `classify_follow_up`
    - temperature: 0.1, max_tokens: 300
  - Result: tool_use → `{is_follow_up, reason?, patch:{slots, intent_override?, reset_context?}}`

- assess_delta_requirements → `anthropic.messages.create` with tool `assess_delta_requirements`
  - Code: `shopping_bot/llm_service.py:L1187-L1194`
  - Purpose: Given follow-up patch + context, list only backend fetchers to run
  - Params:
    - messages[0].content: `DELTA_ASSESS_PROMPT` with `intent_l3`, `current_text`, `canonical_query`, `last_params`, `fetched_keys`, `patch`, `detected_signals`
    - tools: `[DELTA_ASSESS_TOOL]`, tool_choice: `assess_delta_requirements`
    - temperature: 0.1, max_tokens: 300
  - Result: tool_use → `{fetch_functions:["search_products", ...]}` → parsed to `List[BackendFunction]`

Step 4: Build ES params and fetch
- For follow-up delta, core iterates fetchers (L151-L173) → `get_fetcher(SEARCH_PRODUCTS)` → `data_fetchers/es_products.search_products_handler` (L1103-L1135).
  - Build params:
    - Defaults: `_extract_defaults_from_context` (L845-L928) → includes `q` from `assessment.original_query`, budget parsed → `price_max≈200`.
    - LLM normalization: `LLMService.extract_es_params` → `RecommendationService.extract_search_params` (L319-L655) merges constraints; drops noise; may set category_group and cat_path; normalizes dietary terms if present.
      - Anthropic calls inside Recommendation:
        - extract_current_constraints (current turn) → `anthropic.messages.create` (L697-L704)
          - Purpose: Parse brands, dietary_terms/labels, keywords, price_min/max from current text
          - Params: tool `extract_current_constraints`, temperature 0, max_tokens 250
          - Result: `{brands[], dietary_terms[], dietary_labels[], health_claims[], keywords[], price_min?, price_max?}` with normalizations (L709-L727)
        - construct_search_query → (L1100-L1107)
          - Purpose: Build concise search phrase from context + current constraints
          - Params: tool `construct_search_query`, temperature 0, max_tokens 200
          - Result: `{query}`; fallback if invalid (L1112-L1139)
        - extract_category_signals → (L1490-L1497)
          - Purpose: Map query to taxonomy and extract cat paths, brands, price, dietary, keywords
          - Params: tool `extract_category_signals`, temperature 0, max_tokens 300
          - Result: `{l1,l2,l3,cat_path|cat_paths,brands[],price_min,max, dietary_labels[], health_claims[], keywords[]}` (L1498-L1517)
        - emit_es_params → (L735-L742)
          - Purpose: Emit normalized ES params skeleton from context
          - Params: tool `emit_es_params`, temperature 0, max_tokens 400
          - Result: `{q,size,category_group,brands[],dietary_terms[],price_min,max,...}` (L744-L758)
        - normalise_es_params → (L847-L854)
          - Purpose: Final normalization (currency, ranges, uppercase dietary, clamps, carry-over guards)
          - Params: tool `normalise_es_params`, temperature 0, max_tokens 400
          - Result: normalized params merged with heuristics (L855-L997)
        - fb_category_classify (optional) → (L1216-L1222)
          - Purpose: Classify into F&B taxonomy (category/subcategory)
          - Params: tool `fb_category_classify`, temperature 0, max_tokens 200
          - Result: `{is_fnb, category, subcategory}`
    - Final merge and heuristics: `_normalize_params` (L930-L963) + (L1017-L1064).
  - ES query: `_build_enhanced_es_query` (L146-L498) + function_score from `scoring_config.build_function_score_functions` (L221-L280); execute via `ElasticsearchProductsFetcher.search` (L649-L694).
  - Transform: `_transform_results` (L500-L593) → stores into `ctx.fetched_data["search_products"]` and Redis.

Step 5: Product answer and UX
- Back in core `_complete_assessment` (L507-L681): session has `product_intent="show_me_options"`.
  - `LLMService.generate_response` (L703-L727) → `_generate_product_response` (L737-L1021):
    - Uses ES products (top 10) and enriches top-3 via `_mget` (L767-L791) for persuasion briefs.
    - Output: `{ response_type:"final_answer", summary_message, product_ids (MPM), product_intent:"show_me_options" }`
  - `generate_ux_response_for_intent` (L447-L509): enforces MPM, adds DPL + quick replies (budget-aware).
  - Envelope: `fe_payload.build_envelope` (L194-L229) → `response_type="final_answer"` with MPM `ux_response`.

Anthropic call log (Turn 2 – product response)
- generate_product_response → `anthropic.messages.create` with tool `generate_product_response`
  - Code: `shopping_bot/llm_service.py:L887-L894`
  - Purpose: Produce structured product answer (summary_message, products or product_ids, optional hero)
  - Params:
    - messages[0].content: `PRODUCT_RESPONSE_PROMPT` with `query`, `intent_l3`, `session`, `permanent`, `products_json`(top-K), `enriched_top` (briefs)
    - tools: `[PRODUCT_RESPONSE_TOOL]`, tool_choice: `generate_product_response`
    - temperature: 0.7, max_tokens: 800 (or 1500 for SPM)
  - Result: tool_use → normalized via `_strip_keys`; MPM path further prunes to `product_ids` (L971-L1015)

- generate_ux_response → `anthropic.messages.create` with tool `generate_ux_response`
  - Code: `shopping_bot/ux_response_generator.py:L203-L210`
  - Purpose: Generate DPL, surface (enforced), and quick replies
  - Params:
    - messages[0].content: `_build_ux_prompt(...)` with `{intent, previous_answer, product_count, user_session subset, enriched_top}`
    - tools: `[UX_GENERATION_TOOL]`, tool_choice: `generate_ux_response`
    - temperature: 0.7, max_tokens: 500
  - Result: tool_use → `{dpl_runtime_text, ux_surface, quick_replies}`; surface enforced SPM/MPM (L221-L228)

Follow-up 2: "no palm oil"

Step 6: Follow-up delta and refined search
- `_handle_follow_up` detects follow-up → `assess_delta_requirements` (L1157-L1226) extracts dietary signal; includes `SEARCH_PRODUCTS`.
- `Recommendation.extract_search_params` (L319-L655) normalizes dietary to `PALM OIL FREE` and merges with params; brand carry-over dropped for generic modifiers.
- ES search and generation repeat; summary and product_ids updated; UX QR may show “Cleaner only / Less oil”.

Anthropic call log (Turn 3)
- assess_delta_requirements (same as Turn 2)
- Recommendation pipeline repeats with `dietary_terms` normalization (extract_current_constraints + normalise)
- generate_product_response + generate_ux_response repeat with updated context

Result: User receives MPM response with product_ids and DPL; quick replies include price/quality pivots. The session stores `last_recommendation` for future turns (L695-L740).

---

### Scenario B: SPM – Single product validation

- Base query: "is this Veeba ketchup good?"
- Follow-up 1: "show healthier"

Step 0: Ingress and context
- Same route handling as Scenario A; session initialized; `current_user_text` stored.

Step 1: Core orchestration – new assessment
- `_start_new_assessment` (L333-L430)
  - `classify_intent` → `layer3="Specific_Product_Search"`, product-related.
  - 4-intent classification → `intent="is_this_good"`.
  - Assessment built; user slots likely minimal; proceeds to fetch.

Anthropic call log (Turn 1)
- classify_intent (as in Scenario A Turn 1)
- classify_product_intent → expects `is_this_good` intent; temperature 0.1 (L678-L685)
- assess_requirements (often minimal asks for SPM) (L1252-L1259)

Step 2: ES params for SPM
- `search_products_handler` (L1103-L1135):
  - Defaults (L845-L928) + normalizer; SPM upgrade (L1017-L1031): increase `size` to 5 and enforce brand filtering when hints present.
  - ES search; `_transform_results` returns top products.

Anthropic calls inside Recommendation (if invoked by `LLMService.extract_es_params`): same set as Scenario A Turn 2 (construct_search_query, extract_category_signals, emit_es_params, normalise_es_params, optional fb_category_classify; plus extract_current_constraints)

Step 3: Product answer (SPM clamp) and enrichment
- `LLMService._generate_product_response` (L737-L1021):
  - For SPM: `products_data = top1`, `top_k=1` enrichment via `_mget` (L767-L791).
  - Enforces single product in output (L903-L961) and propagates a real ES `product_id`.
  - Output includes `summary_message`, one product, `product_intent="is_this_good"`.

Anthropic call log (Turn 1 – product response)
- generate_product_response → temperature up to 1500 max_tokens for SPM (L893-L894); tool `generate_product_response`
  - Result: One product enforced; ensures product_id exists; may attach `top_products_brief` (L969-L970)
- generate_ux_response → surface enforced to SPM (L221-L228), returns DPL + QRs

Step 4: UX SPM and envelope
- `generate_ux_response_for_intent` (L447-L509) → enforced SPM surface (L221-L228), adds DPL and QR (e.g., "Compare similar", "Healthier options").
- Envelope via `fe_payload` yields `final_answer` with a single product and SPM UX.

Follow-up 1: "show healthier"

Step 5: Follow-up reclass and fallback to MPM
- `_handle_follow_up` (L95-L173): reclassifies/patches product intent to exploration (e.g., "show_me_options").
- `assess_delta_requirements` includes `SEARCH_PRODUCTS` with `min_flean_percentile` boost (heuristics in params builder L1056-L1064).
- New MPM response generated with “cleaner” options and suitable QR.

Anthropic call log (Turn 2)
- classify_follow_up + assess_delta_requirements (as in Scenario A Turn 2)
- Recommendation pipeline applies dietary/quality signals
- generate_product_response + generate_ux_response produce MPM

Result: The conversation pivots from SPM to MPM with cleaner/healthier alternatives; state persisted in Redis.

---

### Diagram

See `docs/diagrams/trace-flow.mmd` for a sequence-style view of the above traces.

### Verification Snippets

- MPM turn 1 (ask):
  - `shopping_bot/bot_core.py:L453-L477` returns QUESTION
  - Envelope normalization: `shopping_bot/fe_payload.py:L72-L101`

- MPM delta → ES:
  - Delta assess: `shopping_bot/llm_service.py:L1157-L1226`
  - ES query builder: `shopping_bot/data_fetchers/es_products.py:L146-L498`

- SPM clamp:
  - One product: `shopping_bot/llm_service.py:L903-L961`
  - UX SPM: `shopping_bot/ux_response_generator.py:L221-L228`

### Anthropic Call Summary (Cheat Sheet)

| Turn/Phase | Call (function) | Purpose | Key Inputs (prompt/tool) | Outputs (schema) | Code anchor |
| --- | --- | --- | --- | --- | --- |
| A: Turn 1 | classify_intent (`LLMService.classify_intent`) | Map query to L1/L2/L3; detect product-related | Prompt: INTENT_CLASSIFICATION_PROMPT; tool: classify_intent; temp 0.2; max 150 | {layer1, layer2, layer3, is_product_related} | `shopping_bot/llm_service.py:L637-L644`, L646-L659 |
| A: Turn 1 | classify_product_intent (`LLMService.classify_product_intent`) | Pick 1 of 4 product intents | Prompt: PRODUCT_INTENT_CLASSIFICATION_PROMPT; tool: classify_product_intent; temp 0.1; max 200 | {intent, confidence, reasoning} | `shopping_bot/llm_service.py:L678-L685`, L687-L700 |
| A: Turn 1 | assess_requirements (`LLMService.assess_requirements`) | Decide ASK_* and FETCH_* | Prompt: REQUIREMENTS_ASSESSMENT_PROMPT; tool: assess_requirements; temp 0.1; max 500 | {missing_data:[{function,rationale}], priority_order:[]} | `shopping_bot/llm_service.py:L1252-L1259`, L1261-L1299 |
| A: Turn 2 | classify_follow_up (`LLMService.classify_follow_up`) | Is this a follow-up? Patch deltas | Prompt: FOLLOW_UP_PROMPT_TEMPLATE; tool: classify_follow_up; temp 0.1; max 300 | {is_follow_up, reason?, patch:{slots, intent_override?, reset_context?}} | `shopping_bot/llm_service.py:L1126-L1133`, L1134-L1155 |
| A: Turn 2 | assess_delta_requirements (`LLMService.assess_delta_requirements`) | List only backend fetchers needed | Prompt: DELTA_ASSESS_PROMPT (+ last_params, signals); tool: assess_delta_requirements; temp 0.1; max 300 | {fetch_functions:[...]} → [BackendFunction] | `shopping_bot/llm_service.py:L1187-L1194`, L1195-L1226 |
| A/B: Any | extract_current_constraints (`Recommendation._extract_constraints_from_text`) | Parse brands/dietary/keywords/price from current text | Tool: extract_current_constraints; temp 0; max 250 | {brands[], dietary_terms[], dietary_labels[], health_claims[], keywords[], price_min?, price_max?} | `shopping_bot/recommendation.py:L697-L704`, L705-L730 |
| A/B: Any | construct_search_query (`Recommendation._construct_search_query`) | Build concise ES `q` from context | Tool: construct_search_query; temp 0; max 200 | {query} | `shopping_bot/recommendation.py:L1100-L1107`, L1108-L1139 |
| A/B: Any | extract_category_signals (`Recommendation._extract_category_and_signals`) | Map to taxonomy; signals | Tool: extract_category_signals; temp 0; max 300 | {l1,l2,l3,cat_path|cat_paths,brands,price_min,max,dietary_labels,health_claims,keywords} | `shopping_bot/recommendation.py:L1490-L1497`, L1498-L1517 |
| A/B: Any | emit_es_params (`Recommendation._call_anthropic_for_params`) | Emit normalized ES params skeleton | Tool: emit_es_params; temp 0; max 400 | ES params dict | `shopping_bot/recommendation.py:L735-L742`, L744-L758 |
| A/B: Any | normalise_es_params (`Recommendation._normalise_es_params`) | Normalize/carry-over guards/clamps | Tool: normalise_es_params; temp 0; max 400 | normalized params | `shopping_bot/recommendation.py:L847-L854`, L855-L997 |
| A/B: Optional | fb_category_classify (`Recommendation._fb_category_classify`) | F&B category/subcategory | Tool: fb_category_classify; temp 0; max 200 | {is_fnb, category, subcategory} | `shopping_bot/recommendation.py:L1216-L1222`, L1223-L1232 |
| A: Turn 2 / B: Turn 1 | generate_product_response (`LLMService._generate_product_response`) | Build structured answer | Prompt: PRODUCT_RESPONSE_PROMPT with products + briefs; tool: generate_product_response; temp 0.7; max 800/1500 | {response_type, summary_message, products[] or product_ids[], hero?} | `shopping_bot/llm_service.py:L887-L894`, L896-L1016 |
| A: Turn 2 / B: Turn 1 | generate_ux_response (`UXResponseGenerator._call_llm_for_ux`) | DPL + surface + QRs | Prompt: _build_ux_prompt; tool: generate_ux_response; temp 0.7; max 500 | {dpl_runtime_text, ux_surface, quick_replies} | `shopping_bot/ux_response_generator.py:L203-L210`, L212-L229 |

### Anthropic Call Summary – MPM Scenario (Example: "show me protein bars" → "under 200" → "no palm oil")

| Step | User text | Call (function → tool) | Key reasoning (why this call) | Inputs (model/temp/max_tokens; prompt summary; tool_choice) | Context passed | Outputs (shape) | Downstream effect | Code anchor |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 (Turn 1) | show me protein bars | classify_intent → classify_intent | Detect L3 intent and whether to enter product pipeline | model=Cfg.LLM_MODEL; temp=0.2; max=150; INTENT_CLASSIFICATION_PROMPT; tool_choice: classify_intent | Recent session history snapshot | {layer1, layer2, layer3, is_product_related} | Sets `intent_l3=Product_Discovery`, enables 4-intent | `shopping_bot/llm_service.py:L637-L644`, L646-L659 |
| 2 (Turn 1) | show me protein bars | classify_product_intent → classify_product_intent | Choose one of 4 product intents for product UX path | temp=0.1; max=200; PRODUCT_INTENT_CLASSIFICATION_PROMPT; tool_choice: classify_product_intent | Minimal session summary (last rec, fetched keys, history) | {intent, confidence, reasoning} | Sets `product_intent=show_me_options` | `shopping_bot/llm_service.py:L678-L685`, L687-L700 |
| 3 (Turn 1) | show me protein bars | assess_requirements → assess_requirements | Decide ASK_* vs FETCH_* before searching | temp=0.1; max=500; REQUIREMENTS_ASSESSMENT_PROMPT with session keys | Session keys (perm, session, fetched) | {missing_data:[{function,rationale}], priority_order:[]} | Returns QUESTION (ask budget first) | `shopping_bot/llm_service.py:L1252-L1259`, L1261-L1299 |
| 4 (Turn 2) | under 200 | classify_follow_up → classify_follow_up | Decide if this narrows previous intent and produce patch | temp=0.1; max=300; FOLLOW_UP_PROMPT_TEMPLATE | Last snapshot + current slots + new text | {is_follow_up, patch:{slots,...}} | Budget captured; patch applied | `shopping_bot/llm_service.py:L1126-L1133`, L1134-L1155 |
| 5 (Turn 2) | under 200 | assess_delta_requirements → assess_delta_requirements | Decide which fetchers to run after the delta | temp=0.1; max=300; DELTA_ASSESS_PROMPT with last_params, signals | canonical_query, last_params, detected_signals | {fetch_functions:["search_products"...]} | Triggers ES search only (no more questions) | `shopping_bot/llm_service.py:L1187-L1194`, L1195-L1226 |
| 6 (Turn 2) | under 200 | extract_current_constraints | Parse explicit constraints from current text | temp=0; max=250; extract_current_constraints | current_user_text | {price_min?, price_max?, brands[], dietary_terms[], keywords[]} | Seed ES params (e.g., price_max=200) | `shopping_bot/recommendation.py:L697-L704`, L705-L730 |
| 7 (Turn 2) | under 200 | construct_search_query | Compose concise ES `q` term | temp=0; max=200; construct_search_query | last category/subcategory hints, last products, session answers | {query} | Stable, concise `q` for ES | `shopping_bot/recommendation.py:L1100-L1107`, L1108-L1139 |
| 8 (Turn 2) | under 200 | extract_category_signals | Align query to taxonomy, collect brand/dietary/keywords | temp=0; max=300; extract_category_signals | taxonomy + user_query | {l1,l2,l3,cat_path(s), brands, price*, dietary_labels, keywords} | Build cat_path(s), normalize signals | `shopping_bot/recommendation.py:L1490-L1497`, L1498-L1517 |
| 9 (Turn 2) | under 200 | emit_es_params | Emit normalized ES param skeleton | temp=0; max=400; emit_es_params | full session context (constructed_q, answers, debug hints) | ES params dict | Provides base fields for normalization | `shopping_bot/recommendation.py:L735-L742`, L744-L758 |
| 10 (Turn 2) | under 200 | normalise_es_params | Final normalize (clamps, currency, brand carry-over guard) | temp=0; max=400; normalise_es_params | current constraints, convo history (last 2), last_search_params | normalized params | Safe, queryable ES params; carries `product_intent` | `shopping_bot/recommendation.py:L847-L854`, L855-L997 |
| 11 (Turn 2 opt.) | under 200 | fb_category_classify | F&B classification if ambiguous | temp=0; max=200; fb_category_classify | constructed_q + taxonomy | {is_fnb, category, subcategory} | Builds `fb_category/subcategory` → improves cat_path | `shopping_bot/recommendation.py:L1216-L1222`, L1223-L1232 |
| 12 (Turn 2) | under 200 | generate_product_response → generate_product_response | Turn ES hits (+briefs) into structured answer for MPM | temp=0.7; max=800; PRODUCT_RESPONSE_PROMPT; tool_choice: generate_product_response | session, permanent, products_json (≤10), enriched_top (top-3 briefs) | {response_type, summary_message, product_ids[], hero?} | Provides MPM ids; no per-product text in MPM | `shopping_bot/llm_service.py:L887-L894`, L896-L1016 |
| 13 (Turn 2) | under 200 | generate_ux_response → generate_ux_response | Add DPL, enforce MPM surface, contextual QRs | temp=0.7; max=500; _build_ux_prompt; tool_choice: generate_ux_response | previous_answer + enriched_top + session subset + product_count | {dpl_runtime_text, ux_surface, quick_replies} | UX payload for FE envelope | `shopping_bot/ux_response_generator.py:L203-L210`, L212-L229 |
| 14 (Turn 3) | no palm oil | (repeat 4–13) | Apply dietary normalization and rerank | Same as above with updated constraints | current text adds dietary_terms → PALM OIL FREE | Updated params, ids, DPL, QRs | Produces refined MPM | anchors as above |

### Anthropic Call Summary – SPM Scenario (Example: "is this Veeba ketchup good?" → "show healthier")

| Step | User text | Call (function → tool) | Key reasoning (why this call) | Inputs (model/temp/max_tokens; prompt summary; tool_choice) | Context passed | Outputs (shape) | Downstream effect | Code anchor |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 (Turn 1) | is this Veeba ketchup good? | classify_intent → classify_intent | Detect L3 and product pipeline eligibility | temp=0.2; max=150; INTENT_CLASSIFICATION_PROMPT | minimal recent context | {layer1, layer2, layer3, is_product_related} | L3 often `Specific_Product_Search` | `shopping_bot/llm_service.py:L637-L644`, L646-L659 |
| 2 (Turn 1) | is this Veeba ketchup good? | classify_product_intent → classify_product_intent | Determine SPM intent | temp=0.1; max=200; PRODUCT_INTENT_CLASSIFICATION_PROMPT | session snapshot | {intent, confidence} ≈ is_this_good | Sets SPM pathway and clamp rules | `shopping_bot/llm_service.py:L678-L685`, L687-L700 |
| 3 (Turn 1) | is this Veeba ketchup good? | assess_requirements → assess_requirements | Confirm minimal asks; typically no ASK_* for SPM | temp=0.1; max=500 | session keys | {missing_data, priority_order} (small/empty) | Proceeds to fetch without user slot asks | `shopping_bot/llm_service.py:L1252-L1259`, L1261-L1299 |
| 4 (Turn 1) | is this Veeba ketchup good? | extract_current_constraints | Extract brand from text (Veeba) | temp=0; max=250 | current_user_text | {brands:["Veeba"], keywords...} | Seeds brand into ES params | `shopping_bot/recommendation.py:L697-L704`, L705-L730 |
| 5 (Turn 1) | is this Veeba ketchup good? | construct_search_query | Compose concise `q` (e.g., "ketchup") | temp=0; max=200 | last context, session answers | {query} | Stable ES q for SPM | `shopping_bot/recommendation.py:L1100-L1107`, L1108-L1139 |
| 6 (Turn 1) | is this Veeba ketchup good? | extract_category_signals | Map to ketchup category path(s) | temp=0; max=300 | taxonomy + query | {cat_path(s), brands?, dietary_labels?} | Builds f_and_b/food/... path | `shopping_bot/recommendation.py:L1490-L1497`, L1498-L1517 |
| 7 (Turn 1) | is this Veeba ketchup good? | emit_es_params | Emit base ES params | temp=0; max=400 | constructed_q + session context | ES params dict | Provides size, filters | `shopping_bot/recommendation.py:L735-L742`, L744-L758 |
| 8 (Turn 1) | is this Veeba ketchup good? | normalise_es_params | Final normalize; SPM brand carry-over allowed | temp=0; max=400 | current constraints + last params + history | normalized params (size bumped to ≥5 downstream; enforce_brand flag later) | Leads to ES fetch with brand focus | `shopping_bot/recommendation.py:L847-L854`, L855-L997 |
| 9 (Turn 1) | is this Veeba ketchup good? | generate_product_response → generate_product_response | Produce single-product answer with real product_id | temp=0.7; max=1500; PRODUCT_RESPONSE_PROMPT (SPM) | products_json (top-5), enriched_top (top-1 brief) | {response_type, summary_message, products:[1], product_ids:[1]} | SPM clamp to 1 item; id propagation | `shopping_bot/llm_service.py:L887-L894`, L896-L916, L903-L961 |
| 10 (Turn 1) | is this Veeba ketchup good? | generate_ux_response → generate_ux_response | Add SPM DPL and QRs (compare, healthier, cheaper) | temp=0.7; max=500; _build_ux_prompt | previous_answer + enriched_top + session subset | {dpl_runtime_text, ux_surface=SPM, quick_replies} | UX SPM payload | `shopping_bot/ux_response_generator.py:L203-L210`, L221-L228 |
| 11 (Turn 2) | show healthier | classify_follow_up + assess_delta_requirements | Pivot to MPM with higher quality threshold | temp=0.1 (each); DELTA_ASSESS_PROMPT uses detected_signals | last_params + current_text | {fetch_functions:[search_products]} | Raises `min_flean_percentile` in params; switches to MPM | `shopping_bot/llm_service.py:L1187-L1194`, L1195-L1226; `shopping_bot/llm_service.py:L1169-L1175` |
| 12 (Turn 2) | show healthier | Recommendation calls | Normalize to add dietary/quality hints | temp=0 across tools | current constraints detect “healthier” | params include `min_flean_percentile ≥ 50` | MPM answer generated | anchors as above |

