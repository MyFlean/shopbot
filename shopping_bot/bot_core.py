"""
Brain of the WhatsApp shopping bot – with follow-up classification & delta assessment.
Core logic focuses on orchestration; helpers/LLM are modular.
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
from .bot_helpers import (
    compute_still_missing,
    store_user_answer,
    snapshot_and_trim,
    is_user_slot,
    get_func_value,
    build_question,
    normalize_to_mc3,          # used once below
)
from .llm_service import LLMService, map_leaf_to_query_intent

Cfg = get_config()
log = logging.getLogger(__name__)


class ShoppingBotCore:
    def __init__(self, context_mgr: RedisContextManager) -> None:
        self.ctx_mgr = context_mgr
        self.llm_service = LLMService()

    # ────────────────────────────────────────────────────────
    # Public entry point
    # ────────────────────────────────────────────────────────
    async def process_query(self, query: str, ctx: UserContext) -> BotResponse:
        try:
            # If we are mid-assessment, continue
            if "assessment" in ctx.session:
                return await self._continue_assessment(query, ctx)

            # Otherwise, check follow-up first
            fu = await self.llm_service.classify_follow_up(query, ctx)
            if fu.is_follow_up and not fu.patch.reset_context:
                self._apply_follow_up_patch(fu.patch, ctx)
                return await self._handle_follow_up(query, ctx, fu)

            # Reset if user asked to, else start fresh
            if fu.patch.reset_context:
                self._reset_session_only(ctx)

            return await self._start_new_assessment(query, ctx)

        except Exception as exc:  # noqa: BLE001
            log.exception("process_query failed")
            return BotResponse(
                ResponseType.ERROR,
                {"message": "Sorry, something went wrong.", "error": str(exc)},
            )

    # ────────────────────────────────────────────────────────
    # Follow-up path
    # ────────────────────────────────────────────────────────
    async def _handle_follow_up(self, query: str, ctx: UserContext, fu) -> BotResponse:
        fetch_list = await self.llm_service.assess_delta_requirements(query, ctx, fu.patch)

        fetched: Dict[str, Any] = {}
        for func in fetch_list:
            try:
                result = await get_fetcher(func)(ctx)
                fetched[func.value] = result
                ctx.fetched_data[func.value] = {
                    "data": result,
                    "timestamp": datetime.now().isoformat(),
                }
            except Exception as exc:  # noqa: BLE001
                log.warning("%s failed: %s", func.value, exc)
                fetched[func.value] = {"error": str(exc)}

        answer_dict = await self.llm_service.generate_answer(query, ctx, fetched)
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))

        snapshot_and_trim(ctx, base_query=query)
        self.ctx_mgr.save_context(ctx)

        return BotResponse(
            resp_type,
            content={
                "message":  answer_dict.get("message", ""),
                "sections": answer_dict.get("sections"),
            },
            functions_executed=list(fetched.keys()),
        )

    def _apply_follow_up_patch(self, patch, ctx: UserContext) -> None:
        for k, v in patch.slots.items():
            ctx.session[k] = v
        if patch.intent_override:
            ctx.session["intent_override"] = patch.intent_override

    def _reset_session_only(self, ctx: UserContext) -> None:
        ctx.session.clear()
        ctx.fetched_data.clear()

    # ────────────────────────────────────────────────────────
    # New assessment
    # ────────────────────────────────────────────────────────
    async def _start_new_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        result = await self.llm_service.classify_intent(query)
        intent = map_leaf_to_query_intent(result.layer3)

        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )

        assessment = await self.llm_service.assess_requirements(query, intent, result.layer3, ctx)

        user_slots = [f for f in assessment.missing_data if is_user_slot(f)]
        if user_slots:
            contextual_questions = await self.llm_service.generate_contextual_questions(
                user_slots, query, result.layer3, ctx
            )
            ctx.session["contextual_questions"] = {
                slot: normalize_to_mc3(q) for slot, q in contextual_questions.items()
            }

        ctx.session["assessment"] = {
            "original_query": query,
            "intent": intent.value,
            "missing_data": [get_func_value(f) for f in assessment.missing_data],
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

        still_missing = compute_still_missing(a, ctx)
        ask_first = [f for f in still_missing if is_user_slot(f)]
        fetch_later = [f for f in still_missing if not is_user_slot(f)]

        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value
            self.ctx_mgr.save_context(ctx)
            return BotResponse(ResponseType.QUESTION, build_question(func, ctx))

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
        fetched: Dict[str, Any] = {}
        for func in fetchers:
            if isinstance(func, BackendFunction):
                try:
                    result = await get_fetcher(func)(ctx)
                    fetched[func.value] = result
                    ctx.fetched_data[func.value] = {
                        "data": result,
                        "timestamp": datetime.now().isoformat(),
                    }
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s failed: %s", func.value, exc)
                    fetched[func.value] = {"error": str(exc)}

        original_q = safe_get(a, "original_query", "")
        answer_dict = await self.llm_service.generate_answer(original_q, ctx, fetched)
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        message = answer_dict.get(
            "message",
            "I can help you with shopping queries. Please provide more details.",
        )

        snapshot_and_trim(ctx, base_query=original_q)
        ctx.session.pop("assessment", None)
        ctx.session.pop("contextual_questions", None)
        self.ctx_mgr.save_context(ctx)

        return BotResponse(
            resp_type,
            content={
                "message":  message,
                "sections": answer_dict.get("sections"),
            },
            functions_executed=list(fetched.keys()),
        )
