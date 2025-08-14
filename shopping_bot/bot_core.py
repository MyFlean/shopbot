"""
Brain of the WhatsApp shopping bot – with follow-up classification & delta assessment.
Core logic focuses on orchestration; helpers/LLM are modular.

UPDATED:
- Adds core-owned background decision immediately after intent classification.
- Returns a PROCESSING_STUB right after the ask-loop finishes when background is needed,
  deferring heavy fetchers/LLM work to the route/worker.
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
from .utils.smart_logger import get_smart_logger, LogLevel
from .bot_helpers import (
    compute_still_missing,
    store_user_answer,
    snapshot_and_trim,
    is_user_slot,
    get_func_value,
    build_question,
    ensure_proper_options,
)
from .llm_service import LLMService, map_leaf_to_query_intent

Cfg = get_config()
log = logging.getLogger(__name__)


class ShoppingBotCore:
    def __init__(self, context_mgr: RedisContextManager) -> None:
        self.ctx_mgr = context_mgr
        self.llm_service = LLMService()
        self.smart_log = get_smart_logger('bot_core')

    # ────────────────────────────────────────────────────────
    # Public entry point
    # ────────────────────────────────────────────────────────
    async def process_query(self, query: str, ctx: UserContext) -> BotResponse:
        self.smart_log.query_start(ctx.user_id, query, bool(ctx.session))

        try:
            # If we are mid-assessment, continue
            if "assessment" in ctx.session:
                self.smart_log.flow_decision(ctx.user_id, "CONTINUE_ASSESSMENT")
                return await self._continue_assessment(query, ctx)

            # Otherwise, check follow-up first
            fu = await self.llm_service.classify_follow_up(query, ctx)

            if fu.is_follow_up and not fu.patch.reset_context:
                self.smart_log.flow_decision(ctx.user_id, "HANDLE_FOLLOW_UP")
                self._apply_follow_up_patch(fu.patch, ctx)
                return await self._handle_follow_up(query, ctx, fu)

            # Reset or start fresh
            if fu.patch.reset_context:
                self.smart_log.flow_decision(ctx.user_id, "RESET_CONTEXT")
                self._reset_session_only(ctx)
            else:
                self.smart_log.flow_decision(ctx.user_id, "NEW_ASSESSMENT")

            return await self._start_new_assessment(query, ctx)

        except Exception as exc:  # noqa: BLE001
            self.smart_log.error_occurred(ctx.user_id, type(exc).__name__, "process_query", str(exc))
            return BotResponse(
                ResponseType.ERROR,
                {"message": "Sorry, something went wrong.", "error": str(exc)},
            )

    # ────────────────────────────────────────────────────────
    # Follow-up path (unchanged behavior)
    # ────────────────────────────────────────────────────────
    async def _handle_follow_up(self, query: str, ctx: UserContext, fu) -> BotResponse:
        # Assess what additional data we need
        fetch_list = await self.llm_service.assess_delta_requirements(query, ctx, fu.patch)

        if fetch_list:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetch_list])

        # Fetch the data
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

                # Log performance at detailed level
                self.smart_log.performance_metric(
                    ctx.user_id, func.value,
                    data_size=len(str(result)) if result else 0
                )

            except Exception as exc:  # noqa: BLE001
                self.smart_log.warning(ctx.user_id, f"DATA_FETCH_FAILED", f"{func.value}: {exc}")
                fetched[func.value] = {"error": str(exc)}

        if fetch_list:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetch_list], success_count)

        # Generate answer
        answer_dict = await self.llm_service.generate_answer(query, ctx, fetched)
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))

        # Save and return
        snapshot_and_trim(ctx, base_query=query)
        self.ctx_mgr.save_context(ctx)

        self.smart_log.response_generated(ctx.user_id, resp_type.value, bool(answer_dict.get("sections")))

        return BotResponse(
            resp_type,
            content={
                "message":  answer_dict.get("message", ""),
                "sections": answer_dict.get("sections"),
            },
            functions_executed=list(fetched.keys()),
        )

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

        self.smart_log.context_change(ctx.user_id, "SESSION_RESET", {"cleared_items": cleared_items})

    # ────────────────────────────────────────────────────────
    # New assessment
    # ────────────────────────────────────────────────────────
    async def _start_new_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        # Classify intent
        result = await self.llm_service.classify_intent(query)
        intent = map_leaf_to_query_intent(result.layer3)

        self.smart_log.intent_classified(
            ctx.user_id,
            (result.layer1, result.layer2, result.layer3),
            intent.value
        )

        # Update session with intent taxonomy and core-owned background decision
        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )
        needs_bg = self._needs_background(intent)
        ctx.session["needs_background"] = needs_bg
        self.smart_log.flow_decision(ctx.user_id, "BACKGROUND_DECISION", {"needs_background": needs_bg, "intent": intent.value})

        # Assess requirements
        assessment = await self.llm_service.assess_requirements(query, intent, result.layer3, ctx)

        user_slots = [f for f in assessment.missing_data if is_user_slot(f)]
        missing_data_names = [get_func_value(f) for f in assessment.missing_data]
        ask_first_names = [get_func_value(f) for f in user_slots]

        self.smart_log.requirements_assessed(ctx.user_id, missing_data_names, ask_first_names)

        # Generate contextual questions if needed
        if user_slots:
            contextual_questions = await self.llm_service.generate_contextual_questions(
                user_slots, query, result.layer3, ctx
            )
            ctx.session["contextual_questions"] = contextual_questions

        # Set up assessment session
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

        # Store user's answer if this isn't the original query
        if query != a["original_query"]:
            store_user_answer(query, a, ctx)
            self.smart_log.context_change(
                ctx.user_id, "USER_ANSWER_STORED",
                {"for": a.get("currently_asking"), "answer_len": len(query)}
            )

        # Compute what's still missing
        still_missing = compute_still_missing(a, ctx)
        ask_first = [f for f in still_missing if is_user_slot(f)]
        fetch_later = [f for f in still_missing if not is_user_slot(f)]

        # If we need to ask the user something
        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value

            self.smart_log.user_question(ctx.user_id, func_value)
            self.ctx_mgr.save_context(ctx)

            return BotResponse(ResponseType.QUESTION, build_question(func, ctx))

        # No more questions to ask; decide whether to defer heavy work
        needs_bg = bool(ctx.session.get("needs_background"))
        if fetch_later and needs_bg:
            # Mark phase as processing and persist; the route will enqueue the background job.
            a["phase"] = "processing"
            self.smart_log.flow_decision(ctx.user_id, "DEFER_TO_BACKGROUND", {"fetchers": [get_func_value(f) for f in fetch_later]})
            self.ctx_mgr.save_context(ctx)

            return BotResponse(
                ResponseType.PROCESSING_STUB,
                content={"message": "Processing your request…"}
            )

        # Complete the assessment synchronously
        self.smart_log.flow_decision(ctx.user_id, "COMPLETE_ASSESSMENT", f"{len(fetch_later)} fetches needed")
        return await self._complete_assessment(a, ctx, fetch_later)

    # ────────────────────────────────────────────────────────
    # Complete assessment
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

                # Log performance at detailed level
                self.smart_log.performance_metric(
                    ctx.user_id, func.value,
                    data_size=len(str(result)) if result else 0
                )

            except Exception as exc:  # noqa: BLE001
                self.smart_log.warning(ctx.user_id, "DATA_FETCH_FAILED", f"{func.value}: {exc}")
                fetched[func.value] = {"error": str(exc)}

        if backend_fetchers:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in backend_fetchers], success_count)

        # Generate final answer
        original_q = safe_get(a, "original_query", "")
        answer_dict = await self.llm_service.generate_answer(original_q, ctx, fetched)
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        message = answer_dict.get(
            "message",
            "I can help you with shopping queries. Please provide more details.",
        )

        # Clean up
        snapshot_and_trim(ctx, base_query=original_q)
        ctx.session.pop("assessment", None)
        ctx.session.pop("contextual_questions", None)
        self.ctx_mgr.save_context(ctx)

        self.smart_log.response_generated(ctx.user_id, resp_type.value, bool(answer_dict.get("sections")))

        return BotResponse(
            resp_type,
            content={
                "message":  message,
                "sections": answer_dict.get("sections"),
            },
            functions_executed=list(fetched.keys()),
        )

    # ────────────────────────────────────────────────────────
    # Background policy helper (core-owned)
    # ────────────────────────────────────────────────────────
    def _needs_background(self, intent) -> bool:
        """
        Decide early—right after intent classification—if this query should be deferred.
        Keep it fast and deterministic. You can later tune this mapping without touching routes.
        """
        try:
            return str(intent).lower() in {
                "queryintent.product_search",
                "queryintent.recommendation",
                "queryintent.product_comparison",
            }
        except Exception:
            # Be conservative on unexpected intents.
            return False
