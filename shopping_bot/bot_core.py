"""
Brain of the WhatsApp shopping bot – baseline core.

UPDATED: Integrated 4-Intent Classification System
─────────────────────────────────────────────────
• Step 1: Modified existing LLM classification to detect product-related queries
• Step 2: Added 4-intent classification for serious product queries
• Step 3: Integrated UX response generation for classified intents
• Fallback: Non-4-intent queries use existing casual/generic flow
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Union

from .bot_helpers import (build_question, compute_still_missing,
                          get_func_value, is_user_slot, snapshot_and_trim,
                          store_user_answer)
from .config import get_config
from .data_fetchers import get_fetcher
from .enums import BackendFunction, ResponseType, UserSlot
from .llm_service import LLMService, map_leaf_to_query_intent
from .models import BotResponse, UserContext
from .redis_manager import RedisContextManager
from .utils.helpers import safe_get
from .utils.smart_logger import get_smart_logger
from .ux_response_generator import generate_ux_response_for_intent

Cfg = get_config()
log = logging.getLogger(__name__)

# Intents that should return structured product responses
PRODUCT_INTENTS = {
    "product_discovery", "recommendation", 
    "specific_product_search", "product_comparison"
}

# NEW: 4-Intent mapping for serious product queries
SERIOUS_PRODUCT_INTENTS = {
    "Product_Discovery", "Recommendation", 
    "Specific_Product_Search", "Product_Comparison"
}


class ShoppingBotCore:
    def __init__(self, context_mgr: RedisContextManager) -> None:
        self.ctx_mgr = context_mgr
        self.llm_service = LLMService()
        self.smart_log = get_smart_logger("bot_core")

    # ────────────────────────────────────────────────────────
    # Public entry point
    # ────────────────────────────────────────────────────────
    async def process_query(self, query: str, ctx: UserContext) -> BotResponse:
        self.smart_log.query_start(ctx.user_id, query, bool(ctx.session))

        try:
            # 1) Continue existing assessment
            if "assessment" in ctx.session:
                self.smart_log.flow_decision(ctx.user_id, "CONTINUE_ASSESSMENT")
                return await self._continue_assessment(query, ctx)

            # 2) Classify as follow-up
            fu = await self.llm_service.classify_follow_up(query, ctx)
            if fu.is_follow_up and not fu.patch.reset_context:
                effective_l3 = fu.patch.intent_override or ctx.session.get("intent_l3", "")
                self.smart_log.follow_up_decision(
                    ctx.user_id, "HANDLE_FOLLOW_UP", effective_l3, fu.reason
                )
                self._apply_follow_up_patch(fu.patch, ctx)
                return await self._handle_follow_up(query, ctx, fu)

            # 3) New or reset
            if fu.patch.reset_context:
                self.smart_log.flow_decision(ctx.user_id, "RESET_CONTEXT")
                self._reset_session_only(ctx)
            else:
                self.smart_log.flow_decision(ctx.user_id, "NEW_ASSESSMENT")

            return await self._start_new_assessment(query, ctx)

        except Exception as exc:
            self.smart_log.error_occurred(
                ctx.user_id, type(exc).__name__, "process_query", str(exc)
            )
            return BotResponse(
                ResponseType.ERROR,
                {"message": "Sorry, something went wrong.", "error": str(exc)},
            )

    # ────────────────────────────────────────────────────────
    # Follow-up path (UPDATED for 4-intent support)
    # ────────────────────────────────────────────────────────
    async def _handle_follow_up(self, query: str, ctx: UserContext, fu) -> BotResponse:
        # Determine effective L3
        effective_l3 = (
            fu.patch.intent_override
            or ctx.session.get("intent_l3")
            or ctx.session.get("intent_override")
            or ""
        )

        # Defer if Recommendation and async enabled
        if effective_l3 == "Recommendation" and Cfg.ENABLE_ASYNC:
            ctx.session["needs_background"] = True
            a = ctx.session.get("assessment") or {
                "original_query": query,
                "intent": "Recommendation",
                "missing_data": [],
                "priority_order": [],
                "fulfilled": [],
                "currently_asking": None,
            }
            ctx.session["assessment"] = a
            a["phase"] = "active"
            self.ctx_mgr.save_context(ctx)
            self.smart_log.flow_decision(
                ctx.user_id, "DEFER_TO_BACKGROUND_FOLLOW_UP", {"intent_l3": effective_l3}
            )
            return BotResponse(
                ResponseType.PROCESSING_STUB,
                content={"message": "Processing your request…"},
            )

        # Opportunistic upgrade: if previous L3 was non-product but current text is product-like,
        # reclassify intent for this follow-up so we can engage 4-intent flow.
        if effective_l3 not in SERIOUS_PRODUCT_INTENTS:
            try:
                reclass = await self.llm_service.classify_intent(query, ctx)
                if reclass.is_product_related and reclass.layer3 in SERIOUS_PRODUCT_INTENTS:
                    effective_l3 = reclass.layer3
                    ctx.session.update(
                        intent_l1=reclass.layer1,
                        intent_l2=reclass.layer2,
                        intent_l3=reclass.layer3,
                        is_product_related=reclass.is_product_related,
                    )
                    self.smart_log.intent_classified(
                        ctx.user_id, (reclass.layer1, reclass.layer2, reclass.layer3), map_leaf_to_query_intent(reclass.layer3).value
                    )
                    self.ctx_mgr.save_context(ctx)
            except Exception:
                pass

        # Delta-fetch-and-reply
        fetch_list = await self.llm_service.assess_delta_requirements(query, ctx, fu.patch)
        if fetch_list:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetch_list])

        fetched: Dict[str, Any] = {}
        success_count = 0
        for func in fetch_list:
            try:
                result = await get_fetcher(func)(ctx)
                fetched[func.value] = result
                ctx.fetched_data[func.value] = {
                    "data": result,
                    "timestamp": datetime.now().isoformat(),
                }
                success_count += 1
                self.smart_log.performance_metric(
                    ctx.user_id, func.value, data_size=len(str(result)) if result else 0
                )
            except Exception as exc:
                self.smart_log.warning(ctx.user_id, "DATA_FETCH_FAILED", f"{func.value}: {exc}")
                fetched[func.value] = {"error": str(exc)}

        if fetch_list:
            self.smart_log.data_operations(
                ctx.user_id, [f.value for f in fetch_list], success_count
            )

        # NEW: Check if this is a serious product query for 4-intent processing
        if effective_l3 in SERIOUS_PRODUCT_INTENTS:
            log.info(f"4INTENT_FOLLOWUP_CHECK | user={ctx.user_id} | intent_l3={effective_l3}")
            
            # Classify into 4-intent system
            product_intent_result = await self.llm_service.classify_product_intent(query, ctx)
            
            if product_intent_result.confidence > 0.3:  # Relaxed confidence threshold to improve UX coverage
                log.info(f"4INTENT_CLASSIFIED | user={ctx.user_id} | intent={product_intent_result.intent} | confidence={product_intent_result.confidence}")
                
                # Generate base answer
                answer_dict = await self.llm_service.generate_response(
                    query,
                    ctx,
                    fetched,
                    intent_l3=effective_l3,
                    query_intent=map_leaf_to_query_intent(effective_l3),
                    product_intent=product_intent_result.intent
                )
                
                # Generate UX-ready response
                ux_enhanced_answer = await generate_ux_response_for_intent(
                    intent=product_intent_result.intent,
                    previous_answer=answer_dict,
                    ctx=ctx,
                    user_query=query
                )
                
                resp_type = ResponseType(ux_enhanced_answer.get("response_type", "final_answer"))
                
                # Store snapshot for follow-ups
                self._store_last_recommendation(query, ctx, fetched)
                
                # Final-answer summary for memory
                final_answer_summary = {
                    "response_type": resp_type.value,
                    "message_preview": str(ux_enhanced_answer.get("summary_message", ux_enhanced_answer.get("message", "")))[:300],
                    "has_sections": False,
                    "has_products": bool(ux_enhanced_answer.get("products")),
                    "flow_triggered": False,
                    "ux_intent": product_intent_result.intent
                }
                
                snapshot_and_trim(ctx, base_query=query, final_answer=final_answer_summary)
                self.ctx_mgr.save_context(ctx)
                self.smart_log.response_generated(ctx.user_id, resp_type.value, False)
                
                return BotResponse(
                    resp_type,
                    content=ux_enhanced_answer,
                    functions_executed=list(fetched.keys()),
                )

        # Heuristic fallback: if product-like follow-up and we have products, attach UX with default intent
        try:
            def _has_products(data: Dict[str, Any]) -> bool:
                if not isinstance(data, dict):
                    return False
                block = data.get("search_products")
                if isinstance(block, dict):
                    payload = block.get("data", block)
                    return bool(isinstance(payload, dict) and payload.get("products"))
                return False
            if (effective_l3 in SERIOUS_PRODUCT_INTENTS) and _has_products(fetched):
                fallback_intent = "show_me_options"
                log.info(f"4INTENT_FALLBACK | user={ctx.user_id} | intent={fallback_intent}")
                base = await self.llm_service.generate_response(
                    query,
                    ctx,
                    fetched,
                    intent_l3=effective_l3,
                    query_intent=map_leaf_to_query_intent(effective_l3),
                    product_intent=fallback_intent,
                )
                ux = await generate_ux_response_for_intent(
                    intent=fallback_intent,
                    previous_answer=base,
                    ctx=ctx,
                    user_query=query,
                )
                resp_type = ResponseType(ux.get("response_type", "final_answer"))
                self._store_last_recommendation(query, ctx, fetched)
                snapshot_and_trim(
                    ctx,
                    base_query=query,
                    final_answer={
                        "response_type": resp_type.value,
                        "message_preview": str(ux.get("summary_message", ux.get("message", "")))[:300],
                        "has_sections": False,
                        "has_products": bool(ux.get("products")),
                        "flow_triggered": False,
                        "ux_intent": fallback_intent,
                    },
                )
                self.ctx_mgr.save_context(ctx)
                self.smart_log.response_generated(ctx.user_id, resp_type.value, False)
                return BotResponse(resp_type, content=ux, functions_executed=list(fetched.keys()))
        except Exception:
            pass

        # Default: Generate unified response (existing flow)
        answer_dict = await self.llm_service.generate_response(
            query,
            ctx,
            fetched,
            intent_l3=effective_l3,
            query_intent=map_leaf_to_query_intent(effective_l3),
        )

        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))

        # Store snapshot for follow-ups
        self._store_last_recommendation(query, ctx, fetched)

        # Final-answer summary for memory
        final_answer_summary = {
            "response_type": resp_type.value,
            "message_preview": str(answer_dict.get("summary_message", answer_dict.get("message", "")))[:300],
            "has_sections": False,
            "has_products": bool(answer_dict.get("products")),
            "flow_triggered": False,
        }

        snapshot_and_trim(ctx, base_query=query, final_answer=final_answer_summary)
        self.ctx_mgr.save_context(ctx)
        self.smart_log.response_generated(ctx.user_id, resp_type.value, False)

        return BotResponse(
            resp_type,
            content=answer_dict,
            functions_executed=list(fetched.keys()),
        )

    # ────────────────────────────────────────────────────────
    # Session helpers
    # ────────────────────────────────────────────────────────
    def _apply_follow_up_patch(self, patch, ctx: UserContext) -> None:
        changes = {}
        for k, v in patch.slots.items():
            old_value = ctx.session.get(k)
            ctx.session[k] = v
            changes[k] = f"{old_value}→{v}"
        if patch.intent_override:
            ctx.session["intent_override"] = patch.intent_override
            changes["intent"] = patch.intent_override
        if changes:
            self.smart_log.context_change(ctx.user_id, "PATCH_APPLIED", changes)

    def _reset_session_only(self, ctx: UserContext) -> None:
        cleared_items = len(ctx.session) + len(ctx.fetched_data)
        ctx.session.clear()
        ctx.fetched_data.clear()
        self.smart_log.context_change(
            ctx.user_id, "SESSION_RESET", {"cleared_items": cleared_items}
        )

    # ────────────────────────────────────────────────────────
    # NEW: Enhanced new assessment with 4-intent support
    # ────────────────────────────────────────────────────────
    async def _start_new_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        # STEP 1: Classify intent with product detection
        result = await self.llm_service.classify_intent(query, ctx)
        intent = map_leaf_to_query_intent(result.layer3)

        self.smart_log.intent_classified(
            ctx.user_id, (result.layer1, result.layer2, result.layer3), intent.value
        )

        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
            is_product_related=result.is_product_related,
        )

        # STEP 2: If serious product query, classify into 4-intent system
        product_intent = None
        if result.is_product_related and result.layer3 in SERIOUS_PRODUCT_INTENTS:
            log.info(f"4INTENT_NEW_CHECK | user={ctx.user_id} | intent_l3={result.layer3}")
            
            product_intent_result = await self.llm_service.classify_product_intent(query, ctx)
            
            if product_intent_result.confidence > 0.3:  # Relaxed confidence threshold to improve UX coverage
                product_intent = product_intent_result.intent
                ctx.session["product_intent"] = product_intent
                log.info(f"4INTENT_NEW_CLASSIFIED | user={ctx.user_id} | intent={product_intent} | confidence={product_intent_result.confidence}")

        needs_bg = self._needs_background(intent)
        ctx.session["needs_background"] = needs_bg
        self.smart_log.flow_decision(
            ctx.user_id,
            "BACKGROUND_DECISION",
            {"needs_background": needs_bg, "intent": intent.value, "intent_l3": result.layer3, "product_intent": product_intent},
        )

        assessment = await self.llm_service.assess_requirements(
            query, intent, result.layer3, ctx
        )

        # Enforce no-ask policy for specific product intents
        no_ask_intents = {"is_this_good", "which_is_better"}
        if (ctx.session.get("product_intent") in no_ask_intents) or (
            product_intent in no_ask_intents
        ):
            # Strip user slots from assessment
            assessment.missing_data = [f for f in assessment.missing_data if not is_user_slot(f)]
            assessment.priority_order = [f for f in assessment.priority_order if not is_user_slot(f)]

        # Remove PRODUCT_CATEGORY asks for product flows – taxonomy/ES infers this
        if result.is_product_related:
            assessment.missing_data = [f for f in assessment.missing_data if get_func_value(f) != UserSlot.PRODUCT_CATEGORY.value]
            assessment.priority_order = [f for f in assessment.priority_order if get_func_value(f) != UserSlot.PRODUCT_CATEGORY.value]

        user_slots = [f for f in assessment.missing_data if is_user_slot(f)]
        missing_data_names = [get_func_value(f) for f in assessment.missing_data]
        ask_first_names = [get_func_value(f) for f in user_slots]

        self.smart_log.requirements_assessed(
            ctx.user_id, missing_data_names, ask_first_names
        )

        if user_slots:
            contextual_questions = await self.llm_service.generate_contextual_questions(
                user_slots, query, result.layer3, ctx
            )
            ctx.session["contextual_questions"] = contextual_questions

        ctx.session["assessment"] = {
            "original_query": query,
            "intent": intent.value,
            "missing_data": missing_data_names,
            "priority_order": [get_func_value(f) for f in assessment.priority_order],
            "fulfilled": [],
            "currently_asking": None,
        }

        # Heuristic: ensure essential slots for product queries when model under-specifies
        try:
            if result.is_product_related:
                essentials: list[str] = []
                if "budget" not in ctx.session:
                    essentials.append(UserSlot.USER_BUDGET.value)
                # Do NOT add PRODUCT_CATEGORY here; category is inferred by taxonomy/ES
                if essentials:
                    a = ctx.session["assessment"]
                    # Prepend essentials if missing
                    for slot in reversed(essentials):
                        if slot not in a["priority_order"]:
                            a["priority_order"].insert(0, slot)
                        if slot not in a["missing_data"]:
                            a["missing_data"].insert(0, slot)
        except Exception:
            pass

        self.ctx_mgr.save_context(ctx)
        return await self._continue_assessment(query, ctx)

    # ────────────────────────────────────────────────────────
    # Continue assessment (UPDATED for 4-intent support)
    # ────────────────────────────────────────────────────────
    async def _continue_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        a = ctx.session["assessment"]

        if query != a["original_query"]:
            store_user_answer(query, a, ctx)
            self.smart_log.context_change(
                ctx.user_id, "USER_ANSWER_STORED",
                {"for": a.get("currently_asking"), "answer_len": len(query)}
            )
            try:
                log.info(
                    f"SLOT_ANSWER_RECEIVED | user={ctx.user_id} | slot={a.get('currently_asking')} | answer='{str(query)[:80]}'"
                )
                log.info(
                    f"ASSESSMENT_STATE | user={ctx.user_id} | base_query='{a.get('original_query','')}' | missing={a.get('missing_data', [])} | priority={a.get('priority_order', [])}"
                )
            except Exception:
                pass

        still_missing = compute_still_missing(a, ctx)
        ask_first = [f for f in still_missing if is_user_slot(f)]
        fetch_later = [f for f in still_missing if not is_user_slot(f)]

        # Enforce no-ask policy for specific product intents
        no_ask_intents = {"is_this_good", "which_is_better"}
        if ctx.session.get("product_intent") in no_ask_intents:
            ask_first = []

        # If any user slots remain, do not run product fetchers yet
        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value

            self.smart_log.user_question(ctx.user_id, func_value)
            self.ctx_mgr.save_context(ctx)
            q = build_question(func, ctx)
            # Include currently_asking so FE can render context like samples
            try:
                if isinstance(q, dict):
                    q["currently_asking"] = func_value
            except Exception:
                pass
            return BotResponse(ResponseType.QUESTION, q)
        
        # At this point, no user slots are pending; safe to fetch
        needs_bg = bool(ctx.session.get("needs_background")) and Cfg.ENABLE_ASYNC
        if fetch_later and needs_bg:
            a["phase"] = "processing"
            self.smart_log.flow_decision(
                ctx.user_id, "DEFER_TO_BACKGROUND",
                {"fetchers": [get_func_value(f) for f in fetch_later]}
            )
            self.ctx_mgr.save_context(ctx)
            return BotResponse(
                ResponseType.PROCESSING_STUB,
                content={"message": "Processing your request…"}
            )

        self.smart_log.flow_decision(
            ctx.user_id, "COMPLETE_ASSESSMENT", f"{len(fetch_later)} fetches needed"
        )
        try:
            log.info(
                f"ASSESSMENT_FINALIZE | user={ctx.user_id} | base_query='{a.get('original_query','')}' | ask_first={ask_first} | fetchers={[get_func_value(f) for f in fetch_later]}"
            )
        except Exception:
            pass
        return await self._complete_assessment(a, ctx, fetch_later)

    # ────────────────────────────────────────────────────────
    # UPDATED: Complete assessment with 4-intent support
    # ────────────────────────────────────────────────────────
    async def _complete_assessment(
        self,
        a: Dict[str, Any],
        ctx: UserContext,
        fetchers: List[Union[BackendFunction, UserSlot]],
    ) -> BotResponse:

        backend_fetchers = [f for f in fetchers if isinstance(f, BackendFunction)]
        if backend_fetchers:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in backend_fetchers])

        fetched: Dict[str, Any] = {}
        success_count = 0
        for func in backend_fetchers:
            try:
                result = await get_fetcher(func)(ctx)
                fetched[func.value] = result
                ctx.fetched_data[func.value] = {
                    "data": result,
                    "timestamp": datetime.now().isoformat(),
                }
                success_count += 1
                self.smart_log.performance_metric(
                    ctx.user_id, func.value, data_size=len(str(result)) if result else 0
                )
            except Exception as exc:
                self.smart_log.warning(
                    ctx.user_id, "DATA_FETCH_FAILED", f"{func.value}: {exc}"
                )
                fetched[func.value] = {"error": str(exc)}

        if backend_fetchers:
            self.smart_log.data_operations(
                ctx.user_id, [f.value for f in backend_fetchers], success_count
            )

        # Get query details
        original_q = safe_get(a, "original_query", "")
        intent_l3 = ctx.session.get("intent_l3", "") or ""
        product_intent = ctx.session.get("product_intent")
        
        # NEW: Enhanced response generation with 4-intent support
        if product_intent and ctx.session.get("is_product_related"):
            log.info(f"4INTENT_COMPLETE | user={ctx.user_id} | intent={product_intent}")
            
            # Generate base answer
            answer_dict = await self.llm_service.generate_response(
                original_q,
                ctx,
                fetched,
                intent_l3=intent_l3,
                query_intent=map_leaf_to_query_intent(intent_l3),
                product_intent=product_intent
            )
            
            # Generate UX-ready response
            ux_enhanced_answer = await generate_ux_response_for_intent(
                intent=product_intent,
                previous_answer=answer_dict,
                ctx=ctx,
                user_query=original_q
            )
            
            resp_type = ResponseType(ux_enhanced_answer.get("response_type", "final_answer"))
            
            # Store last_recommendation for follow-ups if products were fetched
            self._store_last_recommendation(original_q, ctx, fetched)
            
            # Final-answer summary → memory
            final_answer_summary = {
                "response_type": resp_type.value,
                "message_preview": str(ux_enhanced_answer.get("summary_message", ux_enhanced_answer.get("message", "")))[:300],
                "has_sections": False,
                "has_products": bool(ux_enhanced_answer.get("products")),
                "flow_triggered": False,
                "ux_intent": product_intent
            }
            
            snapshot_and_trim(ctx, base_query=original_q, final_answer=final_answer_summary)
            ctx.session.pop("assessment", None)
            ctx.session.pop("contextual_questions", None)
            self.ctx_mgr.save_context(ctx)
            
            self.smart_log.response_generated(ctx.user_id, resp_type.value, False)
            
            return BotResponse(
                resp_type, 
                content=ux_enhanced_answer,
                functions_executed=list(fetched.keys())
            )
        
        # Heuristic fallback: attach UX if products present even without explicit product_intent
        try:
            def _has_products(data: Dict[str, Any]) -> bool:
                if not isinstance(data, dict):
                    return False
                block = data.get("search_products")
                if isinstance(block, dict):
                    payload = block.get("data", block)
                    return bool(isinstance(payload, dict) and payload.get("products"))
                return False
            if ctx.session.get("is_product_related") and not product_intent and _has_products(fetched):
                fallback_intent = "show_me_options"
                log.info(f"4INTENT_COMPLETE_FALLBACK | user={ctx.user_id} | intent={fallback_intent}")
                answer_dict = await self.llm_service.generate_response(
                    original_q,
                    ctx,
                    fetched,
                    intent_l3=intent_l3,
                    query_intent=map_leaf_to_query_intent(intent_l3),
                    product_intent=fallback_intent,
                )
                ux_enhanced_answer = await generate_ux_response_for_intent(
                    intent=fallback_intent,
                    previous_answer=answer_dict,
                    ctx=ctx,
                    user_query=original_q,
                )
                resp_type = ResponseType(ux_enhanced_answer.get("response_type", "final_answer"))
                self._store_last_recommendation(original_q, ctx, fetched)
                snapshot_and_trim(
                    ctx,
                    base_query=original_q,
                    final_answer={
                        "response_type": resp_type.value,
                        "message_preview": str(ux_enhanced_answer.get("summary_message", ux_enhanced_answer.get("message", "")))[:300],
                        "has_sections": False,
                        "has_products": bool(ux_enhanced_answer.get("products")),
                        "flow_triggered": False,
                        "ux_intent": fallback_intent,
                    },
                )
                ctx.session.pop("assessment", None)
                ctx.session.pop("contextual_questions", None)
                self.ctx_mgr.save_context(ctx)
                self.smart_log.response_generated(ctx.user_id, resp_type.value, False)
                return BotResponse(resp_type, content=ux_enhanced_answer, functions_executed=list(fetched.keys()))
        except Exception:
            pass

        # DEFAULT: Generate unified response (existing flow)
        answer_dict = await self.llm_service.generate_response(
            original_q,
            ctx,
            fetched,
            intent_l3=intent_l3,
            query_intent=map_leaf_to_query_intent(intent_l3),
        )

        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))

        # Store last_recommendation for follow-ups if products were fetched
        self._store_last_recommendation(original_q, ctx, fetched)

        # Final-answer summary → memory
        final_answer_summary = {
            "response_type": resp_type.value,
            "message_preview": str(answer_dict.get("summary_message", answer_dict.get("message", "")))[:300],
            "has_sections": False,
            "has_products": bool(answer_dict.get("products")),
            "flow_triggered": False,
        }

        snapshot_and_trim(ctx, base_query=original_q, final_answer=final_answer_summary)
        ctx.session.pop("assessment", None)
        ctx.session.pop("contextual_questions", None)
        self.ctx_mgr.save_context(ctx)

        self.smart_log.response_generated(ctx.user_id, resp_type.value, False)

        return BotResponse(
            resp_type, 
            content=answer_dict,
            functions_executed=list(fetched.keys())
        )

    # ────────────────────────────────────────────────────────
    # Background policy helper
    # ────────────────────────────────────────────────────────
    def _needs_background(self, intent) -> bool:
        try:
            return str(intent).lower() in {"queryintent.recommendation"}
        except Exception:
            return False

    # ────────────────────────────────────────────────────────
    # Store last recommendation snippet for follow-ups
    # ────────────────────────────────────────────────────────
    def _store_last_recommendation(
        self, query: str, ctx: UserContext, fetched: Dict[str, Any]
    ) -> None:
        try:
            products_snapshot = []

            if "search_products" in fetched:
                search_data = fetched["search_products"]
                if isinstance(search_data, dict):
                    products = (
                        search_data.get("data", {}).get("products", [])
                        if "data" in search_data
                        else search_data.get("products", [])
                    )
                elif isinstance(search_data, list):
                    products = search_data
                else:
                    products = []

                for product in (products or [])[:8]:
                    try:
                        products_snapshot.append(
                            {
                                "title": product.get("name")
                                or product.get("title", "Unknown Product"),
                                "brand": product.get("brand", ""),
                                "price": product.get("price"),
                                "image_url": product.get("image", ""),
                                "rating": product.get("rating"),
                            }
                        )
                    except Exception:
                        continue

            if products_snapshot:
                ctx.session["last_recommendation"] = {
                    "query": query,
                    "as_of": datetime.now().isoformat(),
                    "products": products_snapshot,
                }
                self.smart_log.memory_operation(
                    ctx.user_id,
                    "last_recommendation_stored",
                    {"count": len(products_snapshot)},
                )
        except Exception as e:
            log.warning(
                f"LAST_RECOMMENDATION_STORE_FAILED | user={ctx.user_id} | error={e}"
            )