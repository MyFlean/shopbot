# shopping_bot/bot_core.py
"""
Brain of the application — now expects the LLM to return:
{
  "response_type": "question" | "final_answer",
  "message": "..."
}
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

import anthropic

from .config import get_config
from .enums import QueryIntent, ResponseType, ShoppingFunction
from .models import BotResponse, RequirementAssessment, UserContext
from .redis_manager import RedisContextManager
from .data_fetchers import get_fetcher
from .utils import extract_json_block

Cfg = get_config()
log = logging.getLogger(__name__)


class ShoppingBotCore:
    # ─────────────────────────────────────────────────────────────
    # Construction
    # ─────────────────────────────────────────────────────────────
    def __init__(self, context_mgr: RedisContextManager) -> None:
        self.ctx_mgr = context_mgr
        self.anthropic = (
            anthropic.Anthropic(api_key=Cfg.ANTHROPIC_API_KEY)
            if Cfg.ANTHROPIC_API_KEY
            else None
        )

    # ─────────────────────────────────────────────────────────────
    # Public entry-point
    # ─────────────────────────────────────────────────────────────
    async def process_query(self, query: str, ctx: UserContext) -> BotResponse:
        try:
            if "assessment" not in ctx.session:
                return await self._start_new_assessment(query, ctx)
            return await self._continue_assessment(query, ctx)
        except Exception as exc:  # noqa: BLE001
            log.exception("process_query failed")
            return BotResponse(
                ResponseType.ERROR,
                {"message": "Sorry, something went wrong.", "error": str(exc)},
            )

    # ─────────────────────────────────────────────────────────────
    # NEW ASSESSMENT
    # ─────────────────────────────────────────────────────────────
    async def _start_new_assessment(
        self, query: str, ctx: UserContext
    ) -> BotResponse:
        intent = await self._detect_intent(query)
        assessment = await self._assess_requirements(query, intent, ctx)

        ctx.session["assessment"] = {
            "original_query": query,
            "intent": intent.value,
            "missing_data": [f.value for f in assessment.missing_data],
            "priority_order": [f.value for f in assessment.priority_order],
            "fulfilled": [],
            "currently_asking": None,
        }
        self.ctx_mgr.save_context(ctx)
        return await self._continue_assessment(query, ctx)

    # ─────────────────────────────────────────────────────────────
    # CONTINUE ASSESSMENT
    # ─────────────────────────────────────────────────────────────
    async def _continue_assessment(
        self, query: str, ctx: UserContext
    ) -> BotResponse:  # noqa: C901
        a = ctx.session["assessment"]

        # If user just answered a question, store it
        if query != a["original_query"]:
            self._store_user_answer(query, a, ctx)

        still_missing = self._compute_still_missing(a, ctx)

        ask_first = [
            f
            for f in still_missing
            if f
            in {
                ShoppingFunction.ASK_USER_BUDGET,
                ShoppingFunction.ASK_USER_PREFERENCES,
                ShoppingFunction.ASK_DELIVERY_ADDRESS,
            }
        ]
        fetch_later = [f for f in still_missing if f not in ask_first]

        if ask_first:
            func = ask_first[0]
            a["currently_asking"] = func.value
            self.ctx_mgr.save_context(ctx)
            return BotResponse(
                ResponseType.QUESTION, self._build_question(func)
            )

        return await self._complete_assessment(a, ctx, fetch_later)

    # helper: store reply
    def _store_user_answer(self, text: str, a: Dict[str, Any], ctx: UserContext) -> None:
        target = a.get("currently_asking")
        if target == ShoppingFunction.ASK_USER_BUDGET.value:
            ctx.session["budget"] = text
        elif target == ShoppingFunction.ASK_USER_PREFERENCES.value:
            ctx.session["preferences"] = text
        elif target == ShoppingFunction.ASK_DELIVERY_ADDRESS.value:
            ctx.session["delivery_address"] = text
            ctx.permanent["delivery_address"] = text
        if target:
            a["fulfilled"].append(target)
        a["currently_asking"] = None

    # helper: what’s still missing?
    def _compute_still_missing(
        self, a: Dict[str, Any], ctx: UserContext
    ) -> List[ShoppingFunction]:
        out: List[ShoppingFunction] = []
        for f_str in a["priority_order"]:
            f = ShoppingFunction(f_str)
            if f_str in a["fulfilled"]:
                continue
            if self._already_have(f, ctx):
                a["fulfilled"].append(f_str)
                continue
            out.append(f)
        return out

    # ─────────────────────────────────────────────────────────────
    # COMPLETE — run fetchers & ask LLM for final answer
    # ─────────────────────────────────────────────────────────────
    async def _complete_assessment(
        self,
        a: Dict[str, Any],
        ctx: UserContext,
        fetchers: List[ShoppingFunction],
    ) -> BotResponse:
        fetched: Dict[str, Any] = {}

        for func in fetchers:
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

        answer_dict = await self._llm_generate_answer(
            a["original_query"], ctx, fetched
        )

        resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
        message = answer_dict.get(
            "message",
            "I can help you with shopping queries. Please provide more details.",
        )

        ctx.session.pop("assessment", None)
        self.ctx_mgr.save_context(ctx)

        return BotResponse(
            resp_type,
            content={"message": message},
            functions_executed=list(fetched.keys()),
        )

    # ─────────────────────────────────────────────────────────────
    # LLM HELPERS
    # ─────────────────────────────────────────────────────────────
    async def _detect_intent(self, query: str) -> QueryIntent:
        prompt = (
            "Return only the intent name from: "
            "PRODUCT_SEARCH, RECOMMENDATION, PRICE_INQUIRY, PURCHASE, "
            "ORDER_STATUS, PRODUCT_COMPARISON, GENERAL_HELP.\n\n"
            f"Query: {query}"
        )
        if not self.anthropic:
            return self._fallback_intent_detection(query)

        try:
            resp = self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=20,
            )
            return QueryIntent[resp.content[0].text.strip().upper()]
        except Exception:
            return self._fallback_intent_detection(query)

    async def _assess_requirements(
        self, query: str, intent: QueryIntent, ctx: UserContext
    ) -> RequirementAssessment:
        prompt = (
            "Given the query & available data, which helper functions are still needed?\n\n"
            f"Query: {query}\nIntent: {intent.value}\n\n"
            f"permanent keys: {list(ctx.permanent.keys())}\n"
            f"session keys:   {list(ctx.session.keys())}\n"
            f"fetched keys:   {list(ctx.fetched_data.keys())}\n\n"
            "Return JSON exactly like:\n"
            '{"missing_data":["function1"],'
            '"rationale":{"function1":"because"},'
            '"priority_order":["function1"]}'
        )

        if not self.anthropic:
            return self._fallback_assessment(intent, ctx)

        try:
            resp = self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=300,
            )
            data = extract_json_block(resp.content[0].text)
            missing = [
                ShoppingFunction(f)
                for f in data.get("missing_data", [])
                if f in ShoppingFunction._value2member_map_
            ]
            order = [
                ShoppingFunction(f)
                for f in data.get("priority_order", [])
                if f in ShoppingFunction._value2member_map_
            ] or missing
            return RequirementAssessment(
                intent=intent,
                missing_data=missing,
                rationale=data.get("rationale", {}),
                priority_order=order,
            )
        except Exception:
            return self._fallback_assessment(intent, ctx)

    async def _llm_generate_answer(
        self, query: str, ctx: UserContext, fetched: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Claude must output ONLY:
        {
          "response_type": "question" | "final_answer",
          "message": "markdown ..."
        }
        """
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
            '{\n'
            '  "response_type": "question" | "final_answer",\n'
            '  "message": "string"\n'
            '}\n'
        )

        if not self.anthropic:
            return {"response_type": "final_answer", "message": "Demo mode: no LLM."}

        resp = self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=Cfg.LLM_MAX_TOKENS,
        )

        data = extract_json_block(resp.content[0].text)
        if "response_type" in data and "message" in data:
            return data
        return {"response_type": "final_answer", "message": resp.content[0].text.strip()}

    # ─────────────────────────────────────────────────────────────
    # FALLBACK / helper logic (unchanged)
    # ─────────────────────────────────────────────────────────────
    def _already_have(self, func: ShoppingFunction, ctx: UserContext) -> bool:
        if func == ShoppingFunction.ASK_USER_BUDGET:
            return "budget" in ctx.session
        if func == ShoppingFunction.ASK_USER_PREFERENCES:
            return "preferences" in ctx.session
        if func == ShoppingFunction.ASK_DELIVERY_ADDRESS:
            return "delivery_address" in ctx.session or "delivery_address" in ctx.permanent
        rec = ctx.fetched_data.get(func.value)
        if not rec:
            return False
        ts = datetime.fromisoformat(rec["timestamp"])
        return datetime.now() - ts < timedelta(minutes=5)

    def _build_question(self, func: ShoppingFunction) -> Dict[str, Any]:
        if func == ShoppingFunction.ASK_USER_BUDGET:
            return {
                "message": "What's your budget range?",
                "type": "budget_input",
                "examples": ["$500-$1000", "$1000-$1500"],
            }
        if func == ShoppingFunction.ASK_USER_PREFERENCES:
            return {
                "message": "What features matter most to you?",
                "type": "preferences_input",
            }
        if func == ShoppingFunction.ASK_DELIVERY_ADDRESS:
            return {"message": "What's your delivery address?", "type": "address_input"}
        return {"message": "Could you provide more details?"}

    # simple heuristics…
    def _fallback_intent_detection(self, query: str) -> QueryIntent:
        q = query.lower()
        if any(w in q for w in ("buy", "order")):
            return QueryIntent.PURCHASE
        if "price" in q:
            return QueryIntent.PRICE_INQUIRY
        if any(w in q for w in ("recommend", "suggest")):
            return QueryIntent.RECOMMENDATION
        return QueryIntent.GENERAL_HELP

    def _fallback_assessment(self, intent: QueryIntent, ctx: UserContext) -> RequirementAssessment:
        rules = {
            QueryIntent.RECOMMENDATION: [
                ShoppingFunction.ASK_USER_BUDGET,
                ShoppingFunction.ASK_USER_PREFERENCES,
                ShoppingFunction.FETCH_PRODUCT_INVENTORY,
            ],
            QueryIntent.PURCHASE: [
                ShoppingFunction.ASK_DELIVERY_ADDRESS,
                ShoppingFunction.FETCH_PRODUCT_DETAILS,
            ],
        }
        needed = rules.get(intent, [])
        missing = [f for f in needed if not self._already_have(f, ctx)]
        return RequirementAssessment(
            intent=intent,
            missing_data=missing,
            rationale={f.value: "fallback" for f in missing},
            priority_order=missing,
        )
