"""
LLM service module for ShoppingBotCore.

This module encapsulates all LLM interactions including:
- Tool definitions
- Prompt templates
- LLM API calls
- Response parsing

This keeps the core bot logic clean and focused on orchestration.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Union

import anthropic

from .config import get_config
from .enums import QueryIntent, BackendFunction, UserSlot
from .intent_config import (
    INTENT_MAPPING,
    SLOT_QUESTIONS,
    CATEGORY_QUESTION_HINTS,
)
from .models import (
    RequirementAssessment,
    UserContext,
    FollowUpResult,
    FollowUpPatch,
)
from .utils.helpers import extract_json_block
from .bot_helpers import pick_tool, string_to_function

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

def build_assessment_tool() -> Dict[str, Any]:
    """Build the requirements assessment tool dynamically."""
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

def build_questions_tool(slots_needed: List[UserSlot]) -> Dict[str, Any]:
    """Build the contextual questions generation tool dynamically."""
    return {
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

# ─────────────────────────────────────────────────────────────
# Prompt templates
# ─────────────────────────────────────────────────────────────

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

REQUIREMENTS_ASSESSMENT_PROMPT = """
You are analyzing an e-commerce query to determine what information is needed.

Query: "{query}"
Intent Category: {intent}
Specific Intent: {layer3}

Current Context:
- User permanent data: {perm_keys}
- Session data: {sess_keys}
- Already fetched: {fetched_keys}

Typical requirements for {layer3}:
- Slots: {suggested_slots}
- Functions: {suggested_functions}

Analyze the query and context to determine:
1. What user information (ASK_*) do we still need?
2. What backend data (FETCH_*) should we retrieve?
3. In what order should we collect this information?

Consider the specific query details - not all typical requirements may be needed, and some atypical ones might be required based on the query specifics.
"""

CONTEXTUAL_QUESTIONS_PROMPT = """
Generate contextual questions for a shopping query.

Original Query: "{query}"
Intent: {intent_l3}
Product Category: {product_category}

Question Generation Hints:
{slot_hints}

Category-Specific Hints:
{category_hints}

Generate natural, contextual questions for these information needs:
{slots_needed}

Guidelines:
- Make questions specific to the query context
- Include relevant options where appropriate
- For budget questions, use category-appropriate ranges
- For preferences, include category-relevant options
- Questions should feel conversational and helpful
"""

ANSWER_GENERATION_PROMPT = """
You are an e-commerce assistant.

### USER QUERY
{query}

### USER PROFILE
{permanent}

### SESSION ANSWERS
{session}

### FETCHED DATA
{fetched}

