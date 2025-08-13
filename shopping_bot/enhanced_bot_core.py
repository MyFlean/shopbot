"""
Enhanced ShoppingBotCore with WhatsApp Flow support
─────────────────────────────────────────────────────
Create this as shopping_bot/enhanced_bot_core.py

This wraps your existing ShoppingBotCore and adds Flow functionality
while maintaining full backward compatibility.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Union, Optional

from .config import get_config
from .enums import ResponseType, BackendFunction, UserSlot
from .models import BotResponse, UserContext, EnhancedBotResponse, ProductData, FlowPayload, FlowType
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
    sections_to_text,
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
    """Enhanced bot core with Flow support that wraps existing ShoppingBotCore"""
    
    def __init__(self, base_bot_core: 'ShoppingBotCore'):
        """Initialize with existing bot core"""
        self.base_core = base_bot_core
        self.ctx_mgr = base_bot_core.ctx_mgr
        self.llm_service = base_bot_core.llm_service
        self.smart_log = base_bot_core.smart_log
        
        # Enhanced components
        self.flow_generator = FlowTemplateGenerator()
        
        # Feature flags
        self.flow_enabled = True
        self.enhanced_llm_enabled = True

    def enable_flows(self, enabled: bool = True):
        """Enable or disable Flow functionality"""
        self.flow_enabled = enabled
        
    def enable_enhanced_llm(self, enabled: bool = True):
        """Enable or disable enhanced LLM features"""
        self.enhanced_llm_enabled = enabled

    def _reset_session_only(self, ctx: UserContext) -> None:
        """Reset session data only"""
        cleared_items = len(ctx.session) + len(ctx.fetched_data)
        ctx.session.clear()
        ctx.fetched_data.clear()
        
        self.smart_log.context_change(ctx.user_id, "SESSION_RESET", {"cleared_items": cleared_items})

    def _apply_follow_up_patch(self, patch, ctx: UserContext) -> None:
        """Apply follow-up patch"""
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

    # ────────────────────────────────────────────────────────
    # Public entry points
    # ────────────────────────────────────────────────────────
    
    async def collect_questions_for_query(
        self,
        query: str,
        ctx: UserContext
    ) -> Union[BotResponse, None]:
        """
        Phase 1: Collect any required questions without processing.
        Returns None if no questions needed, or BotResponse with questions.
        """
        self.smart_log.query_start(ctx.user_id, query, bool(ctx.session))
        
        try:
            # If we are mid-assessment, continue with questions
            if "assessment" in ctx.session:
                self.smart_log.flow_decision(ctx.user_id, "CONTINUE_ASSESSMENT_QUESTIONS")
                return await self._continue_assessment_questions_only(query, ctx)

            # Check if this is a follow-up (reuse existing logic)
            fu = await self.llm_service.classify_follow_up(query, ctx)
            
            if fu.is_follow_up and not fu.patch.reset_context:
                # Follow-ups typically don't need new questions
                return None

            # Reset or start fresh assessment
            if fu.patch.reset_context:
                self.smart_log.flow_decision(ctx.user_id, "RESET_CONTEXT")
                self._reset_session_only(ctx)
            
            # Start new assessment and check for questions
            return await self._start_assessment_questions_only(query, ctx)

        except Exception as exc:
            self.smart_log.error_occurred(ctx.user_id, type(exc).__name__, "collect_questions", str(exc))
            return None

    async def process_query(
        self, 
        query: str, 
        ctx: UserContext, 
        enable_flows: bool = True
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """
        Enhanced process_query that can return either BotResponse or EnhancedBotResponse
        """
        self.smart_log.query_start(ctx.user_id, query, bool(ctx.session))
        
        # If flows are disabled, use base core
        if not self.flow_enabled or not enable_flows:
            return await self.base_core.process_query(query, ctx)
        
        try:
            # Handle assessment continuation (unchanged logic)
            if "assessment" in ctx.session:
                self.smart_log.flow_decision(ctx.user_id, "CONTINUE_ASSESSMENT")
                return await self._continue_assessment_enhanced(query, ctx)

            # Handle follow-ups
            fu = await self.llm_service.classify_follow_up(query, ctx)
            
            if fu.is_follow_up and not fu.patch.reset_context:
                self.smart_log.flow_decision(ctx.user_id, "HANDLE_FOLLOW_UP")
                self._apply_follow_up_patch(fu.patch, ctx)
                return await self._handle_follow_up_enhanced(query, ctx, fu)

            # Reset or start fresh
            if fu.patch.reset_context:
                self.smart_log.flow_decision(ctx.user_id, "RESET_CONTEXT")
                self._reset_session_only(ctx)
            else:
                self.smart_log.flow_decision(ctx.user_id, "NEW_ASSESSMENT")

            return await self._start_new_assessment_enhanced(query, ctx)

        except Exception as exc:
            self.smart_log.error_occurred(ctx.user_id, type(exc).__name__, "process_query", str(exc))
            return BotResponse(
                ResponseType.ERROR,
                {"message": "Sorry, something went wrong.", "error": str(exc)},
            )

    async def process_query_legacy(self, query: str, ctx: UserContext) -> BotResponse:
        """Legacy interface that always returns BotResponse"""
        result = await self.process_query(query, ctx, enable_flows=False)
        
        if isinstance(result, EnhancedBotResponse):
            return result.to_legacy_bot_response()
        
        return result

    # ────────────────────────────────────────────────────────
    # Enhanced processing methods
    # ────────────────────────────────────────────────────────

    async def _handle_follow_up_enhanced(
        self, 
        query: str, 
        ctx: UserContext, 
        fu
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """Enhanced follow-up handling with Flow support"""
        
        # Assess what additional data we need
        fetch_list = await self.llm_service.assess_delta_requirements(query, ctx, fu.patch)
        
        if fetch_list:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetch_list])

        # Fetch the data
        fetched = await self._execute_fetchers(fetch_list, ctx)

        # Generate enhanced answer with structured product data
        answer_dict = await self._generate_enhanced_answer(query, ctx, fetched)
        
        # Create enhanced response with potential Flow
        enhanced_response = await self._create_enhanced_response(
            answer_dict, list(fetched.keys()), query, ctx
        )

        # Save context and return
        snapshot_and_trim(ctx, base_query=query)
        self.ctx_mgr.save_context(ctx)
        
        self.smart_log.response_generated(
            ctx.user_id, 
            enhanced_response.response_type.value, 
            enhanced_response.requires_flow
        )

        return enhanced_response

    async def _start_new_assessment_enhanced(
        self, 
        query: str, 
        ctx: UserContext
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """Enhanced new assessment with Flow support"""
        
        # Use base logic for intent classification and requirements assessment
        result = await self.llm_service.classify_intent(query)
        from .llm_service import map_leaf_to_query_intent
        intent = map_leaf_to_query_intent(result.layer3)
        
        self.smart_log.intent_classified(
            ctx.user_id, 
            (result.layer1, result.layer2, result.layer3),
            intent.value
        )

        # Update session
        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )

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
        return await self._continue_assessment_enhanced(query, ctx)

    async def _continue_assessment_enhanced(
        self, 
        query: str, 
        ctx: UserContext
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """Enhanced assessment continuation"""
        
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

        # If we need to ask the user something, return legacy BotResponse
        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value
            
            self.smart_log.user_question(ctx.user_id, func_value)
            self.ctx_mgr.save_context(ctx)
            
            return BotResponse(ResponseType.QUESTION, build_question(func, ctx))

        # Complete the assessment with enhanced response
        self.smart_log.flow_decision(ctx.user_id, "COMPLETE_ASSESSMENT", f"{len(fetch_later)} fetches needed")
        return await self._complete_assessment_enhanced(a, ctx, fetch_later)

    # ────────────────────────────────────────────────────────
    # Question-only methods for two-phase processing
    # ────────────────────────────────────────────────────────
    
    async def _start_assessment_questions_only(
        self, 
        query: str, 
        ctx: UserContext
    ) -> Union[BotResponse, None]:
        """Start assessment and return only questions if needed"""
        
        # Classify intent (same as normal flow)
        result = await self.llm_service.classify_intent(query)
        intent = map_leaf_to_query_intent(result.layer3)
        
        self.smart_log.intent_classified(
            ctx.user_id, 
            (result.layer1, result.layer2, result.layer3),
            intent.value
        )

        # Update session
        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )

        # Assess requirements
        assessment = await self.llm_service.assess_requirements(query, intent, result.layer3, ctx)
        
        user_slots = [f for f in assessment.missing_data if is_user_slot(f)]
        missing_data_names = [get_func_value(f) for f in assessment.missing_data]
        ask_first_names = [get_func_value(f) for f in user_slots]
        
        self.smart_log.requirements_assessed(ctx.user_id, missing_data_names, ask_first_names)

        # If no user questions needed, return None
        if not user_slots:
            return None

        # Generate contextual questions
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
            "phase": "collecting_questions"  # Mark this as question collection phase
        }

        self.ctx_mgr.save_context(ctx)
        
        # Return the first question
        return await self._continue_assessment_questions_only(query, ctx)

    async def _continue_assessment_questions_only(
        self, 
        query: str, 
        ctx: UserContext
    ) -> Union[BotResponse, None]:
        """Continue assessment but only return questions"""
        
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

        # If we need to ask the user something
        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value
            
            self.smart_log.user_question(ctx.user_id, func_value)
            self.ctx_mgr.save_context(ctx)
            
            return BotResponse(ResponseType.QUESTION, build_question(func, ctx))

        # No more questions needed - mark as ready for processing
        a["phase"] = "ready_for_processing"
        self.ctx_mgr.save_context(ctx)
        return None

    async def _complete_assessment_enhanced(
        self,
        a: Dict[str, Any],
        ctx: UserContext,
        fetchers: List[Union[BackendFunction, UserSlot]],
    ) -> EnhancedBotResponse:
        """Enhanced assessment completion with Flow support"""
        
        backend_fetchers = [f for f in fetchers if isinstance(f, BackendFunction)]
        
        # Execute backend fetchers
        fetched = await self._execute_fetchers(backend_fetchers, ctx)

        # Generate enhanced answer
        original_q = a.get("original_query", "")
        answer_dict = await self._generate_enhanced_answer(original_q, ctx, fetched)

        # Create enhanced response
        enhanced_response = await self._create_enhanced_response(
            answer_dict, list(fetched.keys()), original_q, ctx
        )

        # Clean up
        snapshot_and_trim(ctx, base_query=original_q)
        ctx.session.pop("assessment", None)
        ctx.session.pop("contextual_questions", None)
        self.ctx_mgr.save_context(ctx)

        self.smart_log.response_generated(
            ctx.user_id, 
            enhanced_response.response_type.value, 
            enhanced_response.requires_flow
        )

        return enhanced_response

    # ────────────────────────────────────────────────────────
    # Enhanced response creation
    # ────────────────────────────────────────────────────────

    async def _create_enhanced_response(
        self, 
        answer_dict: Dict[str, Any], 
        functions_executed: List[str],
        query: str,
        ctx: UserContext
    ) -> EnhancedBotResponse:
        """Create enhanced response with potential Flow support"""
        
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        message = answer_dict.get("message", "")
        sections = answer_dict.get("sections", {})
        
        # Create base content
        base_content = {
            "message": message,
            "sections": sections,
        }
        
        flow_payload = None
        requires_flow = False
        
        # Check if we should create a Flow
        if self.flow_enabled and resp_type == ResponseType.FINAL_ANSWER:
            flow_payload = await self._create_flow_payload(answer_dict, query, ctx)
            requires_flow = flow_payload is not None
        
        # Create enhanced response
        return EnhancedBotResponse(
            response_type=resp_type,
            content=base_content,
            functions_executed=functions_executed,
            flow_payload=flow_payload,
            requires_flow=requires_flow
        )

    async def _create_flow_payload(
        self, 
        answer_dict: Dict[str, Any], 
        query: str, 
        ctx: UserContext
    ) -> Optional[FlowPayload]:
        """Create Flow payload if appropriate"""
        
        # Get structured products from answer
        structured_products = answer_dict.get("structured_products", [])
        flow_context = answer_dict.get("flow_context", {})
        
        if not structured_products:
            # Try to extract from sections
            sections = answer_dict.get("sections", {})
            flow_payload = self.flow_generator.create_flow_from_sections(sections)
            if flow_payload and self.flow_generator.validate_flow_payload(flow_payload):
                return flow_payload
            return None
        
        # Convert to ProductData objects
        products = []
        for product_dict in structured_products:
            try:
                # Handle both ProductData objects and dicts
                if isinstance(product_dict, ProductData):
                    products.append(product_dict)
                else:
                    product = ProductData(
                        product_id=product_dict.get("product_id", f"prod_{hash(product_dict.get('title', ''))%100000}"),
                        title=product_dict["title"],
                        subtitle=product_dict["subtitle"],
                        price=product_dict["price"],
                        rating=product_dict.get("rating"),
                        image_url=product_dict.get("image_url", "https://via.placeholder.com/200x200?text=Product"),
                        brand=product_dict.get("brand"),
                        key_features=product_dict.get("key_features", []),
                        availability=product_dict.get("availability", "In Stock"),
                        discount=product_dict.get("discount")
                    )
                    products.append(product)
            except Exception as e:
                log.warning(f"Failed to create ProductData: {e}")
                continue
        
        if not products:
            return None
        
        # Determine Flow type and create payload
        intent_layer3 = ctx.session.get("intent_l3", "")
        flow_type = self._determine_flow_type(flow_context, intent_layer3, query)
        
        header_text = flow_context.get("header_text", "Product Options")
        reason = flow_context.get("reason", "")
        
        if flow_type == FlowType.RECOMMENDATION:
            return self.flow_generator.generate_recommendation_flow(
                products, reason, header_text
            )
        elif flow_type == FlowType.COMPARISON:
            return self.flow_generator.generate_comparison_flow(
                products, ["price", "rating", "features"], header_text
            )
        else:
            return self.flow_generator.generate_product_catalog_flow(
                products, query, header_text
            )

    # ────────────────────────────────────────────────────────
    # Helper methods
    # ────────────────────────────────────────────────────────

    async def _execute_fetchers(
        self, 
        fetchers: List[BackendFunction], 
        ctx: UserContext
    ) -> Dict[str, Any]:
        """Execute backend fetchers and return results"""
        
        if fetchers:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetchers])
        
        fetched = {}
        success_count = 0
        
        for func in fetchers:
            try:
                result = await get_fetcher(func)(ctx)
                fetched[func.value] = result
                ctx.fetched_data[func.value] = {
                    "data": result,
                    "timestamp": datetime.now().isoformat(),
                }
                success_count += 1
                
                self.smart_log.performance_metric(
                    ctx.user_id, func.value, 
                    data_size=len(str(result)) if result else 0
                )
                
            except Exception as exc:
                self.smart_log.warning(ctx.user_id, f"DATA_FETCH_FAILED", f"{func.value}: {exc}")
                fetched[func.value] = {"error": str(exc)}

        if fetchers:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetchers], success_count)
        
        return fetched

    async def _generate_enhanced_answer(
        self, 
        query: str, 
        ctx: UserContext, 
        fetched: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate enhanced answer with structured data"""
        
        if self.enhanced_llm_enabled and hasattr(self.llm_service, 'generate_enhanced_answer'):
            try:
                # Try enhanced generation first
                return await self.llm_service.generate_enhanced_answer(query, ctx, fetched)
            except Exception as e:
                log.warning(f"Enhanced answer generation failed, falling back to base: {e}")
        
        # Fallback to base LLM service
        return await self.llm_service.generate_answer(query, ctx, fetched)

    def _determine_flow_type(
        self, 
        flow_context: Dict[str, Any], 
        intent_layer3: str,
        query: str
    ) -> FlowType:
        """Determine appropriate Flow type"""
        
        # Check explicit flow context
        context_intent = flow_context.get("intent", "none")
        if context_intent == "recommendation":
            return FlowType.RECOMMENDATION
        elif context_intent == "comparison":
            return FlowType.COMPARISON
        elif context_intent == "catalog":
            return FlowType.PRODUCT_CATALOG
        
        # Infer from intent
        if intent_layer3 == "Product_Comparison":
            return FlowType.COMPARISON
        elif intent_layer3 in ["Recommendation", "Product_Discovery"]:
            return FlowType.RECOMMENDATION
        
        # Infer from query
        query_lower = query.lower()
        if any(word in query_lower for word in ["compare", "vs", "versus", "difference"]):
            return FlowType.COMPARISON
        elif any(word in query_lower for word in ["recommend", "suggest", "best", "should i"]):
            return FlowType.RECOMMENDATION
        
        # Default to catalog
        return FlowType.PRODUCT_CATALOG

    def _apply_follow_up_patch(self, patch, ctx: UserContext) -> None:
        """Apply follow-up patch (unchanged from base)"""
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
        """Reset session (unchanged from base)"""
        cleared_items = len(ctx.session) + len(ctx.fetched_data)
        ctx.session.clear()
        ctx.fetched_data.clear()
        
        self.smart_log.context_change(ctx.user_id, "SESSION_RESET", {"cleared_items": cleared_items})

    # ────────────────────────────────────────────────────────
    # Configuration methods
    # ────────────────────────────────────────────────────────
    
    def enable_flows(self, enabled: bool = True) -> None:
        """Enable or disable Flow functionality"""
        self.flow_enabled = enabled
    
    def enable_enhanced_llm(self, enabled: bool = True) -> None:
        """Enable or disable enhanced LLM functionality"""
        self.enhanced_llm_enabled = enabled
    
    def get_flow_stats(self, ctx: UserContext) -> Dict[str, Any]:
        """Get Flow usage statistics for debugging"""
        return {
            "flow_enabled": self.flow_enabled,
            "enhanced_llm_enabled": self.enhanced_llm_enabled,
            "session_has_flow_history": "flow_history" in ctx.session,
            "total_queries": len(ctx.session.get("history", [])),
            "flow_capable_responses": sum(
                1 for h in ctx.session.get("history", []) 
                if h.get("had_flow", False)
            )
        }