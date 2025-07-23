"""
Brain of the WhatsApp shopping bot – with follow‑up classification & delta assessment.

Key additions in this version
────────────────────────────────
1. Follow‑up classifier BEFORE intent classification
2. Delta requirement assessment (no ASK_*; only FETCH_*)
3. Snapshot + history instead of purging context
4. Bug fix: _complete_assessment used a[" "]
5. Removed @lru_cache on async fn
6. Safer JSON parsing & tool parsing helpers
7. Configurable history trimming
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Union

import anthropic

from .config import get_config
from .enums import QueryIntent, ResponseType, BackendFunction, UserSlot
from .intent_config import (
    INTENT_MAPPING,
    SLOT_QUESTIONS,
    SLOT_TO_SESSION_KEY,
    FUNCTION_TTL,
    CATEGORY_QUESTION_HINTS,
)
from .models import (
    BotResponse,
    RequirementAssessment,
    UserContext,
    FollowUpResult,
    FollowUpPatch,
)
from .redis_manager import RedisContextManager
from .data_fetchers import get_fetcher
from .utils.helpers import extract_json_block, iso_now, trim_history, safe_get

Cfg = get_config()
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Tool definitions
# ─────────────────────────────────────────────────────────────

INTENT_CLASSIFICATION_TOOL = {
    "name": "classify_intent",
    "description": "Classify user query into e-commerce intent hierarchy",
    "input_schema": {
        "type": "object",
        "properties": {
            "layer1": {
                "type": "string",
                "enum": ["A", "B", "C", "D", "E"],
                "description": "Top level: A=Awareness, B=Consideration, C=Transaction, D=Post_Purchase, E=Account_Support",
            },
            "layer2": {
                "type": "string",
                "enum": ["A1", "B1", "B2", "C1", "D1", "D2", "E1", "E2"],
                "description": "Second level category",
            },
            "layer3": {
                "type": "string",
                "enum": list(INTENT_MAPPING.keys()),
                "description": "Specific intent - pick the most likely one even if uncertain",
            },
        },
        "required": ["layer1", "layer2", "layer3"],
    },
}

INTENT_CLASSIFICATION_PROMPT = """
You are an e-commerce intent classifier. Analyze the user query and classify it.

### Intent Hierarchy:
A. Awareness_Discovery
   A1. Catalogue → [Product_Discovery, Recommendation]

B. Consideration_Evaluation
   B1. Catalogue   → [Specific_Product_Search, Product_Comparison, Price_Inquiry]
   B2. Logistics   → [Availability_Delivery_Inquiry]

C. Transaction
   C1. Commerce    → [Purchase_Checkout, Order_Modification]

D. Post_Purchase
   D1. Logistics   → [Order_Status, Returns_Refunds]
   D2. Engagement  → [Feedback_Review_Submission, Subscription_Reorder]

E. Account_Support
   E1. Account  → [Account_Profile_Management]
   E2. Support  → [Technical_Support, General_Help]

IMPORTANT: Always pick a specific layer3 intent, even if the query is ambiguous. Choose the most likely one based on context clues. The system will ask follow-up questions if needed.

User Query: "{query}"
"""

# NEW ── Follow‑up classifier tool
FOLLOW_UP_TOOL = {
    "name": "classify_follow_up",
    "description": "Decide if the user query is a follow-up to the last conversation and provide a patch (delta).",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_follow_up": {"type": "boolean"},
            "reason": {"type": "string"},
            "patch": {
                "type": "object",
                "properties": {
                    "slots": {"type": "object"},
                    "intent_override": {"type": "string"},
                    "reset_context": {"type": "boolean"},
                },
                "required": ["slots"],
            },
        },
        "required": ["is_follow_up", "patch"],
    },
}

FOLLOW_UP_PROMPT_TEMPLATE = """
You are determining if the user's new message should be treated as a follow-up.

### Last snapshot (most recent completed interaction):
{last_snapshot}

### Current session slots:
{current_slots}

### New user message:
"{query}"