### Instructions
Reply with a single JSON object **without code fences**, exactly:
{
  "response_type": "question" | "final_answer",
  "message": "string"
}
"""

# ─────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class IntentResult:
    layer1: str
    layer2: str
    layer3: str

# ─────────────────────────────────────────────────────────────
# LLM Service Class
# ─────────────────────────────────────────────────────────────

class LLMService:
    """Service class for all LLM interactions."""
    
    def __init__(self):
        self.anthropic = anthropic.Anthropic(api_key=Cfg.ANTHROPIC_API_KEY)
    
    async def classify_intent(self, query: str) -> IntentResult:
        """Classify user query into intent hierarchy."""
        prompt = INTENT_CLASSIFICATION_PROMPT.format(query=query.strip())
        
        resp = self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[INTENT_CLASSIFICATION_TOOL],
            tool_choice={"type": "tool", "name": "classify_intent"},
            temperature=0.2,
            max_tokens=100,
        )
        
        tool_use = pick_tool(resp, "classify_intent")
        if not tool_use:
            raise ValueError("No classify_intent tool use found in response")
        
        args = tool_use.input
        return IntentResult(args["layer1"], args["layer2"], args["layer3"])
    
    async def classify_follow_up(self, query: str, ctx: UserContext) -> FollowUpResult:
        """Determine if query is a follow-up and extract patch information."""
        # Quick heuristic: if no history, can't be follow-up
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
            
            tool_use = pick_tool(resp, "classify_follow_up")
            if not tool_use:
                return FollowUpResult(False, FollowUpPatch(slots={}))
            
            ipt = tool_use.input
            patch = FollowUpPatch(
                slots=ipt.get("patch", {}).get("slots", {}),
                intent_override=ipt.get("patch", {}).get("intent_override"),
                reset_context=ipt.get("patch", {}).get("reset_context", False),
            )
            return FollowUpResult(
                bool(ipt.get("is_follow_up", False)), 
                patch, 
                ipt.get("reason", "")
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Follow-up classification failed: %s", exc)
            return FollowUpResult(False, FollowUpPatch(slots={}))
    
    async def assess_delta_requirements(
        self, 
        query: str, 
        ctx: UserContext, 
        patch: FollowUpPatch
    ) -> List[BackendFunction]:
        """Assess which backend functions need to be called after a follow-up."""
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
            
            tool_use = pick_tool(resp, "assess_delta_requirements")
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
    
    async def assess_requirements(
        self, 
        query: str, 
        intent: QueryIntent, 
        layer3: str, 
        ctx: UserContext
    ) -> RequirementAssessment:
        """Assess what information is needed to fulfill the user's query."""
        assessment_tool = build_assessment_tool()
        intent_config = INTENT_MAPPING.get(layer3, {})
        suggested_slots = [s.value for s in intent_config.get("suggested_slots", [])]
        suggested_functions = [f.value for f in intent_config.get("suggested_functions", [])]

        prompt = REQUIREMENTS_ASSESSMENT_PROMPT.format(
            query=query,
            intent=intent.value,
            layer3=layer3,
            perm_keys=list(ctx.permanent.keys()),
            sess_keys=list(ctx.session.keys()),
            fetched_keys=list(ctx.fetched_data.keys()),
            suggested_slots=suggested_slots,
            suggested_functions=suggested_functions,
        )

        resp = self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[assessment_tool],
            tool_choice={"type": "tool", "name": "assess_requirements"},
            temperature=0.1,
            max_tokens=500,
        )
        
        tool_use = pick_tool(resp, "assess_requirements")
        if not tool_use:
            raise ValueError("No assess_requirements tool use found")
        
        args = tool_use.input

        missing: List[Union[BackendFunction, UserSlot]] = []
        for item in args["missing_data"]:
            func = string_to_function(item["function"])
            if func:
                missing.append(func)

        order: List[Union[BackendFunction, UserSlot]] = []
        for f in args["priority_order"]:
            func = string_to_function(f)
            if func:
                order.append(func)

        rationale = {item["function"]: item["rationale"] for item in args["missing_data"]}

        return RequirementAssessment(
            intent=intent,
            missing_data=missing,
            rationale=rationale,
            priority_order=order or missing,
        )
    
    async def generate_contextual_questions(
        self,
        slots_needed: List[UserSlot],
        query: str,
        intent_l3: str,
        ctx: UserContext,
    ) -> Dict[str, Dict[str, Any]]:
        """Generate contextual questions for needed user slots."""
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
        questions_tool = build_questions_tool(slots_needed)
        
        prompt = CONTEXTUAL_QUESTIONS_PROMPT.format(
            query=query,
            intent_l3=intent_l3,
            product_category=product_category,
            slot_hints=json.dumps(slot_hints, indent=2),
            category_hints=json.dumps(category_hints, indent=2),
            slots_needed=[slot.value for slot in slots_needed],
        )

        try:
            resp = self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[questions_tool],
                tool_choice={"type": "tool", "name": "generate_questions"},
                temperature=0.3,
                max_tokens=800,
            )
            
            tool_use = pick_tool(resp, "generate_questions")
            if tool_use:
                return tool_use.input.get("questions", {})
        except Exception as exc:  # noqa: BLE001
            log.warning("Contextual question generation failed: %s", exc)

        return {}
    
    async def generate_answer(
        self, 
        query: str, 
        ctx: UserContext, 
        fetched: Dict[str, Any]
    ) -> Dict[str, str]:
        """Generate final answer using LLM."""
        prompt = ANSWER_GENERATION_PROMPT.format(
            query=query,
            permanent=ctx.permanent,
            session=ctx.session,
            fetched=fetched,
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

# ─────────────────────────────────────────────────────────────
# Helper functions for intent mapping
# ─────────────────────────────────────────────────────────────

def map_leaf_to_query_intent(leaf: str) -> QueryIntent:
    """Map layer3 intent to QueryIntent enum."""
    return INTENT_MAPPING.get(leaf, {}).get("query_intent", QueryIntent.GENERAL_HELP)