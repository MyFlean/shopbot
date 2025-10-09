# shopping_bot/enhanced_core.py
"""
Enhanced ShoppingBotCore with UX Response Integration
────────────────────────────────────────────────────
Extends the original ShoppingBotCore to support the new UX intent patterns
while maintaining full backward compatibility.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Union

from .config import get_config
from .enums import ResponseType, BackendFunction, UserSlot, EnhancedResponseType
from .models import UserContext, EnhancedBotResponse, BotResponse
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
from .enhanced_llm_service import get_enhanced_llm_service, get_ux_decision_engine
from .llm_service import map_leaf_to_query_intent

# Import original core for backward compatibility
from .bot_core import ShoppingBotCore

Cfg = get_config()
log = logging.getLogger(__name__)


class EnhancedShoppingBotCore(ShoppingBotCore):
    """
    Enhanced shopping bot core with UX response capabilities.
    Extends the original core while maintaining backward compatibility.
    """
    
    def __init__(self, context_mgr: RedisContextManager) -> None:
        super().__init__(context_mgr)
        self.enhanced_llm_service = get_enhanced_llm_service()
        self.ux_decision_engine = get_ux_decision_engine()
        self.smart_log = get_smart_logger("enhanced_bot_core")
        
        # Feature flags
        self.ux_patterns_enabled = Cfg.get("UX_PATTERNS_ENABLED", True)
        self.ux_rollout_percentage = Cfg.get("UX_ROLLOUT_PERCENTAGE", 100)
    
    # ────────────────────────────────────────────────────────
    # Enhanced public entry point
    # ────────────────────────────────────────────────────────
    async def process_query_enhanced(
        self, 
        query: str, 
        ctx: UserContext,
        use_ux_patterns: bool = None
    ) -> EnhancedBotResponse:
        """
        Enhanced query processing with UX pattern support.
        
        Args:
            query: User query string
            ctx: User context
            use_ux_patterns: Override for UX pattern usage (None = auto-decide)
        """
        self.smart_log.query_start(ctx.user_id, query, bool(ctx.session))
        
        # Determine if UX patterns should be used
        if use_ux_patterns is None:
            use_ux_patterns = self._should_enable_ux_patterns(ctx)
        
        try:
            # Process using original logic but with enhanced responses
            if "assessment" in ctx.session:
                self.smart_log.flow_decision(ctx.user_id, "CONTINUE_ASSESSMENT_ENHANCED")
                return await self._continue_assessment_enhanced(query, ctx, use_ux_patterns)
            
            # Follow-up classification
            fu = await self.llm_service.classify_follow_up(query, ctx)
            if fu.is_follow_up and not fu.patch.reset_context:
                effective_l3 = fu.patch.intent_override or ctx.session.get("intent_l3", "")
                self.smart_log.follow_up_decision(
                    ctx.user_id, "HANDLE_FOLLOW_UP_ENHANCED", effective_l3, fu.reason
                )
                self._apply_follow_up_patch(fu.patch, ctx)
                return await self._handle_follow_up_enhanced(query, ctx, fu, use_ux_patterns)
            
            # New or reset assessment
            if fu.patch.reset_context:
                self.smart_log.flow_decision(ctx.user_id, "RESET_CONTEXT_ENHANCED")
                self._reset_session_only(ctx)
            else:
                self.smart_log.flow_decision(ctx.user_id, "NEW_ASSESSMENT_ENHANCED")
            
            return await self._start_new_assessment_enhanced(query, ctx, use_ux_patterns)
            
        except Exception as exc:
            self.smart_log.error_occurred(
                ctx.user_id, type(exc).__name__, "process_query_enhanced", str(exc)
            )
            return EnhancedBotResponse(
                response_type=EnhancedResponseType.ERROR,
                content={"message": "Sorry, something went wrong.", "error": str(exc)},
            )
    
    # ────────────────────────────────────────────────────────
    # Enhanced follow-up handling
    # ────────────────────────────────────────────────────────
    async def _handle_follow_up_enhanced(
        self, 
        query: str, 
        ctx: UserContext, 
        fu,
        use_ux_patterns: bool
    ) -> EnhancedBotResponse:
        """Enhanced follow-up handling with UX patterns"""
        
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
                ctx.user_id, "DEFER_TO_BACKGROUND_FOLLOW_UP_ENHANCED", {"intent_l3": effective_l3}
            )
            return EnhancedBotResponse(
                response_type=EnhancedResponseType.PROCESSING_STUB,
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
        
        # Generate enhanced or standard response
        if use_ux_patterns:
            # Set flag for enhanced LLM service
            ctx._use_enhanced_response = True
            
            enhanced_response = await self.enhanced_llm_service.generate_enhanced_response(
                query,
                ctx,
                fetched,
                intent_l3=effective_l3,
                query_intent=map_leaf_to_query_intent(effective_l3),
            )
            enhanced_response.functions_executed = list(fetched.keys())
        else:
            # Standard response wrapped as enhanced
            answer_dict = await self.llm_service.generate_response(
                query,
                ctx,
                fetched,
                intent_l3=effective_l3,
                query_intent=map_leaf_to_query_intent(effective_l3),
            )
            
            enhanced_response = self._wrap_standard_response(answer_dict, fetched)
        
        # Store snapshot for follow-ups
        self._store_last_recommendation(query, ctx, fetched)
        
        # Final-answer summary for memory
        final_answer_summary = {
            "response_type": enhanced_response.response_type.value,
            "message_preview": str(enhanced_response.content.get("message", ""))[:300],
            "has_sections": False,
            "has_products": bool(enhanced_response.content.get("products")),
            "flow_triggered": False,
            "ux_enhanced": use_ux_patterns,
        }
        
        snapshot_and_trim(ctx, base_query=query, final_answer=final_answer_summary)
        self.ctx_mgr.save_context(ctx)
        self.smart_log.response_generated(ctx.user_id, enhanced_response.response_type.value, use_ux_patterns)
        
        return enhanced_response
    
    # ────────────────────────────────────────────────────────
    # Enhanced new assessment
    # ────────────────────────────────────────────────────────
    async def _start_new_assessment_enhanced(
        self, 
        query: str, 
        ctx: UserContext,
        use_ux_patterns: bool
    ) -> EnhancedBotResponse:
        """Enhanced new assessment handling"""
        
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
        ctx.session["use_ux_patterns"] = use_ux_patterns  # Store decision
        
        self.smart_log.flow_decision(
            ctx.user_id,
            "BACKGROUND_DECISION_ENHANCED",
            {
                "needs_background": needs_bg, 
                "intent": intent.value, 
                "intent_l3": result.layer3,
                "ux_patterns": use_ux_patterns
            },
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
        
        # Seed canonical query and clear stale params
        try:
            ctx.session["canonical_query"] = query
            ctx.session["last_query"] = query
            dbg = ctx.session.setdefault("debug", {})
            dbg["last_search_params"] = {}
        except Exception:
            pass
        
        # FIX: Clear product-specific slots to prevent pollution across product switches
        try:
            product_slots_to_clear = [
                # Generic user slot answers
                "preferences", "budget", "dietary_requirements", "use_case", "product_category", "quantity",
                # Price filters & taxonomy hints
                "price_min", "price_max", "category_group", "category_paths", "category_path",
                # Personal care specific session keys
                "pc_concern", "pc_compatibility", "ingredient_avoid",
                # Legacy/aux PC hints used by planners
                "skin_types_slot", "hair_types_slot", "efficacy_terms_slot", 
                "avoid_terms_slot", "pc_keywords_slot", "pc_must_keywords_slot"
            ]
            for slot_key in product_slots_to_clear:
                ctx.session.pop(slot_key, None)
            self.smart_log.context_change(ctx.user_id, "PRODUCT_SLOTS_CLEARED", {"slots": product_slots_to_clear})
        except Exception:
            pass
        
        self.ctx_mgr.save_context(ctx)
        return await self._continue_assessment_enhanced(query, ctx, use_ux_patterns)
    
    # ────────────────────────────────────────────────────────
    # Enhanced assessment continuation
    # ────────────────────────────────────────────────────────
    async def _continue_assessment_enhanced(
        self, 
        query: str, 
        ctx: UserContext,
        use_ux_patterns: bool
    ) -> EnhancedBotResponse:
        """Enhanced assessment continuation"""
        
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
            
            # Return question as enhanced response
            question_content = build_question(func, ctx)
            return EnhancedBotResponse(
                response_type=EnhancedResponseType.QUESTION,
                content=question_content
            )
        
        needs_bg = bool(ctx.session.get("needs_background")) and Cfg.ENABLE_ASYNC
        if fetch_later and needs_bg:
            a["phase"] = "processing"
            self.smart_log.flow_decision(
                ctx.user_id, "DEFER_TO_BACKGROUND_ENHANCED",
                {"fetchers": [get_func_value(f) for f in fetch_later]}
            )
            self.ctx_mgr.save_context(ctx)
            return EnhancedBotResponse(
                response_type=EnhancedResponseType.PROCESSING_STUB,
                content={"message": "Processing your request…"}
            )
        
        self.smart_log.flow_decision(
            ctx.user_id, "COMPLETE_ASSESSMENT_ENHANCED", f"{len(fetch_later)} fetches needed"
        )
        return await self._complete_assessment_enhanced(a, ctx, fetch_later, use_ux_patterns)
    
    # ────────────────────────────────────────────────────────
    # Enhanced assessment completion
    # ────────────────────────────────────────────────────────
    async def _complete_assessment_enhanced(
        self,
        a: Dict[str, Any],
        ctx: UserContext,
        fetchers: List[Union[BackendFunction, UserSlot]],
        use_ux_patterns: bool
    ) -> EnhancedBotResponse:
        """Enhanced assessment completion with UX patterns"""
        
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
        
        # Generate enhanced or standard response
        original_q = safe_get(a, "original_query", "")
        intent_l3 = ctx.session.get("intent_l3", "") or ""
        
        if use_ux_patterns:
            # Set flag for enhanced LLM service
            ctx._use_enhanced_response = True
            
            enhanced_response = await self.enhanced_llm_service.generate_enhanced_response(
                original_q,
                ctx,
                fetched,
                intent_l3=intent_l3,
                query_intent=map_leaf_to_query_intent(intent_l3),
            )
            enhanced_response.functions_executed = list(fetched.keys())
        else:
            # Standard response wrapped as enhanced
            answer_dict = await self.llm_service.generate_response(
                original_q,
                ctx,
                fetched,
                intent_l3=intent_l3,
                query_intent=map_leaf_to_query_intent(intent_l3),
            )
            
            enhanced_response = self._wrap_standard_response(answer_dict, fetched)
        
        # Store last_recommendation for follow-ups if products were fetched
        self._store_last_recommendation(original_q, ctx, fetched)
        
        # Final-answer summary → memory
        final_answer_summary = {
            "response_type": enhanced_response.response_type.value,
            "message_preview": str(enhanced_response.content.get("message", ""))[:300],
            "has_sections": False,
            "has_products": bool(enhanced_response.content.get("products")),
            "flow_triggered": False,
            "ux_enhanced": use_ux_patterns,
        }
        
        snapshot_and_trim(ctx, base_query=original_q, final_answer=final_answer_summary)
        ctx.session.pop("assessment", None)
        ctx.session.pop("contextual_questions", None)
        self.ctx_mgr.save_context(ctx)
        
        self.smart_log.response_generated(ctx.user_id, enhanced_response.response_type.value, use_ux_patterns)
        
        return enhanced_response
    
    # ────────────────────────────────────────────────────────
    # Backward compatibility methods
    # ────────────────────────────────────────────────────────
    async def process_query(self, query: str, ctx: UserContext) -> 'BotResponse':
        """
        Backward compatibility method that returns legacy BotResponse.
        Internally uses enhanced processing but converts to legacy format.
        """
        enhanced_response = await self.process_query_enhanced(query, ctx, use_ux_patterns=False)
        return enhanced_response.to_legacy_response()
    
    # ────────────────────────────────────────────────────────
    # Helper methods
    # ────────────────────────────────────────────────────────
    def _should_enable_ux_patterns(self, ctx: UserContext) -> bool:
        """
        Decision logic for enabling UX patterns.
        Can be made more sophisticated with A/B testing, user preferences, etc.
        """
        
        # Feature flag check
        if not self.ux_patterns_enabled:
            return False
        
        # Rollout percentage check
        if self.ux_rollout_percentage < 100:
            user_hash = hash(ctx.user_id) % 100
            if user_hash >= self.ux_rollout_percentage:
                return False
        
        # Additional logic can be added here:
        # - User preferences
        # - Session context
        # - Performance considerations
        # - Error rate monitoring
        
        return True
    
    def _wrap_standard_response(
        self, 
        standard_response: Dict[str, Any], 
        fetched: Dict[str, Any]
    ) -> EnhancedBotResponse:
        """Wrap standard response dict as enhanced response"""
        
        # Map response type
        response_type_mapping = {
            "final_answer": EnhancedResponseType.CASUAL,
            "error": EnhancedResponseType.ERROR,
            "question": EnhancedResponseType.QUESTION,
            "processing": EnhancedResponseType.PROCESSING_STUB
        }
        
        response_type_str = standard_response.get("response_type", "final_answer")
        enhanced_type = response_type_mapping.get(response_type_str, EnhancedResponseType.CASUAL)
        
        return EnhancedBotResponse(
            response_type=enhanced_type,
            content=standard_response,
            functions_executed=list(fetched.keys())
        )


# Global instance for dependency injection
_enhanced_core = None


def get_enhanced_shopping_bot_core(context_mgr: RedisContextManager) -> EnhancedShoppingBotCore:
    """Get enhanced shopping bot core instance"""
    global _enhanced_core
    if _enhanced_core is None:
        _enhanced_core = EnhancedShoppingBotCore(context_mgr)
    return _enhanced_core