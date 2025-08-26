"""
Brain of the WhatsApp shopping bot – baseline core.

UPDATED POLICY
--------------
• ALL product intents use unified response generation
• Structured product responses with descriptions
• Clean separation between product and non-product responses
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Union

from .config import get_config
from .enums import ResponseType, BackendFunction, UserSlot
from .models import BotResponse, UserContext
from .redis_manager import RedisContextManager
from .data_fetchers import get_fetcher
from .utils.helpers import safe_get
from .utils.smart_logger import get_smart_logger
from .bot_helpers import (
    compute_still_missing,
    store_user_answer,
    snapshot_and_trim,
    is_user_slot,
    get_func_value,
    build_question,
)
from .llm_service import LLMService, map_leaf_to_query_intent

Cfg = get_config()
log = logging.getLogger(__name__)

# Intents that should return structured product responses
PRODUCT_INTENTS = {
    "product_discovery", "recommendation", 
    "specific_product_search", "product_comparison"
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
    # Follow-up path
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

        # Generate unified response
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
    # New assessment
    # ────────────────────────────────────────────────────────
    async def _start_new_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        result = await self.llm_service.classify_intent(query, ctx)
        intent = map_leaf_to_query_intent(result.layer3)

        self.smart_log.intent_classified(
            ctx.user_id, (result.layer1, result.layer2, result.layer3), intent.value
        )

        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )
        needs_bg = self._needs_background(intent)
        ctx.session["needs_background"] = needs_bg
        self.smart_log.flow_decision(
            ctx.user_id,
            "BACKGROUND_DECISION",
            {"needs_background": needs_bg, "intent": intent.value, "intent_l3": result.layer3},
        )

        assessment = await self.llm_service.assess_requirements(
            query, intent, result.layer3, ctx
        )

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

        self.ctx_mgr.save_context(ctx)
        return await self._continue_assessment(query, ctx)

    # ────────────────────────────────────────────────────────
    # Continue assessment
    # ────────────────────────────────────────────────────────
    async def _continue_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        a = ctx.session["assessment"]

        if query != a["original_query"]:
            store_user_answer(query, a, ctx)
            self.smart_log.context_change(
                ctx.user_id, "USER_ANSWER_STORED",
                {"for": a.get("currently_asking"), "answer_len": len(query)}
            )

        still_missing = compute_still_missing(a, ctx)
        ask_first = [f for f in still_missing if is_user_slot(f)]
        fetch_later = [f for f in still_missing if not is_user_slot(f)]

        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value

            self.smart_log.user_question(ctx.user_id, func_value)
            self.ctx_mgr.save_context(ctx)
            return BotResponse(ResponseType.QUESTION, build_question(func, ctx))

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
        return await self._complete_assessment(a, ctx, fetch_later)

    # ────────────────────────────────────────────────────────
    # Complete assessment (sync)
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

        # Generate unified response
        original_q = safe_get(a, "original_query", "")
        intent_l3 = ctx.session.get("intent_l3", "") or ""
        
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