Decide:
1. Is this a follow-up to the last snapshot? (The user is modifying or refining that request, or asking something directly related.)
2. If yes, list ONLY the changed or newly specified slot values in `patch.slots` (e.g., color:"red").
3. If the intent fundamentally changes, set `intent_override` to the most likely new layer3 label.
4. If the user clearly wants to start fresh ("forget that"), set `reset_context` true.
5. Be concise.
"""

# NEW ── Delta requirement tool (no ASK_*, only FETCH_*)
DELTA_ASSESS_TOOL = {
    "name": "assess_delta_requirements",
    "description": "Given a follow-up patch and full context, list only backend fetches needed to answer the new query.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fetch_functions": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [f.value for f in BackendFunction],
                },
            },
            "rationale": {"type": "string"},
        },
        "required": ["fetch_functions"],
    },
}

DELTA_ASSESS_PROMPT = """
You are determining ONLY which backend functions (FETCH_*) must run after a follow-up patch.
Do NOT include any ASK_* user slots. If nothing is needed, return an empty list.

### Query:
"{query}"

### Patch (changed slots, etc.):
{patch}

### Current Context Keys:
- permanent: {perm_keys}
- session: {sess_keys}
- fetched: {fetched_keys}

### Notes:
- Consider TTLs: if cached data is still fresh, no need to re-fetch.
- Only include fetches whose inputs changed.
"""

# Dataclass for intent result remains internal
@dataclass
class IntentResult:
    layer1: str
    layer2: str
    layer3: str


class ShoppingBotCore:
    def __init__(self, context_mgr: RedisContextManager) -> None:
        self.ctx_mgr = context_mgr
        self.anthropic = anthropic.Anthropic(api_key=Cfg.ANTHROPIC_API_KEY)

    # ────────────────────────────────────────────────────────
    # Public entry point
    # ────────────────────────────────────────────────────────
    async def process_query(self, query: str, ctx: UserContext) -> BotResponse:
        try:
            # If we are mid-assessment, continue as before
            if "assessment" in ctx.session:
                return await self._continue_assessment(query, ctx)

            # Otherwise, detect follow-up first
            fu = await self._classify_follow_up(query, ctx)
            if fu.is_follow_up and not fu.patch.reset_context:
                # Apply patch & resume with delta assessment
                self._apply_follow_up_patch(fu.patch, ctx)
                return await self._handle_follow_up(query, ctx, fu)

            # If reset_context or not follow-up → start fresh
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
    # NEW: Follow-up path
    # ────────────────────────────────────────────────────────
    async def _handle_follow_up(self, query: str, ctx: UserContext, fu: FollowUpResult) -> BotResponse:
        """Run delta assessment (no questions) then fetch + answer."""
        # Decide what (if anything) to fetch again
        fetch_list = await self._assess_delta_requirements(query, ctx, fu.patch)

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

        # Use latest query text for final answer
        answer_dict = await self._llm_generate_answer(query, ctx, fetched)
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))

        # Snapshot + save
        self._snapshot_and_trim(ctx, base_query=query)
        self.ctx_mgr.save_context(ctx)

        return BotResponse(
            resp_type,
            content={"message": answer_dict.get("message", "")},
            functions_executed=list(fetched.keys()),
        )

    # ────────────────────────────────────────────────────────
    # NEW: follow-up classifier
    # ────────────────────────────────────────────────────────
    async def _classify_follow_up(self, query: str, ctx: UserContext) -> FollowUpResult:
        # quick heuristic: if no history, can't be follow-up
        history = ctx.session.get("history", [])
        if not history:
            return FollowUpResult(False, FollowUpPatch(slots={}))

        last_snapshot = history[-1]
        prompt = FOLLOW_UP_PROMPT_TEMPLATE.format(
            last_snapshot=json.dumps(last_snapshot, ensure_ascii=False, indent=2),
            current_slots=json.dumps(ctx.session, ensure_ascii=False, indent=2),
            query=query,
        )
        try:
            resp = self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[FOLLOW_UP_TOOL],
                tool_choice={"type": "tool", "name": "classify_follow_up"},
                temperature=0.1,
                max_tokens=300,
            )
            tool_use = self._pick_tool(resp, "classify_follow_up")
            if not tool_use:
                return FollowUpResult(False, FollowUpPatch(slots={}))
            ipt = tool_use.input
            patch = FollowUpPatch(
                slots=ipt.get("patch", {}).get("slots", {}),
                intent_override=ipt.get("patch", {}).get("intent_override"),
                reset_context=ipt.get("patch", {}).get("reset_context", False),
            )
            return FollowUpResult(bool(ipt.get("is_follow_up", False)), patch, ipt.get("reason", ""))
        except Exception as exc:  # noqa: BLE001
            log.warning("Follow-up classification failed: %s", exc)
            return FollowUpResult(False, FollowUpPatch(slots={}))

    def _apply_follow_up_patch(self, patch: FollowUpPatch, ctx: UserContext) -> None:
        # Update slots in session
        for k, v in patch.slots.items():
            ctx.session[k] = v
        # Optionally override intent info in last snapshot or top-level
        if patch.intent_override:
            ctx.session["intent_override"] = patch.intent_override

    def _reset_session_only(self, ctx: UserContext) -> None:
        ctx.session.clear()
        ctx.fetched_data.clear()

    # ────────────────────────────────────────────────────────
    # NEW: delta assessment
    # ────────────────────────────────────────────────────────
    async def _assess_delta_requirements(self, query: str, ctx: UserContext, patch: FollowUpPatch) -> List[BackendFunction]:
        prompt = DELTA_ASSESS_PROMPT.format(
            query=query,
            patch=json.dumps(patch.__dict__, ensure_ascii=False),
            perm_keys=list(ctx.permanent.keys()),
            sess_keys=list(ctx.session.keys()),
            fetched_keys=list(ctx.fetched_data.keys()),
        )
        try:
            resp = self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[DELTA_ASSESS_TOOL],
                tool_choice={"type": "tool", "name": "assess_delta_requirements"},
                temperature=0.1,
                max_tokens=300,
            )
            tool_use = self._pick_tool(resp, "assess_delta_requirements")
            if not tool_use:
                return []
            items = tool_use.input.get("fetch_functions", [])
            out: List[BackendFunction] = []
            for it in items:
                try:
                    out.append(BackendFunction(it))
                except ValueError:
                    pass
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("Delta assess failed: %s", exc)
            return []

    # ────────────────────────────────────────────────────────
    # START NEW ASSESSMENT (unchanged mostly)
    # ────────────────────────────────────────────────────────
    async def _start_new_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        result = await self._classify_intent(query)
        intent = self._map_leaf_to_query_intent(result.layer3)

        ctx.session.update(
            intent_l1=result.layer1,
            intent_l2=result.layer2,
            intent_l3=result.layer3,
        )

        assessment = await self._assess_requirements(query, intent, result.layer3, ctx)

        user_slots = [f for f in assessment.missing_data if self._is_user_slot(f)]
        if user_slots:
            contextual_questions = await self._generate_contextual_questions(
                user_slots, query, result.layer3, ctx
            )
            ctx.session["contextual_questions"] = contextual_questions

        ctx.session["assessment"] = {
            "original_query": query,
            "intent": intent.value,
            "missing_data": [self._get_func_value(f) for f in assessment.missing_data],
            "priority_order": [self._get_func_value(f) for f in assessment.priority_order],
            "fulfilled": [],
            "currently_asking": None,
        }

        self.ctx_mgr.save_context(ctx)
        return await self._continue_assessment(query, ctx)

    # ────────────────────────────────────────────────────────
    # CONTINUE ASSESSMENT (mostly same)
    # ────────────────────────────────────────────────────────
    async def _continue_assessment(self, query: str, ctx: UserContext) -> BotResponse:
        a = ctx.session["assessment"]

        # If user answered a question
        if query != a["original_query"]:
            self._store_user_answer(query, a, ctx)

        still_missing = self._compute_still_missing(a, ctx)
        ask_first = [f for f in still_missing if self._is_user_slot(f)]
        fetch_later = [f for f in still_missing if not self._is_user_slot(f)]

        if ask_first:
            func = ask_first[0]
            func_value = self._get_func_value(func)
            a["currently_asking"] = func_value
            self.ctx_mgr.save_context(ctx)
            return BotResponse(ResponseType.QUESTION, self._build_question(func, ctx))

        # Complete
        return await self._complete_assessment(a, ctx, fetch_later)

    # ────────────────────────────────────────────────────────
    # COMPLETE (bug fix + snapshot)
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
        answer_dict = await self._llm_generate_answer(original_q, ctx, fetched)
        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        message = answer_dict.get(
            "message",
            "I can help you with shopping queries. Please provide more details.",
        )

        # Snapshot history (don't wipe session slots)
        self._snapshot_and_trim(ctx, base_query=original_q)

        # Clear assessment but keep slots + fetched_data
        ctx.session.pop("assessment", None)
        ctx.session.pop("contextual_questions", None)
        self.ctx_mgr.save_context(ctx)

        return BotResponse(resp_type, content={"message": message}, functions_executed=list(fetched.keys()))

    # ─────────────────────────────────────────────────────────
    # LLM HELPERS
    # ─────────────────────────────────────────────────────────

    #Generate contextual questions
    async def _generate_contextual_questions(
        self,
        slots_needed: List[UserSlot],
        query: str,
        intent_l3: str,
        ctx: UserContext,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Generate contextual questions for all needed slots at once using Claude tool-calling.
        Falls back to {} if tool call fails; _build_question() will use static fallbacks.
        """
        # Gather per-slot generation hints
        slot_hints = {}
        for slot in slots_needed:
            config = SLOT_QUESTIONS.get(slot, {})
            slot_hints[slot.value] = config.get("generation_hints", {})

        # Crude category guess (keep as-is or improve later)
        product_category = ctx.session.get("product_category", "general")
        ql = query.lower()
        if any(w in ql for w in ["phone", "mobile", "laptop", "camera"]):
            product_category = "electronics"
        elif any(w in ql for w in ["soap", "shampoo", "detergent", "toothpaste"]):
            product_category = "fmcg"
        elif any(w in ql for w in ["shirt", "dress", "shoes", "jeans"]):
            product_category = "fashion"

        category_hints = CATEGORY_QUESTION_HINTS.get(product_category, {})

        questions_tool = {
            "name": "generate_questions",
            "description": "Generate contextual questions for user slots",
            "input_schema": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "object",
                        "properties": {
                            slot.value: {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                    "type": {"type": "string"},
                                    "options": {"type": "array", "items": {"type": "string"}},
                                    "placeholder": {"type": "string"},
                                    "hints": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["message", "type"],
                            }
                            for slot in slots_needed
                        },
                    }
                },
                "required": ["questions"],
            },
        }

        prompt = f"""
Generate contextual questions for a shopping query.

Original Query: "{query}"
Intent: {intent_l3}
Product Category: {product_category}

Question Generation Hints:
{json.dumps(slot_hints, indent=2)}

Category-Specific Hints:
{json.dumps(category_hints, indent=2)}

Generate natural, contextual questions for these information needs:
{[slot.value for slot in slots_needed]}

Guidelines:
- Make questions specific to the query context
- Include relevant options where appropriate
- For budget questions, use category-appropriate ranges
- For preferences, include category-relevant options
- Questions should feel conversational and helpful
"""

        try:
            resp = self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[questions_tool],
                tool_choice={"type": "tool", "name": "generate_questions"},
                temperature=0.3,
                max_tokens=800,
            )
            tool_use = self._pick_tool(resp, "generate_questions")
            if tool_use:
                return tool_use.input.get("questions", {})
        except Exception as exc:  # noqa: BLE001
            log.warning("Contextual question generation failed: %s", exc)

        return {}

    async def _classify_intent(self, query: str) -> IntentResult:
        prompt = INTENT_CLASSIFICATION_PROMPT.format(query=query.strip())
        resp = self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[INTENT_CLASSIFICATION_TOOL],
            tool_choice={"type": "tool", "name": "classify_intent"},
            temperature=0.2,
            max_tokens=100,
        )
        tool_use = self._pick_tool(resp, "classify_intent")
        if not tool_use:
            raise ValueError("No classify_intent tool use found in response")
        args = tool_use.input
        return IntentResult(args["layer1"], args["layer2"], args["layer3"])

    def _map_leaf_to_query_intent(self, leaf: str) -> QueryIntent:
        return INTENT_MAPPING.get(leaf, {}).get("query_intent", QueryIntent.GENERAL_HELP)

    async def _assess_requirements(
        self, query: str, intent: QueryIntent, layer3: str, ctx: UserContext
    ) -> RequirementAssessment:
        assessment_tool = self._build_assessment_tool()
        intent_config = INTENT_MAPPING.get(layer3, {})
        suggested_slots = [s.value for s in intent_config.get("suggested_slots", [])]
        suggested_functions = [f.value for f in intent_config.get("suggested_functions", [])]

        prompt = f"""
You are analyzing an e-commerce query to determine what information is needed.

Query: "{query}"
Intent Category: {intent.value}
Specific Intent: {layer3}

Current Context:
- User permanent data: {list(ctx.permanent.keys())}
- Session data: {list(ctx.session.keys())}
- Already fetched: {list(ctx.fetched_data.keys())}

Typical requirements for {layer3}:
- Slots: {suggested_slots}
- Functions: {suggested_functions}

Analyze the query and context to determine:
1. What user information (ASK_*) do we still need?
2. What backend data (FETCH_*) should we retrieve?
3. In what order should we collect this information?

Consider the specific query details - not all typical requirements may be needed, and some atypical ones might be required based on the query specifics.
"""

        resp = self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[assessment_tool],
            tool_choice={"type": "tool", "name": "assess_requirements"},
            temperature=0.1,
            max_tokens=500,
        )
        tool_use = self._pick_tool(resp, "assess_requirements")
        if not tool_use:
            raise ValueError("No assess_requirements tool use found")
        args = tool_use.input

        missing: List[Union[BackendFunction, UserSlot]] = []
        for item in args["missing_data"]:
            func = self._string_to_function(item["function"])
            if func:
                missing.append(func)

        order: List[Union[BackendFunction, UserSlot]] = []
        for f in args["priority_order"]:
            func = self._string_to_function(f)
            if func:
                order.append(func)

        rationale = {item["function"]: item["rationale"] for item in args["missing_data"]}

        return RequirementAssessment(
            intent=intent,
            missing_data=missing,
            rationale=rationale,
            priority_order=order or missing,
        )

    def _build_assessment_tool(self) -> Dict[str, Any]:
        all_slots = [slot.value for slot in UserSlot]
        all_functions = [func.value for func in BackendFunction]
        all_available = all_slots + all_functions
        return {
            "name": "assess_requirements",
            "description": "Determine what information is needed to fulfill the user's query",
            "input_schema": {
                "type": "object",
                "properties": {
                    "missing_data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "function": {
                                    "type": "string",
                                    "enum": all_available,
                                },
                                "rationale": {"type": "string"},
                            },
                            "required": ["function", "rationale"],
                        },
                    },
                    "priority_order": {
                        "type": "array",
                        "items": {"type": "string", "enum": all_available},
                    },
                },
                "required": ["missing_data", "priority_order"],
            },
        }

    async def _llm_generate_answer(self, query: str, ctx: UserContext, fetched: Dict[str, Any]) -> Dict[str, str]:
        prompt = (
            "You are an e-commerce assistant.\n\n"
            "### USER QUERY\n"
            f"{query}\n\n"
            "### USER PROFILE\n"
            f"{ctx.permanent}\n\n"
            "### SESSION ANSWERS\n"
            f"{ctx.session}\n\n"
            "### FETCHED DATA\n"
            f"{fetched}\n\n"
            "### Instructions\n"
            "Reply with a single JSON object **without code fences**, exactly:\n"
            "{\n"
            '  "response_type": "question" | "final_answer",\n'
            '  "message": "string"\n'
            "}\n"
        )
        resp = self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=Cfg.LLM_MAX_TOKENS,
        )
        # Claude may return text parts; extract first JSON
        data = extract_json_block(resp.content[0].text)
        if "response_type" in data and "message" in data:
            return data
        return {"response_type": "final_answer", "message": resp.content[0].text.strip()}

    # ─────────────────────────────────────────────────────────
    # Helper utilities
    # ─────────────────────────────────────────────────────────
    def _already_have_data(self, func_str: str, ctx: UserContext) -> bool:
        # User slot?
        try:
            slot = UserSlot(func_str)
            session_key = SLOT_TO_SESSION_KEY.get(slot, slot.name.lower())
            if slot == UserSlot.DELIVERY_ADDRESS:
                return session_key in ctx.session or session_key in ctx.permanent
            return session_key in ctx.session
        except ValueError:
            pass

        # Backend function?
        try:
            func = BackendFunction(func_str)
            rec = ctx.fetched_data.get(func.value)
            if not rec:
                return False
            ts = datetime.fromisoformat(rec["timestamp"])
            ttl = FUNCTION_TTL.get(func, timedelta(minutes=5))
            return datetime.now() - ts < ttl
        except ValueError:
            pass
        return False

    def _build_question(self, func: Union[UserSlot, str], ctx: UserContext) -> Dict[str, Any]:
        func_value = self._get_func_value(func)
        contextual_q = ctx.session.get("contextual_questions", {}).get(func_value)
        if contextual_q:
            return contextual_q
        if isinstance(func, UserSlot):
            config = SLOT_QUESTIONS.get(func, {})
            if "fallback" in config:
                return config["fallback"].copy()
        try:
            slot = UserSlot(func_value)
            config = SLOT_QUESTIONS.get(slot, {})
            if "fallback" in config:
                return config["fallback"].copy()
        except ValueError:
            pass
        if func_value.startswith("ASK_"):
            slot_name = func_value[4:].lower().replace("_", " ")
            return {"message": f"Could you tell me your {slot_name}?", "type": "text_input"}
        return {"message": "Could you provide more details?", "type": "text_input"}

    def _string_to_function(self, f_str: str) -> Union[BackendFunction, UserSlot, None]:
        try:
            return UserSlot(f_str)
        except ValueError:
            try:
                return BackendFunction(f_str)
            except ValueError:
                return None

    def _is_user_slot(self, func: Union[BackendFunction, UserSlot, str]) -> bool:
        return isinstance(func, UserSlot) or (isinstance(func, str) and func.startswith("ASK_"))

    def _get_func_value(self, func: Union[BackendFunction, UserSlot, str]) -> str:
        if isinstance(func, (BackendFunction, UserSlot)):
            return func.value
        return str(func)

    def _compute_still_missing(self, a: Dict[str, Any], ctx: UserContext) -> List[Union[BackendFunction, UserSlot]]:
        out: List[Union[BackendFunction, UserSlot]] = []
        for f_str in a["priority_order"]:
            if f_str in a["fulfilled"]:
                continue
            if self._already_have_data(f_str, ctx):
                a["fulfilled"].append(f_str)
                continue
            func = self._string_to_function(f_str)
            if func:
                out.append(func)
        return out

    def _store_user_answer(self, text: str, a: Dict[str, Any], ctx: UserContext) -> None:
        target = a.get("currently_asking")
        if not target:
            return
        try:
            slot = UserSlot(target)
            session_key = SLOT_TO_SESSION_KEY.get(slot, slot.name.lower())
        except ValueError:
            session_key = target[4:].lower() if target.startswith("ASK_") else target
        ctx.session[session_key] = text
        if target == UserSlot.DELIVERY_ADDRESS.value:
            ctx.permanent["delivery_address"] = text
        a["fulfilled"].append(target)
        a["currently_asking"] = None

    def _snapshot_and_trim(self, ctx: UserContext, *, base_query: str) -> None:
        snapshot = {
            "query": base_query,
            "intent": ctx.session.get("intent_l3") or ctx.session.get("intent_override"),
            "slots": {k: ctx.session.get(k) for k in SLOT_TO_SESSION_KEY.values() if k in ctx.session},
            "fetched": {k: v["timestamp"] for k, v in ctx.fetched_data.items()},
            "finished_at": iso_now(),
        }
        history = ctx.session.setdefault("history", [])
        history.append(snapshot)
        trim_history(history, Cfg.HISTORY_MAX_SNAPSHOTS)

    # Generic tool picker helper
    def _pick_tool(self, resp: Any, name: str):
        for c in resp.content:
            if getattr(c, "type", None) == "tool_use" and getattr(c, "name", None) == name:
                return c
        return None