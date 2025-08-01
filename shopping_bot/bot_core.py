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
    ensure_proper_options,     # updated import
)
from .llm_service import LLMService, map_leaf_to_query_intent

Cfg = get_config()
log = logging.getLogger(__name__)


class ShoppingBotCore:
    def __init__(self, context_mgr: RedisContextManager) -> None:
        self.ctx_mgr = context_mgr
        self.llm_service = LLMService()
        log.info("ShoppingBotCore initialized successfully")

    # ────────────────────────────────────────────────────────
    # Public entry point
    # ────────────────────────────────────────────────────────
    async def process_query(self, query: str, ctx: UserContext) -> BotResponse:
        # Generate a unique request ID for tracing this entire conversation flow
        request_id = f"{ctx.user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        log.info(
            "🚀 PROCESSING_QUERY_START | req_id=%s | user_id=%s | query_len=%d | has_session=%s",
            request_id, ctx.user_id, len(query), bool(ctx.session)
        )
        log.debug("📝 QUERY_CONTENT | req_id=%s | query='%s'", request_id, query[:200])
        
        try:
            # Log current session state for debugging
            if ctx.session:
                log.debug(
                    "📊 SESSION_STATE | req_id=%s | keys=%s | assessment_active=%s",
                    request_id, list(ctx.session.keys()), "assessment" in ctx.session
                )
            
            # If we are mid-assessment, continue
            if "assessment" in ctx.session:
                log.info("🔄 CONTINUING_ASSESSMENT | req_id=%s", request_id)
                result = await self._continue_assessment(query, ctx)
                log.info(
                    "✅ ASSESSMENT_CONTINUED | req_id=%s | response_type=%s | functions=%s",
                    request_id, result.response_type.value, result.functions_executed
                )
                return result

            # Otherwise, check follow-up first
            log.info("🔍 CLASSIFYING_FOLLOW_UP | req_id=%s", request_id)
            fu = await self.llm_service.classify_follow_up(query, ctx)
            log.info(
                "📋 FOLLOW_UP_CLASSIFICATION | req_id=%s | is_follow_up=%s | reset_context=%s",
                request_id, fu.is_follow_up, fu.patch.reset_context if fu.patch else None
            )
            
            if fu.is_follow_up and not fu.patch.reset_context:
                log.info("➡️ HANDLING_FOLLOW_UP | req_id=%s", request_id)
                self._apply_follow_up_patch(fu.patch, ctx)
                result = await self._handle_follow_up(query, ctx, fu)
                log.info(
                    "✅ FOLLOW_UP_HANDLED | req_id=%s | response_type=%s | functions=%s",
                    request_id, result.response_type.value, result.functions_executed
                )
                return result

            # Reset if user asked to, else start fresh
            if fu.patch.reset_context:
                log.info("🔄 RESETTING_CONTEXT | req_id=%s", request_id)
                self._reset_session_only(ctx)

            log.info("🆕 STARTING_NEW_ASSESSMENT | req_id=%s", request_id)
            result = await self._start_new_assessment(query, ctx)
            log.info(
                "✅ NEW_ASSESSMENT_STARTED | req_id=%s | response_type=%s",
                request_id, result.response_type.value
            )
            return result

        except Exception as exc:  # noqa: BLE001
            log.exception(
                "❌ PROCESS_QUERY_ERROR | req_id=%s | error_type=%s | error_msg=%s",
                request_id, type(exc).__name__, str(exc)
            )
            return BotResponse(
                ResponseType.ERROR,
                {"message": "Sorry, something went wrong.", "error": str(exc)},
            )

    # ────────────────────────────────────────────────────────
    # Follow-up path
    # ────────────────────────────────────────────────────────
    async def _handle_follow_up(self, query: str, ctx: UserContext, fu) -> BotResponse:
        log.info("🔄 HANDLE_FOLLOW_UP_START | user_id=%s", ctx.user_id)
        
        # Assess what additional data we need for this follow-up
        log.debug("📊 ASSESSING_DELTA_REQUIREMENTS | user_id=%s", ctx.user_id)
        fetch_list = await self.llm_service.assess_delta_requirements(query, ctx, fu.patch)
        log.info(
            "📋 DELTA_REQUIREMENTS_ASSESSED | user_id=%s | functions_needed=%s",
            ctx.user_id, [f.value for f in fetch_list]
        )

        fetched: Dict[str, Any] = {}
        for func in fetch_list:
            log.debug("🔍 FETCHING_DATA | user_id=%s | function=%s", ctx.user_id, func.value)
            try:
                result = await get_fetcher(func)(ctx)
                fetched[func.value] = result
                ctx.fetched_data[func.value] = {
                    "data": result,
                    "timestamp": datetime.now().isoformat(),
                }
                log.info(
                    "✅ DATA_FETCHED | user_id=%s | function=%s | data_size=%d",
                    ctx.user_id, func.value, len(str(result)) if result else 0
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "⚠️ DATA_FETCH_FAILED | user_id=%s | function=%s | error=%s",
                    ctx.user_id, func.value, str(exc)
                )
                fetched[func.value] = {"error": str(exc)}

        # Generate the final answer
        log.debug("🤖 GENERATING_FOLLOW_UP_ANSWER | user_id=%s", ctx.user_id)
        answer_dict = await self.llm_service.generate_answer(query, ctx, fetched)
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        
        log.info(
            "✅ FOLLOW_UP_ANSWER_GENERATED | user_id=%s | response_type=%s | has_sections=%s",
            ctx.user_id, resp_type.value, bool(answer_dict.get("sections"))
        )

        # Save context and clean up
        snapshot_and_trim(ctx, base_query=query)
        self.ctx_mgr.save_context(ctx)
        log.debug("💾 CONTEXT_SAVED_AFTER_FOLLOW_UP | user_id=%s", ctx.user_id)

        return BotResponse(
            resp_type,
            content={
                "message":  answer_dict.get("message", ""),
                "sections": answer_dict.get("sections"),
            },
            functions_executed=list(fetched.keys()),
        )

    def _apply_follow_up_patch(self, patch, ctx: UserContext) -> None:
        log.debug(
            "🔧 APPLYING_FOLLOW_UP_PATCH | user_id=%s | slots=%s | intent_override=%s",
            ctx.user_id, list(patch.slots.keys()) if patch.slots else [], 
            patch.intent_override
        )
        
        for k, v in patch.slots.items():
            old_value = ctx.session.get(k)
            ctx.session[k] = v
            log.debug(
                "🔄 SLOT_UPDATED | user_id=%s | slot=%s | old_value=%s | new_value=%s",
                ctx.user_id, k, old_value, v
            )
            
        if patch.intent_override:
            ctx.session["intent_override"] = patch.intent_override
            log.info(
                "🎯 INTENT_OVERRIDDEN | user_id=%s | new_intent=%s",
                ctx.user_id, patch.intent_override
            )

    def _reset_session_only(self, ctx: UserContext) -> None:
        session_keys = list(ctx.session.keys())
        fetched_keys = list(ctx.fetched_data.keys())
        
        ctx.session.clear()
        ctx.fetched_data.clear()
        
        log.info(
            "🔄 SESSION_RESET | user_id=%s | cleared_session_keys=%s | cleared_fetched_keys=%s",
            ctx.user_id, session_keys, fetched_keys
        )

    # ────────────────────────────────────────────────────────
    # New assessment
    # ────────────────────────────────────────────────────────
    async def _start_new_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        log.info("🆕 NEW_ASSESSMENT_START | user_id=%s", ctx.user_id)
        
        # Classify the user's intent
        log.debug("🧠 CLASSIFYING_INTENT | user_id=%s", ctx.user_id)
        result = await self.llm_service.classify_intent(query)
        intent = map_leaf_to_query_intent(result.layer3)
        
        log.info(
            "🎯 INTENT_CLASSIFIED | user_id=%s | l1=%s | l2=%s | l3=%s | mapped_intent=%s",
            ctx.user_id, result.layer1, result.layer2, result.layer3, intent.value
        )

        # Update session with classification results
        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )

        # Assess what data we need to fulfill this request
        log.debug("📊 ASSESSING_REQUIREMENTS | user_id=%s | intent=%s", ctx.user_id, intent.value)
        assessment = await self.llm_service.assess_requirements(query, intent, result.layer3, ctx)
        
        log.info(
            "📋 REQUIREMENTS_ASSESSED | user_id=%s | missing_data=%s | priority_order=%s",
            ctx.user_id, 
            [get_func_value(f) for f in assessment.missing_data],
            [get_func_value(f) for f in assessment.priority_order]
        )

        # Generate contextual questions for user slots
        user_slots = [f for f in assessment.missing_data if is_user_slot(f)]
        if user_slots:
            log.info(
                "❓ GENERATING_CONTEXTUAL_QUESTIONS | user_id=%s | user_slots=%s",
                ctx.user_id, [get_func_value(f) for f in user_slots]
            )
            
            contextual_questions = await self.llm_service.generate_contextual_questions(
                user_slots, query, result.layer3, ctx
            )
            ctx.session["contextual_questions"] = contextual_questions
            log.debug(
                "✅ CONTEXTUAL_QUESTIONS_GENERATED | user_id=%s | question_count=%d",
                ctx.user_id, len(contextual_questions)
            )

        # Set up the assessment session
        ctx.session["assessment"] = {
            "original_query": query,
            "intent": intent.value,
            "missing_data": [get_func_value(f) for f in assessment.missing_data],
            "priority_order": [get_func_value(f) for f in assessment.priority_order],
            "fulfilled": [],
            "currently_asking": None,
        }

        log.info("💾 SAVING_ASSESSMENT_CONTEXT | user_id=%s", ctx.user_id)
        self.ctx_mgr.save_context(ctx)
        
        # Continue with the assessment flow
        return await self._continue_assessment(query, ctx)

    # ────────────────────────────────────────────────────────
    # Continue assessment
    # ────────────────────────────────────────────────────────
    async def _continue_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        a = ctx.session["assessment"]
        log.debug(
            "🔄 CONTINUE_ASSESSMENT | user_id=%s | original_query=%s | current_query=%s",
            ctx.user_id, a["original_query"], query
        )

        # Store user's answer if this isn't the original query
        if query != a["original_query"]:
            log.info(
                "💬 STORING_USER_ANSWER | user_id=%s | currently_asking=%s",
                ctx.user_id, a.get("currently_asking")
            )
            store_user_answer(query, a, ctx)

        # Compute what's still missing
        still_missing = compute_still_missing(a, ctx)
        ask_first = [f for f in still_missing if is_user_slot(f)]
        fetch_later = [f for f in still_missing if not is_user_slot(f)]
        
        log.info(
            "📊 ASSESSMENT_STATUS | user_id=%s | still_missing=%s | ask_first=%s | fetch_later=%s",
            ctx.user_id, 
            [get_func_value(f) for f in still_missing],
            [get_func_value(f) for f in ask_first],
            [get_func_value(f) for f in fetch_later]
        )

        # If we still need to ask the user something
        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value
            
            log.info("❓ ASKING_USER_QUESTION | user_id=%s | asking_for=%s", ctx.user_id, func_value)
            self.ctx_mgr.save_context(ctx)
            
            return BotResponse(ResponseType.QUESTION, build_question(func, ctx))

        # Otherwise, we can complete the assessment
        log.info("🏁 COMPLETING_ASSESSMENT | user_id=%s", ctx.user_id)
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
        log.info(
            "🏁 COMPLETE_ASSESSMENT_START | user_id=%s | fetchers=%s",
            ctx.user_id, [get_func_value(f) for f in fetchers if isinstance(f, BackendFunction)]
        )
        
        fetched: Dict[str, Any] = {}
        for func in fetchers:
            if isinstance(func, BackendFunction):
                log.debug("🔍 FETCHING_FINAL_DATA | user_id=%s | function=%s", ctx.user_id, func.value)
                try:
                    result = await get_fetcher(func)(ctx)
                    fetched[func.value] = result
                    ctx.fetched_data[func.value] = {
                        "data": result,
                        "timestamp": datetime.now().isoformat(),
                    }
                    log.info(
                        "✅ FINAL_DATA_FETCHED | user_id=%s | function=%s | data_size=%d",
                        ctx.user_id, func.value, len(str(result)) if result else 0
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "⚠️ FINAL_DATA_FETCH_FAILED | user_id=%s | function=%s | error=%s",
                        ctx.user_id, func.value, str(exc)
                    )
                    fetched[func.value] = {"error": str(exc)}

        # Generate the final answer
        original_q = safe_get(a, "original_query", "")
        log.debug("🤖 GENERATING_FINAL_ANSWER | user_id=%s", ctx.user_id)
        
        answer_dict = await self.llm_service.generate_answer(original_q, ctx, fetched)
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        message = answer_dict.get(
            "message",
            "I can help you with shopping queries. Please provide more details.",
        )
        
        log.info(
            "✅ FINAL_ANSWER_GENERATED | user_id=%s | response_type=%s | has_sections=%s | message_len=%d",
            ctx.user_id, resp_type.value, bool(answer_dict.get("sections")), len(message)
        )

        # Clean up and save
        snapshot_and_trim(ctx, base_query=original_q)
        ctx.session.pop("assessment", None)
        ctx.session.pop("contextual_questions", None)
        self.ctx_mgr.save_context(ctx)
        
        log.info(
            "🎉 ASSESSMENT_COMPLETED | user_id=%s | functions_executed=%s",
            ctx.user_id, list(fetched.keys())
        )

        return BotResponse(
            resp_type,
            content={
                "message":  message,
                "sections": answer_dict.get("sections"),
            },
            functions_executed=list(fetched.keys()),
        )