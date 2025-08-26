"""
Enhanced ShoppingBotCore with Flow support - FIXED VERSION
==========================================================

Fixes:
4. Fetchers now properly save to session:*:fetched (merge, don't overwrite)
5. User answers now saved to user:*:permanent storage  
6. Added missing await statements and proper error handling
7. Guard against re-entry when phase=processing
8. Enhanced logging for debugging persistence issues

Policy (unchanged):
- Flows & six-core sections are ONLY used when layer3 intent == "Recommendation"
- Background deferral (PROCESSING_STUB → FE polls → Flow) happens ONLY for Recommendation
- All other intents always return synchronous simple text (single-message reply)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Union, Optional

from .config import get_config
from .enums import ResponseType, BackendFunction, UserSlot
from .models import (
    BotResponse,
    UserContext,
    EnhancedBotResponse,
    ProductData,
    FlowPayload,
    FlowType,
)
from .redis_manager import RedisContextManager
from .data_fetchers import get_fetcher
from .utils.smart_logger import get_smart_logger
from .bot_helpers import (
    compute_still_missing,
    store_user_answer,
    snapshot_and_trim,
    is_user_slot,
    get_func_value,
    build_question,
    SLOT_TO_SESSION_KEY,
)
from .flow_generator import FlowTemplateGenerator
from .llm_service import map_leaf_to_query_intent

# TYPE_CHECKING import to avoid circular dependency
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot_core import ShoppingBotCore

Cfg = get_config()
log = logging.getLogger(__name__)


class EnhancedShoppingBotCore:
    """Enhanced bot core with Flow support that wraps existing ShoppingBotCore."""

    def __init__(self, base_bot_core: "ShoppingBotCore"):
        """Initialize with an existing base bot core."""
        self.base_core = base_bot_core
        self.ctx_mgr: RedisContextManager = base_bot_core.ctx_mgr
        self.llm_service = base_bot_core.llm_service
        self.smart_log = base_bot_core.smart_log

        # Enhanced components
        self.flow_generator = FlowTemplateGenerator()

        # Feature flags
        # Tie flow availability to async flag so memory path is consistent in sync-only mode
        self.flow_enabled = Cfg.ENABLE_ASYNC
        self.enhanced_llm_enabled = True

    # ────────────────────────────────────────────────────────
    # Public entry points
    # ────────────────────────────────────────────────────────

    async def process_query(
        self,
        query: str,
        ctx: UserContext,
        enable_flows: bool = True,
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """Enhanced process_query with comprehensive logging and error handling."""
        self.smart_log.query_start(ctx.user_id, query, bool(ctx.session))
        log.info(f"ENHANCED_QUERY_START | user={ctx.user_id} | session={ctx.session_id} | query='{query[:50]}...'")

        if not self.flow_enabled or not enable_flows:
            log.info(f"FLOW_DISABLED | user={ctx.user_id} | delegating to base core")
            return await self.base_core.process_query(query, ctx)

        try:
            # FIX: Guard against re-entry when already processing
            if "assessment" in ctx.session:
                current_phase = ctx.session["assessment"].get("phase")
                if current_phase == "processing":
                    log.warning(f"RE_ENTRY_BLOCKED | user={ctx.user_id} | phase={current_phase}")
                    return BotResponse(
                        ResponseType.QUESTION,
                        {"message": "Still working on your previous request. Please wait..."}
                    )
                
                log.info(f"CONTINUE_ASSESSMENT | user={ctx.user_id} | phase={current_phase}")
                return await self._continue_assessment_enhanced(query, ctx)

            # Follow-up handling
            fu = await self.llm_service.classify_follow_up(query, ctx)
            if fu.is_follow_up and not fu.patch.reset_context:
                log.info(f"HANDLE_FOLLOW_UP | user={ctx.user_id} | is_follow_up=true")
                self.smart_log.flow_decision(ctx.user_id, "HANDLE_FOLLOW_UP")
                self._apply_follow_up_patch(fu.patch, ctx)
                return await self._handle_follow_up_enhanced(query, ctx, fu)

            # Reset or start fresh
            if fu.patch.reset_context:
                log.info(f"RESET_CONTEXT | user={ctx.user_id}")
                self.smart_log.flow_decision(ctx.user_id, "RESET_CONTEXT")
                self._reset_session_only(ctx)
            else:
                log.info(f"NEW_ASSESSMENT | user={ctx.user_id}")
                self.smart_log.flow_decision(ctx.user_id, "NEW_ASSESSMENT")

            return await self._start_new_assessment_enhanced(query, ctx)

        except Exception as exc:
            log.error(f"ENHANCED_QUERY_ERROR | user={ctx.user_id} | error={exc}", exc_info=True)
            self.smart_log.error_occurred(
                ctx.user_id, type(exc).__name__, "process_query", str(exc)
            )
            return BotResponse(
                ResponseType.ERROR,
                {"message": "Sorry, something went wrong.", "error": str(exc)},
            )

    async def process_query_legacy(self, query: str, ctx: UserContext) -> BotResponse:
        """Legacy interface that always returns BotResponse."""
        result = await self.process_query(query, ctx, enable_flows=False)
        if isinstance(result, EnhancedBotResponse):
            return result.to_legacy_bot_response()
        return result

    # ────────────────────────────────────────────────────────
    # Enhanced processing methods
    # ────────────────────────────────────────────────────────

    async def _handle_follow_up_enhanced(self, query: str, ctx: UserContext, fu):
        log.error(f"DEBUG_FOLLOW_UP_START | user={ctx.user_id}")
        
        effective_l3 = fu.patch.intent_override or ctx.session.get("intent_l3", "") or ""
        log.error(f"DEBUG_FOLLOW_UP_L3 | user={ctx.user_id} | effective_l3={effective_l3}")

        effective_qi = map_leaf_to_query_intent(effective_l3)
        
        log.info(f"FOLLOW_UP_ENHANCED | user={ctx.user_id} | effective_l3={effective_l3}")

        # If Recommendation, defer only if async is enabled; otherwise complete synchronously
        if effective_l3 == "Recommendation" and Cfg.ENABLE_ASYNC:
            ctx.session["needs_background"] = True
            a = ctx.session.get("assessment")
            if not a:
                a = {
                    "original_query": query,
                    "intent": "Recommendation",
                    "missing_data": [],
                    "priority_order": [],
                    "fulfilled": [],
                    "currently_asking": None,
                }
                ctx.session["assessment"] = a
            a["phase"] = "active"
            
            # Save context immediately when deferring
            self.ctx_mgr.save_context(ctx)
            log.info(f"FOLLOW_UP_DEFER | user={ctx.user_id} | intent_l3={effective_l3}")
            
            self.smart_log.flow_decision(
                ctx.user_id, "DEFER_TO_BACKGROUND_FOLLOW_UP", {"intent_l3": effective_l3}
            )
            return BotResponse(
                ResponseType.PROCESSING_STUB,
                content={"message": "Processing your request…"},
            )

        # Non-Recommendation or Recommendation in forced-sync mode: delta fetch + reply
        fetch_list = await self.llm_service.assess_delta_requirements(query, ctx, fu.patch)
        if fetch_list:
            log.info(f"FOLLOW_UP_FETCHERS | user={ctx.user_id} | fetchers={[f.value for f in fetch_list]}")
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetch_list])

        # FIX: Execute fetchers with proper persistence
        fetched = await self._execute_fetchers_with_persistence(fetch_list, ctx)

        # Generate answer (enhanced for Recommendation; simple otherwise)
        if effective_l3 == "Recommendation":
            answer_dict = await self._generate_enhanced_answer(query, ctx, fetched)
        else:
            answer_dict = await self.llm_service.generate_simple_reply(
                query, ctx, fetched, intent_l3=effective_l3, query_intent=effective_qi
        )

        enhanced_response = await self._create_enhanced_response(
            answer_dict, list(fetched.keys()), query, ctx
        )

        # FIX: Store user answers and save context
        self._store_follow_up_answers(query, ctx)
        snapshot_and_trim(ctx, base_query=query)
        self.ctx_mgr.save_context(ctx)

        log.info(f"FOLLOW_UP_COMPLETE | user={ctx.user_id} | response_type={enhanced_response.response_type.value}")
        self.smart_log.response_generated(
            ctx.user_id,
            enhanced_response.response_type.value,
            enhanced_response.requires_flow,
        )

        return enhanced_response

    async def _start_new_assessment_enhanced(
        self,
        query: str,
        ctx: UserContext,
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """Enhanced new assessment with proper persistence."""
        log.info(f"NEW_ASSESSMENT_START | user={ctx.user_id} | query='{query[:50]}...'")

        # Classify intent
        result = await self.llm_service.classify_intent(query, ctx)
        intent = map_leaf_to_query_intent(result.layer3)

        log.info(f"INTENT_CLASSIFIED | user={ctx.user_id} | l1={result.layer1} | l2={result.layer2} | l3={result.layer3}")
        self.smart_log.intent_classified(
            ctx.user_id, (result.layer1, result.layer2, result.layer3), intent.value
        )

        # Update session with intent taxonomy
        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )
        needs_bg = self._needs_background(intent)
        ctx.session["needs_background"] = needs_bg
        
        log.info(f"BACKGROUND_DECISION | user={ctx.user_id} | needs_background={needs_bg} | intent_l3={result.layer3}")
        self.smart_log.flow_decision(
            ctx.user_id,
            "BACKGROUND_DECISION",
            {"needs_background": needs_bg, "intent": intent.value, "intent_l3": result.layer3},
        )

        # Assess requirements
        assessment = await self.llm_service.assess_requirements(
            query, intent, result.layer3, ctx
        )

        user_slots = [f for f in assessment.missing_data if is_user_slot(f)]
        missing_data_names = [get_func_value(f) for f in assessment.missing_data]
        ask_first_names = [get_func_value(f) for f in user_slots]

        log.info(f"REQUIREMENTS_ASSESSED | user={ctx.user_id} | missing_data={missing_data_names} | ask_first={ask_first_names}")
        self.smart_log.requirements_assessed(
            ctx.user_id, missing_data_names, ask_first_names
        )

        # Generate contextual questions if needed
        if user_slots:
            contextual_questions = await self.llm_service.generate_contextual_questions(
                user_slots, query, result.layer3, ctx
            )
            ctx.session["contextual_questions"] = contextual_questions
            log.info(f"CONTEXTUAL_QUESTIONS_GENERATED | user={ctx.user_id} | count={len(contextual_questions)}")

        # Initialize assessment session
        ctx.session["assessment"] = {
            "original_query": query,
            "intent": intent.value,
            "missing_data": missing_data_names,
            "priority_order": [get_func_value(f) for f in assessment.priority_order],
            "fulfilled": [],
            "currently_asking": None,
            "phase": "active",  # FIX: Explicit phase tracking
            "started_at": datetime.now().isoformat(),
        }

        log.info(f"ASSESSMENT_INITIALIZED | user={ctx.user_id} | phase=active")
        self.ctx_mgr.save_context(ctx)
        return await self._continue_assessment_enhanced(query, ctx)

    async def _continue_assessment_enhanced(
        self,
        query: str,
        ctx: UserContext,
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """Enhanced assessment continuation with proper persistence."""
        a = ctx.session["assessment"]
        log.info(f"CONTINUE_ASSESSMENT | user={ctx.user_id} | phase={a.get('phase')} | currently_asking={a.get('currently_asking')}")

        # Store user's answer if this isn't the original query
        if query != a["original_query"]:
            # FIX: Store user answer with persistence to permanent storage
            self._store_user_answer_enhanced(query, a, ctx)
            log.info(f"USER_ANSWER_STORED | user={ctx.user_id} | for={a.get('currently_asking')} | answer_len={len(query)}")
            
            self.smart_log.context_change(
                ctx.user_id,
                "USER_ANSWER_STORED",
                {"for": a.get("currently_asking"), "answer_len": len(query)},
            )

        # Compute what's still missing
        still_missing = compute_still_missing(a, ctx)
        ask_first = [f for f in still_missing if is_user_slot(f)]
        fetch_later = [f for f in still_missing if not is_user_slot(f)]

        log.info(f"MISSING_COMPUTED | user={ctx.user_id} | ask_first={[get_func_value(f) for f in ask_first]} | fetch_later={[get_func_value(f) for f in fetch_later]}")

        # If we need to ask the user something, return QUESTION
        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value

            log.info(f"ASK_USER_QUESTION | user={ctx.user_id} | asking={func_value}")
            self.smart_log.user_question(ctx.user_id, func_value)
            self.ctx_mgr.save_context(ctx)

            return BotResponse(ResponseType.QUESTION, build_question(func, ctx))

        # Ask-loop is done. If background is needed (Recommendation only) and backend work remains
        needs_bg = bool(ctx.session.get("needs_background")) and Cfg.ENABLE_ASYNC
        if fetch_later and needs_bg:
            a["phase"] = "processing"
            log.info(f"DEFER_TO_BACKGROUND | user={ctx.user_id} | fetchers={[get_func_value(f) for f in fetch_later]}")
            
            self.smart_log.flow_decision(
                ctx.user_id,
                "DEFER_TO_BACKGROUND",
                {"fetchers": [get_func_value(f) for f in fetch_later]},
            )
            self.ctx_mgr.save_context(ctx)

            return BotResponse(
                ResponseType.PROCESSING_STUB,
                content={"message": "Processing your request…"},
            )

        # Complete synchronously
        log.info(f"COMPLETE_SYNC | user={ctx.user_id} | fetchers_needed={len(fetch_later)}")
        self.smart_log.flow_decision(
            ctx.user_id, "COMPLETE_ASSESSMENT", f"{len(fetch_later)} fetches needed"
        )
        return await self._complete_assessment_enhanced(a, ctx, fetch_later)

    async def _complete_assessment_enhanced(
        self,
        a: Dict[str, Any],
        ctx: UserContext,
        fetchers: List[Union[BackendFunction, UserSlot]],
    ) -> EnhancedBotResponse:
        """Enhanced assessment completion with proper fetcher persistence."""
        backend_fetchers = [f for f in fetchers if isinstance(f, BackendFunction)]
        log.info(f"COMPLETE_ASSESSMENT | user={ctx.user_id} | backend_fetchers={[f.value for f in backend_fetchers]}")

        # FIX: Execute backend fetchers with proper persistence
        fetched = await self._execute_fetchers_with_persistence(backend_fetchers, ctx)

        original_q = a.get("original_query", "")
        intent_l3 = ctx.session.get("intent_l3", "") or ""
        query_intent = map_leaf_to_query_intent(intent_l3)

        log.info(f"GENERATE_ANSWER | user={ctx.user_id} | intent_l3={intent_l3} | original_q='{original_q[:50]}...'")

        # Generate answer based on intent; force sync path when async disabled
        if intent_l3 == "Recommendation":
            log.info(f"GENERATE_ENHANCED | user={ctx.user_id} | using enhanced answer generation")
            answer_dict = await self._generate_enhanced_answer(original_q, ctx, fetched)
        else:
            log.info(f"GENERATE_SIMPLE | user={ctx.user_id} | using simple reply generation")
            answer_dict = await self.llm_service.generate_simple_reply(
                original_q, ctx, fetched, intent_l3=intent_l3, query_intent=query_intent
            )

        # Create enhanced response
        enhanced_response = await self._create_enhanced_response(
            answer_dict, list(fetched.keys()), original_q, ctx
        )

        # FIX: Clean up and persist final state with structured history
        self._finalize_assessment(a, ctx, original_q, enhanced_response)

        log.info(f"ASSESSMENT_COMPLETE | user={ctx.user_id} | response_type={enhanced_response.response_type.value} | requires_flow={enhanced_response.requires_flow}")
        self.smart_log.response_generated(
            ctx.user_id,
            enhanced_response.response_type.value,
            enhanced_response.requires_flow,
        )

        return enhanced_response

    # ────────────────────────────────────────────────────────
    # FIX: Enhanced fetcher execution with proper persistence
    # ────────────────────────────────────────────────────────

    async def _execute_fetchers_with_persistence(
        self,
        fetchers: List[BackendFunction],
        ctx: UserContext,
    ) -> Dict[str, Any]:
        """
        FIX: Execute fetchers and properly persist results to session:*:fetched.
        This addresses issue #4 from the diagnostic.
        """
        if fetchers:
            log.info(f"FETCHERS_START | user={ctx.user_id} | session={ctx.session_id} | fetchers={[f.value for f in fetchers]}")
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetchers])

        fetched: Dict[str, Any] = {}
        success_count = 0

        for func in fetchers:
            func_name = func.value
            log.info(f"FETCHER_EXECUTE | user={ctx.user_id} | fetcher={func_name}")
            
            try:
                result = await get_fetcher(func)(ctx)
                fetched[func_name] = result
                
                # FIX: Save to both fetched_data (in-memory) AND persist to Redis
                ctx.fetched_data[func_name] = {
                    "data": result,
                    "timestamp": datetime.now().isoformat(),
                    "status": "success"
                }
                
                # FIX: Merge into session:*:fetched (don't overwrite)
                fetched_key = f"session:{ctx.session_id}:fetched"
                existing_fetched = self.ctx_mgr._get_json(fetched_key, default={})
                existing_fetched[func_name] = {
                    "data": result,
                    "timestamp": datetime.now().isoformat(),
                    "status": "success"
                }
                self.ctx_mgr._set_json(fetched_key, existing_fetched, ttl=self.ctx_mgr.ttl)
                
                success_count += 1
                data_size = len(str(result)) if result else 0
                log.info(f"FETCHER_SUCCESS | user={ctx.user_id} | fetcher={func_name} | data_size={data_size}")
                
                self.smart_log.performance_metric(
                    ctx.user_id, func_name, data_size=data_size
                )

            except Exception as exc:
                log.error(f"FETCHER_FAILED | user={ctx.user_id} | fetcher={func_name} | error={exc}", exc_info=True)
                
                error_info = {"error": str(exc), "error_type": type(exc).__name__}
                fetched[func_name] = error_info
                
                # FIX: Persist error state as well
                ctx.fetched_data[func_name] = {
                    "data": None,
                    "error": str(exc),
                    "timestamp": datetime.now().isoformat(),
                    "status": "failed"
                }
                
                # FIX: Merge error into Redis as well
                fetched_key = f"session:{ctx.session_id}:fetched"
                existing_fetched = self.ctx_mgr._get_json(fetched_key, default={})
                existing_fetched[func_name] = {
                    "data": None,
                    "error": str(exc),
                    "timestamp": datetime.now().isoformat(),
                    "status": "failed"
                }
                self.ctx_mgr._set_json(fetched_key, existing_fetched, ttl=self.ctx_mgr.ttl)
                
                self.smart_log.warning(
                    ctx.user_id, "DATA_FETCH_FAILED", f"{func_name}: {exc}"
                )

        if fetchers:
            log.info(f"FETCHERS_COMPLETE | user={ctx.user_id} | success={success_count}/{len(fetchers)}")
            self.smart_log.data_operations(
                ctx.user_id, [f.value for f in fetchers], success_count
            )
            
            # FIX: Mark session as having completed data fetching
            if success_count > 0:
                ctx.session["last_fetch_timestamp"] = datetime.now().isoformat()
                ctx.session["needs_background"] = False  # No longer needs background processing
                
                # FIX: Mark assessment phase as done if all fetchers completed
                if "assessment" in ctx.session and success_count == len(fetchers):
                    ctx.session["assessment"]["phase"] = "done"
                    ctx.session["assessment"]["completed_at"] = datetime.now().isoformat()
                    log.info(f"ASSESSMENT_PHASE_DONE | user={ctx.user_id}")

        return fetched

    # ────────────────────────────────────────────────────────
    # FIX: Enhanced user answer storage with permanent persistence  
    # ────────────────────────────────────────────────────────

    def _store_user_answer_enhanced(self, query: str, assessment: Dict[str, Any], ctx: UserContext) -> None:
        """
        FIX: Store user answer with persistence to permanent user storage.
        This addresses issue #8 from the diagnostic.
        """
        currently_asking = assessment.get("currently_asking")
        if not currently_asking:
            return
            
        log.info(f"STORE_USER_ANSWER | user={ctx.user_id} | slot={currently_asking} | answer='{query[:50]}...'")
        
        # Store in session (existing behavior)
        store_user_answer(query, assessment, ctx)
        
        # FIX: Also store in permanent user profile
        try:
            user_key = f"user:{ctx.user_id}:permanent"
            permanent_data = self.ctx_mgr._get_json(user_key, default={})
            
            # Create user_answers section if it doesn't exist
            if "user_answers" not in permanent_data:
                permanent_data["user_answers"] = {}
            
            # Store the answer with metadata
            permanent_data["user_answers"][currently_asking] = {
                "value": query,
                "timestamp": datetime.now().isoformat(),
                "session_id": ctx.session_id
            }
            
            # FIX: For specific slots, also update top-level permanent data
            if currently_asking == "ASK_USER_PREFERENCES":
                permanent_data["preferences"] = query
                log.info(f"PERMANENT_PREFERENCES | user={ctx.user_id} | preferences='{query[:30]}...'")
            elif currently_asking == "ASK_USER_BUDGET":
                permanent_data["budget"] = query
                log.info(f"PERMANENT_BUDGET | user={ctx.user_id} | budget='{query}'")
            elif currently_asking == "ASK_DELIVERY_ADDRESS":
                permanent_data["address"] = query
                log.info(f"PERMANENT_ADDRESS | user={ctx.user_id} | address='{query[:30]}...'")
            
            # Update last_updated timestamp
            permanent_data["last_updated"] = datetime.now().isoformat()
            
            # Persist to Redis (no TTL for permanent data)
            self.ctx_mgr._set_json(user_key, permanent_data, ttl=None)
            
            log.info(f"USER_ANSWER_PERSISTED | user={ctx.user_id} | slot={currently_asking} | to_permanent=true")
            
        except Exception as e:
            log.error(f"USER_ANSWER_PERSIST_FAILED | user={ctx.user_id} | slot={currently_asking} | error={e}", exc_info=True)

    def _store_follow_up_answers(self, query: str, ctx: UserContext) -> None:
        """Store follow-up answers that might contain valuable user data."""
        try:
            # Simple heuristic: if query contains preferences, budget, or address info, store it
            query_lower = query.lower()
            
            user_key = f"user:{ctx.user_id}:permanent"
            permanent_data = self.ctx_mgr._get_json(user_key, default={})
            
            if "follow_up_data" not in permanent_data:
                permanent_data["follow_up_data"] = []
            
            # Store follow-up with context
            follow_up_entry = {
                "query": query,
                "timestamp": datetime.now().isoformat(),
                "session_id": ctx.session_id,
                "intent_l3": ctx.session.get("intent_l3")
            }
            
            permanent_data["follow_up_data"].append(follow_up_entry)
            
            # Keep only last 10 follow-ups
            permanent_data["follow_up_data"] = permanent_data["follow_up_data"][-10:]
            permanent_data["last_updated"] = datetime.now().isoformat()
            
            self.ctx_mgr._set_json(user_key, permanent_data, ttl=None)
            log.info(f"FOLLOW_UP_STORED | user={ctx.user_id} | query_len={len(query)}")
            
        except Exception as e:
            log.error(f"FOLLOW_UP_STORE_FAILED | user={ctx.user_id} | error={e}", exc_info=True)

    def _finalize_assessment(self, assessment: Dict[str, Any], ctx: UserContext, original_query: str, enhanced_response: EnhancedBotResponse = None) -> None:
        """Clean up assessment and persist final state with structured conversation history."""
        try:
            # FIX: Mark assessment as completed
            assessment["phase"] = "completed"
            assessment["completed_at"] = datetime.now().isoformat()
            
            # Build detailed internal_actions for structured history
            internal_actions = {
                "intent_classified": ctx.session.get("intent_l3") or ctx.session.get("intent_override"),
                "questions_asked": assessment.get("priority_order", []),
                "user_responses": {
                    k: ctx.session.get(k) for k in SLOT_TO_SESSION_KEY.values() 
                    if k in ctx.session and ctx.session.get(k)
                },
                "fetchers_executed": list(ctx.fetched_data.keys()),
                "fetched_data_details": {
                    k: {
                        "timestamp": v.get("timestamp") if isinstance(v, dict) else datetime.now().isoformat(),
                        "data_type": type(v.get("data") if isinstance(v, dict) else v).__name__,
                        "has_products": bool(
                            isinstance(v.get("data"), dict) and v.get("data", {}).get("products")
                        ) if isinstance(v, dict) else False,
                        "product_count": len(v.get("data", {}).get("products", [])) 
                            if isinstance(v, dict) and isinstance(v.get("data"), dict) 
                            and isinstance(v.get("data", {}).get("products"), list) else 0,
                        "products_summary": [
                            {
                                "title": p.get("title", "Unknown")[:50] + "..." if len(p.get("title", "")) > 50 else p.get("title", "Unknown"),
                                "price": p.get("price"),
                                "rating": p.get("rating")
                            } for p in (v.get("data", {}).get("products", []) if isinstance(v, dict) and isinstance(v.get("data"), dict) else [])[:3]
                        ] if isinstance(v, dict) and isinstance(v.get("data"), dict) and v.get("data", {}).get("products") else []
                    } for k, v in ctx.fetched_data.items()
                },
                "processing_metadata": {
                    "session_phase": assessment.get("phase"),
                    "completed_at": assessment.get("completed_at"),
                    "missing_data": assessment.get("missing_data", []),
                    "fulfilled": assessment.get("fulfilled", []),
                    "background_processing": ctx.session.get("needs_background", False)
                }
            }
            
            # Build final_answer details
            final_answer = {
                "response_type": enhanced_response.response_type.value if enhanced_response else "unknown",
                "message_preview": (enhanced_response.content.get("message", "")[:100] + "..." 
                                  if enhanced_response and len(enhanced_response.content.get("message", "")) > 100 
                                  else enhanced_response.content.get("message", "") if enhanced_response else ""),
                "message_full": enhanced_response.content.get("message", "") if enhanced_response else "",
                "has_sections": bool(enhanced_response and enhanced_response.content.get("sections")),
                "sections_summary": list(enhanced_response.content.get("sections", {}).keys()) if enhanced_response and enhanced_response.content.get("sections") else [],
                "has_products": bool(
                    enhanced_response 
                    and enhanced_response.flow_payload 
                    and getattr(enhanced_response.flow_payload, "products", None)
                ),
                "product_count": (
                    len(enhanced_response.flow_payload.products)
                    if enhanced_response and enhanced_response.flow_payload and enhanced_response.flow_payload.products
                    else 0
                ),
                "flow_triggered": enhanced_response.requires_flow if enhanced_response else False,
                "functions_executed": enhanced_response.functions_executed if enhanced_response else []
            }
            
            # Persist a compact last_recommendation snapshot for follow-ups (no refetch reuse)
            try:
                products_snapshot = []
                # Prefer Flow products
                if enhanced_response and enhanced_response.flow_payload and enhanced_response.flow_payload.products:
                    for p in enhanced_response.flow_payload.products[:8]:
                        try:
                            products_snapshot.append({
                                "title": getattr(p, "title", None),
                                "brand": getattr(p, "brand", None),
                                "price": getattr(p, "price", None),
                                "image_url": getattr(p, "image_url", None),
                                "rating": getattr(p, "rating", None),
                            })
                        except Exception:
                            continue
                # Fallback: derive from fetched ES results when no Flow
                if not products_snapshot:
                    try:
                        fetched_entry = (ctx.fetched_data or {}).get("search_products")
                        data_block = fetched_entry.get("data") if isinstance(fetched_entry, dict) and fetched_entry.get("data") else fetched_entry
                        items = (data_block or {}).get("products", []) if isinstance(data_block, dict) else []
                        for it in items[:8]:
                            try:
                                products_snapshot.append({
                                    "title": (it or {}).get("name"),
                                    "brand": (it or {}).get("brand"),
                                    "price": (it or {}).get("price"),
                                    "image_url": (it or {}).get("image"),
                                    "rating": (it or {}).get("rating"),
                                })
                            except Exception:
                                continue
                    except Exception:
                        pass

                if products_snapshot:
                    ctx.session["last_recommendation"] = {
                        "query": original_query,
                        "as_of": datetime.now().isoformat(),
                        "products": products_snapshot,
                    }
                    self.smart_log.memory_operation(ctx.user_id, "last_recommendation_stored", {"count": len(products_snapshot)})
            except Exception as e:
                log.warning(f"LAST_RECOMMENDATION_STORE_FAILED | user={ctx.user_id} | error={e}")

            # Clean up session state with structured history
            snapshot_and_trim(ctx, base_query=original_query, internal_actions=internal_actions, final_answer=final_answer)
            ctx.session.pop("assessment", None)
            ctx.session.pop("contextual_questions", None)
            ctx.session["needs_background"] = False
            
            # Save final context state
            self.ctx_mgr.save_context(ctx)
            
            log.info(f"ASSESSMENT_FINALIZED | user={ctx.user_id} | original_query='{original_query[:50]}...' | structured_history=true")
            
        except Exception as e:
            log.error(f"ASSESSMENT_FINALIZE_FAILED | user={ctx.user_id} | error={e}", exc_info=True)

    # ────────────────────────────────────────────────────────
    # Enhanced response creation (unchanged but with logging)
    # ────────────────────────────────────────────────────────

    async def _create_enhanced_response(
        self,
        answer_dict: Dict[str, Any],
        functions_executed: List[str],
        query: str,
        ctx: UserContext,
    ) -> EnhancedBotResponse:
        """Create enhanced response with detailed logging."""
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        message = answer_dict.get("message", "")
        sections = answer_dict.get("sections", {})

        log.info(f"CREATE_RESPONSE | user={ctx.user_id} | response_type={resp_type.value} | has_sections={bool(sections)}")

        base_content = {
            "message": message,
            **({"sections": sections} if sections else {}),
        }

        flow_payload: Optional[FlowPayload] = None
        requires_flow = False

        # Flow is gated strictly to current session intent being Recommendation
        # and only when async/flows are enabled
        if self.flow_enabled and Cfg.ENABLE_ASYNC and resp_type == ResponseType.FINAL_ANSWER:
            flow_payload = await self._create_flow_payload(answer_dict, query, ctx)
            requires_flow = flow_payload is not None
            log.info(f"FLOW_PAYLOAD_CREATED | user={ctx.user_id} | requires_flow={requires_flow}")

        # If flow is not created (flows disabled), carry structured_products in content for sync FE payload
        if not requires_flow:
            try:
                if isinstance(answer_dict.get("structured_products"), list) and answer_dict.get("structured_products"):
                    base_content["structured_products"] = answer_dict.get("structured_products")
            except Exception:
                pass

        return EnhancedBotResponse(
            response_type=resp_type,
            content=base_content,
            functions_executed=functions_executed,
            flow_payload=flow_payload,
            requires_flow=requires_flow,
        )

    async def _create_flow_payload(
        self,
        answer_dict: Dict[str, Any],
        query: str,
        ctx: UserContext,
    ) -> Optional[FlowPayload]:
        """Create Flow payload ONLY for Recommendation with logging."""
        intent_l3 = ctx.session.get("intent_l3", "") or ""
        if intent_l3 != "Recommendation":
            log.info(f"FLOW_SKIPPED | user={ctx.user_id} | intent_l3={intent_l3} | not_recommendation")
            return None

        structured_products = answer_dict.get("structured_products", [])
        flow_context = answer_dict.get("flow_context", {})

        log.info(f"FLOW_DATA_EXTRACT | user={ctx.user_id} | structured_products={len(structured_products)} | flow_context_keys={list(flow_context.keys())}")

        # If LLM already gave structured products, use them
        if structured_products:
            products: List[ProductData] = []
            for i, product_dict in enumerate(structured_products):
                try:
                    if isinstance(product_dict, ProductData):
                        products.append(product_dict)
                    else:
                        product = ProductData(
                            product_id=product_dict.get(
                                "product_id",
                                f"prod_{hash(product_dict.get('title', ''))%100000}",
                            ),
                            title=product_dict["title"],
                            subtitle=product_dict["subtitle"],
                            price=product_dict["price"],
                            rating=product_dict.get("rating"),
                            image_url=product_dict.get(
                                "image_url",
                                "https://via.placeholder.com/200x200?text=Product",
                            ),
                            brand=product_dict.get("brand"),
                            key_features=product_dict.get("key_features", []),
                            availability=product_dict.get("availability", "In Stock"),
                            discount=product_dict.get("discount"),
                        )
                        products.append(product)
                        log.debug(f"PRODUCT_CREATED | user={ctx.user_id} | product_{i}={product.title}")
                except Exception as e:
                    log.warning(f"PRODUCT_CREATE_FAILED | user={ctx.user_id} | product_{i} | error={e}")
                    continue

            if not products:
                log.warning(f"FLOW_NO_PRODUCTS | user={ctx.user_id} | structured_products_failed")
                return None

            header_text = flow_context.get("header_text", "Recommended for you")
            reason = flow_context.get("reason", "")
            log.info(f"FLOW_GENERATED | user={ctx.user_id} | products_count={len(products)} | header='{header_text[:30]}...'")
            
            return self.flow_generator.generate_recommendation_flow(
                products, reason, header_text
            )

        # Otherwise, attempt to derive a Flow from the "sections"
        sections = answer_dict.get("sections", {})
        if not sections:
            log.info(f"FLOW_NO_SECTIONS | user={ctx.user_id}")
            return None
            
        flow_payload = self.flow_generator.create_flow_from_sections(sections)
        if flow_payload and self.flow_generator.validate_flow_payload(flow_payload):
            log.info(f"FLOW_FROM_SECTIONS | user={ctx.user_id} | flow_type={flow_payload.flow_type}")
            return flow_payload
            
        log.info(f"FLOW_SECTIONS_INVALID | user={ctx.user_id}")
        return None

    # ────────────────────────────────────────────────────────
    # Helper methods (with enhanced logging)
    # ────────────────────────────────────────────────────────

    async def _generate_enhanced_answer(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate enhanced answer with structured data."""
        if self.enhanced_llm_enabled and hasattr(
            self.llm_service, "generate_enhanced_answer"
        ):
            try:
                log.info(f"ENHANCED_ANSWER | user={ctx.user_id} | using enhanced LLM")
                return await self.llm_service.generate_enhanced_answer(
                    query, ctx, fetched
                )
            except Exception as e:
                log.warning(f"ENHANCED_ANSWER_FAILED | user={ctx.user_id} | error={e} | falling back")

        log.info(f"ENHANCED_ANSWER_FALLBACK | user={ctx.user_id} | using standard answer")
        return await self.llm_service.generate_answer(query, ctx, fetched)

    def _apply_follow_up_patch(self, patch, ctx: UserContext) -> None:
        """Apply follow-up patch with logging."""
        changes = {}

        for k, v in patch.slots.items():
            old_value = ctx.session.get(k)
            ctx.session[k] = v
            changes[k] = f"{old_value}→{v}"

        if patch.intent_override:
            ctx.session["intent_override"] = patch.intent_override
            changes["intent"] = patch.intent_override

        if changes:
            log.info(f"PATCH_APPLIED | user={ctx.user_id} | changes={changes}")
            self.smart_log.context_change(ctx.user_id, "PATCH_APPLIED", changes)

    def _reset_session_only(self, ctx: UserContext) -> None:
        """Reset session with logging."""
        cleared_items = len(ctx.session) + len(ctx.fetched_data)
        ctx.session.clear()
        ctx.fetched_data.clear()
        
        log.info(f"SESSION_RESET | user={ctx.user_id} | cleared_items={cleared_items}")
        self.smart_log.context_change(
            ctx.user_id, "SESSION_RESET", {"cleared_items": cleared_items}
        )

    # ────────────────────────────────────────────────────────
    # Feature toggles and utilities
    # ────────────────────────────────────────────────────────

    def enable_flows(self, enabled: bool = True) -> None:
        self.flow_enabled = enabled
        log.info(f"FLOW_TOGGLE | enabled={enabled}")

    def enable_enhanced_llm(self, enabled: bool = True) -> None:
        self.enhanced_llm_enabled = enabled
        log.info(f"ENHANCED_LLM_TOGGLE | enabled={enabled}")

    def get_flow_stats(self, ctx: UserContext) -> Dict[str, Any]:
        """Flow stats for debugging."""
        return {
            "flow_enabled": self.flow_enabled,
            "enhanced_llm_enabled": self.enhanced_llm_enabled,
            "session_has_flow_history": "flow_history" in ctx.session,
            "total_queries": len(ctx.session.get("history", [])),
            "flow_capable_responses": sum(
                1 for h in ctx.session.get("history", []) if h.get("had_flow", False)
            ),
        }

    def _needs_background(self, intent) -> bool:
        """ONLY Recommendation defers to background (for Flow)."""
        try:
            result = str(intent).lower() in {"queryintent.recommendation"}
            log.debug(f"NEEDS_BACKGROUND | intent={intent} | result={result}")
            return result
        except Exception:
            return False