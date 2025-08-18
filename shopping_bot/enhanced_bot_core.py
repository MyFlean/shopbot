"""
Enhanced ShoppingBotCore with Flow support.

Policy (final):
- Flows are ONLY used for layer3 intent == "Recommendation".
- Background deferral (PROCESSING_STUB → FE button after webhook) happens ONLY for Recommendation.
- All non-Flow intents always return sync text (QUESTION or FINAL_ANSWER).
- Follow-ups that land on Recommendation also defer (return PROCESSING_STUB), never send sync text.

This guarantees: no dual text+flow, and a clean split between Flow vs non-Flow behaviors.
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
        self.flow_enabled = True
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
        """
        Enhanced process_query that can return either BotResponse or EnhancedBotResponse.
        If flows are disabled, delegates entirely to the base core.
        """
        self.smart_log.query_start(ctx.user_id, query, bool(ctx.session))

        if not self.flow_enabled or not enable_flows:
            return await self.base_core.process_query(query, ctx)

        try:
            # Continue assessment if already in progress
            if "assessment" in ctx.session:
                self.smart_log.flow_decision(ctx.user_id, "CONTINUE_ASSESSMENT")
                return await self._continue_assessment_enhanced(query, ctx)

            # Follow-up handling (use same logic as base)
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

        except Exception as exc:  # noqa: BLE001
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

    async def _handle_follow_up_enhanced(
        self,
        query: str,
        ctx: UserContext,
        fu,
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """Enhanced follow-up handling with Flow policy."""
        # Determine effective layer3 after patch
        effective_l3 = fu.patch.intent_override or ctx.session.get("intent_l3", "") or ""

        # If Recommendation, we always defer to background (no sync text).
        if effective_l3 == "Recommendation":
            ctx.session["needs_background"] = True
            a = ctx.session.get("assessment")
            if a and a.get("original_query"):
                a["phase"] = "processing"
            self.ctx_mgr.save_context(ctx)
            self.smart_log.flow_decision(
                ctx.user_id, "DEFER_TO_BACKGROUND_FOLLOW_UP", {"intent_l3": effective_l3}
            )
            return BotResponse(
                ResponseType.PROCESSING_STUB,
                content={"message": "Processing your request…"},
            )

        # Non-Flow intents: do a normal follow-up (synchronous)
        fetch_list = await self.llm_service.assess_delta_requirements(query, ctx, fu.patch)
        if fetch_list:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetch_list])

        fetched = await self._execute_fetchers(fetch_list, ctx)
        answer_dict = await self._generate_enhanced_answer(query, ctx, fetched)

        enhanced_response = await self._create_enhanced_response(
            answer_dict, list(fetched.keys()), query, ctx
        )

        snapshot_and_trim(ctx, base_query=query)
        self.ctx_mgr.save_context(ctx)

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
        """Enhanced new assessment with Flow support."""

        # Classify intent
        result = await self.llm_service.classify_intent(query)
        intent = map_leaf_to_query_intent(result.layer3)

        self.smart_log.intent_classified(
            ctx.user_id, (result.layer1, result.layer2, result.layer3), intent.value
        )

        # Update session with intent taxonomy and early background decision
        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )
        needs_bg = self._needs_background(intent)  # only True for Recommendation
        ctx.session["needs_background"] = needs_bg
        self.smart_log.flow_decision(
            ctx.user_id,
            "BACKGROUND_DECISION",
            {"needs_background": needs_bg, "intent": intent.value, "intent_l3": result.layer3},
        )

        # Assess requirements (slots + backend plan)
        assessment = await self.llm_service.assess_requirements(
            query, intent, result.layer3, ctx
        )

        user_slots = [f for f in assessment.missing_data if is_user_slot(f)]
        missing_data_names = [get_func_value(f) for f in assessment.missing_data]
        ask_first_names = [get_func_value(f) for f in user_slots]

        self.smart_log.requirements_assessed(
            ctx.user_id, missing_data_names, ask_first_names
        )

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
        ctx: UserContext,
    ) -> Union[BotResponse, EnhancedBotResponse]:
        """Enhanced assessment continuation."""

        a = ctx.session["assessment"]

        # Store user's answer if this isn't the original query
        if query != a["original_query"]:
            store_user_answer(query, a, ctx)
            self.smart_log.context_change(
                ctx.user_id,
                "USER_ANSWER_STORED",
                {"for": a.get("currently_asking"), "answer_len": len(query)},
            )

        # Compute what's still missing
        still_missing = compute_still_missing(a, ctx)
        ask_first = [f for f in still_missing if is_user_slot(f)]
        fetch_later = [f for f in still_missing if not is_user_slot(f)]

        # If we need to ask the user something, return QUESTION
        if ask_first:
            func = ask_first[0]
            func_value = get_func_value(func)
            a["currently_asking"] = func_value

            self.smart_log.user_question(ctx.user_id, func_value)
            self.ctx_mgr.save_context(ctx)

            return BotResponse(ResponseType.QUESTION, build_question(func, ctx))

        # Ask-loop is done. If background is needed (Recommendation only) and backend work remains,
        # return PROCESSING_STUB (do NOT run fetchers here).
        needs_bg = bool(ctx.session.get("needs_background"))
        if fetch_later and needs_bg:
            a["phase"] = "processing"
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

        # Otherwise complete synchronously with enhanced answer (non-Flow path)
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
        """Enhanced assessment completion with Flow support (synchronous path for non-Flow)."""

        backend_fetchers = [f for f in fetchers if isinstance(f, BackendFunction)]

        # Execute backend fetchers
        fetched = await self._execute_fetchers(backend_fetchers, ctx)

        # Generate enhanced answer
        original_q = a.get("original_query", "")
        answer_dict = await self._generate_enhanced_answer(original_q, ctx, fetched)

        # Create enhanced response (Flow only if Recommendation; otherwise None)
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
            enhanced_response.requires_flow,
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
        ctx: UserContext,
    ) -> EnhancedBotResponse:
        """Create enhanced response (Flow only for Recommendation)."""

        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        message = answer_dict.get("message", "")
        sections = answer_dict.get("sections", {})

        base_content = {
            "message": message,
            "sections": sections,
        }

        flow_payload: Optional[FlowPayload] = None
        requires_flow = False

        # Flow is gated strictly to Recommendation
        if self.flow_enabled and resp_type == ResponseType.FINAL_ANSWER:
            flow_payload = await self._create_flow_payload(answer_dict, query, ctx)
            requires_flow = flow_payload is not None

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
        """
        Create Flow payload ONLY for Recommendation.
        Returns None for any other intent.
        """
        intent_l3 = ctx.session.get("intent_l3", "") or ""
        if intent_l3 != "Recommendation":
            return None  # hard gate: flows are only for recommendations

        structured_products = answer_dict.get("structured_products", [])
        flow_context = answer_dict.get("flow_context", {})

        # If the LLM already gave structured products, use them
        if structured_products:
            products: List[ProductData] = []
            for product_dict in structured_products:
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
                except Exception as e:  # noqa: BLE001
                    log.warning(f"Failed to create ProductData: {e}")
                    continue

            if not products:
                return None

            # For Recommendation we default to a recommendation flow
            header_text = flow_context.get("header_text", "Recommended for you")
            reason = flow_context.get("reason", "")
            return self.flow_generator.generate_recommendation_flow(
                products, reason, header_text
            )

        # Otherwise, attempt to derive a Flow from the “sections”
        # (still gated to Recommendation)
        sections = answer_dict.get("sections", {})
        flow_payload = self.flow_generator.create_flow_from_sections(sections)
        if flow_payload and self.flow_generator.validate_flow_payload(flow_payload):
            return flow_payload
        return None

    # ────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────

    async def _execute_fetchers(
        self,
        fetchers: List[BackendFunction],
        ctx: UserContext,
    ) -> Dict[str, Any]:
        """Execute backend fetchers and return results."""

        if fetchers:
            self.smart_log.data_operations(ctx.user_id, [f.value for f in fetchers])

        fetched: Dict[str, Any] = {}
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
                    ctx.user_id, func.value, data_size=len(str(result)) if result else 0
                )

            except Exception as exc:  # noqa: BLE001
                self.smart_log.warning(
                    ctx.user_id, "DATA_FETCH_FAILED", f"{func.value}: {exc}"
                )
                fetched[func.value] = {"error": str(exc)}

        if fetchers:
            self.smart_log.data_operations(
                ctx.user_id, [f.value for f in fetchers], success_count
            )

        return fetched

    async def _generate_enhanced_answer(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate enhanced answer with structured data (fallback to base if needed)."""

        if self.enhanced_llm_enabled and hasattr(
            self.llm_service, "generate_enhanced_answer"
        ):
            try:
                return await self.llm_service.generate_enhanced_answer(
                    query, ctx, fetched
                )
            except Exception as e:  # noqa: BLE001
                log.warning(f"Enhanced answer generation failed, falling back: {e}")

        return await self.llm_service.generate_answer(query, ctx, fetched)

    def _apply_follow_up_patch(self, patch, ctx: UserContext) -> None:
        """Apply follow-up patch (same semantics as base)."""
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
        """Reset session (same semantics as base)."""
        cleared_items = len(ctx.session) + len(ctx.fetched_data)
        ctx.session.clear()
        ctx.fetched_data.clear()
        self.smart_log.context_change(
            ctx.user_id, "SESSION_RESET", {"cleared_items": cleared_items}
        )

    # ────────────────────────────────────────────────────────
    # Feature toggles
    # ────────────────────────────────────────────────────────

    def enable_flows(self, enabled: bool = True) -> None:
        self.flow_enabled = enabled

    def enable_enhanced_llm(self, enabled: bool = True) -> None:
        self.enhanced_llm_enabled = enabled

    def get_flow_stats(self, ctx: UserContext) -> Dict[str, Any]:
        """Basic flow stats for debugging."""
        return {
            "flow_enabled": self.flow_enabled,
            "enhanced_llm_enabled": self.enhanced_llm_enabled,
            "session_has_flow_history": "flow_history" in ctx.session,
            "total_queries": len(ctx.session.get("history", [])),
            "flow_capable_responses": sum(
                1 for h in ctx.session.get("history", []) if h.get("had_flow", False)
            ),
        }

    # ────────────────────────────────────────────────────────
    # Core-owned background policy
    # ────────────────────────────────────────────────────────

    def _needs_background(self, intent) -> bool:
        """
        ONLY Recommendation defers to background (for Flow).
        All other intents are synchronous text.
        """
        try:
            return str(intent).lower() in {"queryintent.recommendation"}
        except Exception:
            return False
