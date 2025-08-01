"""
LLM service module for ShoppingBotCore
──────────────────────────────────────
• Handles all Anthropic calls
• Defines tool schemas
• Holds all prompt templates
• Parses / normalises results

Changes (2025-07-31):
• ANSWER_GENERATION_PROMPT now instructs the model to return a six-section
  object ( + / ALT / – / BUY / OVERRIDE / INFO ).
• generate_answer() converts that dict -> formatted text via
  bot_helpers.sections_to_text().
• Improved build_questions_tool to generate proper discrete options
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
from .bot_helpers import pick_tool, string_to_function, sections_to_text

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
    """Build the contextual questions generation tool dynamically with improved option generation."""
    slot_properties = {}
    
    for slot in slots_needed:
        slot_properties[slot.value] = {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The question text to ask the user"
                },
                "type": {
                    "type": "string",
                    "enum": ["multi_choice"],
                    "description": "Always multi_choice"
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Exactly 3 discrete, actionable options (no instructional text or 'e.g.' examples)"
                },
                "placeholder": {
                    "type": "string",
                    "description": "Optional placeholder text"
                },
                "hints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional helpful hints"
                },
            },
            "required": ["message", "type", "options"],
        }
    
    return {
        "name": "generate_questions",
        "description": "Generate contextual questions for user slots with proper discrete options",
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "object",
                    "properties": slot_properties,
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
Generate contextual questions for a shopping query with EXACTLY 3 discrete options for each question.

Original Query: "{query}"
Intent: {intent_l3}
Product Category: {product_category}

Question Generation Hints:
{slot_hints}

Category-Specific Hints:
{category_hints}

Generate natural, contextual questions for these information needs:
{slots_needed}

CRITICAL REQUIREMENTS for options:
1. Provide EXACTLY 3 discrete, actionable options per question
2. Each option should be a short, specific choice (1-4 words max)
3. NO instructional text like "Consider..." or "Think about..."
4. NO examples prefixed with "e.g." or similar
5. Options should be directly selectable answers
6. Make options relevant to the query context and product category

Examples of GOOD options:
- For budget: ["Under $100", "$100-500", "Over $500"]
- For size: ["Small", "Medium", "Large"]
- For brand preference: ["Premium brands", "Popular brands", "Budget-friendly"]
- For features: ["Basic features", "Standard features", "Advanced features"]
- For style: ["Modern", "Classic", "Trendy"]

Examples of BAD options (AVOID):
- ["Consider your budget, quality needs, etc.", "Think about features", "Other"]
- ["e.g. Nike, Adidas", "Such as wireless, waterproof", "etc."]

Guidelines:
- Make questions specific to the query context
- Use category-appropriate option ranges
- Questions should feel conversational and helpful
- Each question must have exactly 3 options that are clear choices
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
Respond with **only** a JSON object (no code fences) shaped like:
{{
  "response_type": "final_answer",
  "sections": {{
    "+": "string",
    "ALT": "string",
    "-": "string",
    "BUY": "string",
    "OVERRIDE": "string",
    "INFO": "string"
  }}
}}
Always include all six keys; leave a key an empty string if you have no content.
"""

# ─────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────

@dataclass
class IntentResult:
    layer1: str
    layer2: str
    layer3: str

# ─────────────────────────────────────────────────────────────
# LLM Service
# ─────────────────────────────────────────────────────────────

class LLMService:
    """Service class for all LLM interactions."""

    def __init__(self) -> None:
        self.anthropic = anthropic.Anthropic(api_key=Cfg.ANTHROPIC_API_KEY)

    # ---------------- INTENT ----------------
    async def classify_intent(self, query: str) -> IntentResult:
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
            raise ValueError("No classify_intent tool use found")
        args = tool_use.input
        return IntentResult(args["layer1"], args["layer2"], args["layer3"])

    # ---------------- FOLLOW-UP ----------------
    async def classify_follow_up(self, query: str, ctx: UserContext) -> FollowUpResult:
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
                ipt.get("reason", ""),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Follow-up classification failed: %s", exc)
            return FollowUpResult(False, FollowUpPatch(slots={}))

    # ---------------- DELTA ASSESS ----------------
    async def assess_delta_requirements(
        self, query: str, ctx: UserContext, patch: FollowUpPatch
    ) -> List[BackendFunction]:
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

    # ---------------- REQUIREMENTS ----------------
    async def assess_requirements(
        self,
        query: str,
        intent: QueryIntent,
        layer3: str,
        ctx: UserContext,
    ) -> RequirementAssessment:
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

    # ---------------- QUESTION GENERATION ----------------
    async def generate_contextual_questions(
        self,
        slots_needed: List[UserSlot],
        query: str,
        intent_l3: str,
        ctx: UserContext,
    ) -> Dict[str, Dict[str, Any]]:
        """Generate contextual questions with improved option generation."""
        if not slots_needed:
            return {}

        # Get product category from session if available
        product_category = ctx.session.get("product_category", "general products")
        
        # Build slot hints
        slot_hints_lines = []
        for slot in slots_needed:
            hint_config = SLOT_QUESTIONS.get(slot, {})
            if "hint" in hint_config:
                slot_hints_lines.append(f"- {slot.value}: {hint_config['hint']}")
        slot_hints = "\n".join(slot_hints_lines) if slot_hints_lines else "No specific hints available."

        # Get category-specific hints
        category_hints = CATEGORY_QUESTION_HINTS.get(product_category, "Focus on the most relevant attributes for the user's query.")

        # Prepare slots needed description
        slots_needed_desc = ", ".join([slot.value for slot in slots_needed])

        prompt = CONTEXTUAL_QUESTIONS_PROMPT.format(
            query=query,
            intent_l3=intent_l3,
            product_category=product_category,
            slot_hints=slot_hints,
            category_hints=category_hints,
            slots_needed=slots_needed_desc,
        )

        try:
            questions_tool = build_questions_tool(slots_needed)
            resp = self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[questions_tool],
                tool_choice={"type": "tool", "name": "generate_questions"},
                temperature=0.3,
                max_tokens=800,
            )

            tool_use = pick_tool(resp, "generate_questions")
            if not tool_use:
                log.warning("No generate_questions tool use found")
                return {}

            questions_data = tool_use.input.get("questions", {})
            
            # Process the questions to ensure proper format
            processed_questions = {}
            for slot_value, question_data in questions_data.items():
                # Ensure we have exactly 3 options and they're properly formatted
                options = question_data.get("options", [])
                
                # Convert string options to proper format if needed
                if isinstance(options, list) and len(options) >= 3:
                    formatted_options = []
                    for i, opt in enumerate(options[:3]):  # Take first 3
                        if isinstance(opt, str):
                            # Clean up the option text
                            clean_opt = opt.strip()
                            # Skip options that look like instructions
                            if any(phrase in clean_opt.lower() for phrase in ["consider", "think about", "e.g.", "such as", "etc."]):
                                continue
                            formatted_options.append({
                                "label": clean_opt,
                                "value": clean_opt
                            })
                    
                    # If we don't have enough good options, add generic ones
                    while len(formatted_options) < 3:
                        if len(formatted_options) == 0:
                            formatted_options.append({"label": "Yes", "value": "Yes"})
                        elif len(formatted_options) == 1:
                            formatted_options.append({"label": "No", "value": "No"})
                        else:
                            formatted_options.append({"label": "Other", "value": "Other"})
                    
                    processed_questions[slot_value] = {
                        "message": question_data.get("message", f"Please provide your {slot_value.lower().replace('_', ' ')}"),
                        "type": "multi_choice",
                        "options": formatted_options[:3],  # Ensure exactly 3
                        "placeholder": question_data.get("placeholder", ""),
                        "hints": question_data.get("hints", [])
                    }
                else:
                    # Fallback if options are malformed
                    processed_questions[slot_value] = self._generate_fallback_question(slot_value)

            return processed_questions

        except Exception as exc:
            log.warning("Contextual question generation failed: %s", exc)
            # Return fallback questions for all slots
            return {slot.value: self._generate_fallback_question(slot.value) for slot in slots_needed}

    def _generate_fallback_question(self, slot_value: str) -> Dict[str, Any]:
        """Generate a fallback question with proper options."""
        slot_name = slot_value.lower().replace("ask_", "").replace("_", " ")
        
        # Provide sensible default options based on slot type
        if "budget" in slot_name or "price" in slot_name:
            options = [
                {"label": "Budget-friendly", "value": "Budget-friendly"},
                {"label": "Mid-range", "value": "Mid-range"},
                {"label": "Premium", "value": "Premium"}
            ]
        elif "size" in slot_name:
            options = [
                {"label": "Small", "value": "Small"},
                {"label": "Medium", "value": "Medium"},
                {"label": "Large", "value": "Large"}
            ]
        elif "brand" in slot_name:
            options = [
                {"label": "Popular brands", "value": "Popular brands"},
                {"label": "Premium brands", "value": "Premium brands"},
                {"label": "Any brand", "value": "Any brand"}
            ]
        elif "color" in slot_name:
            options = [
                {"label": "Dark colors", "value": "Dark colors"},
                {"label": "Light colors", "value": "Light colors"},
                {"label": "Bright colors", "value": "Bright colors"}
            ]
        else:
            options = [
                {"label": "Important", "value": "Important"},
                {"label": "Somewhat important", "value": "Somewhat important"},
                {"label": "Not important", "value": "Not important"}
            ]
        
        return {
            "message": f"What's your preference for {slot_name}?",
            "type": "multi_choice",
            "options": options,
            "placeholder": "",
            "hints": []
        }

    # ---------------- ANSWER GENERATION ----------------
    async def generate_answer(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Generate final answer.
        • Preferred path: model returns six-section dict.
        • Fallback: old {response_type,message} format.
        """
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

        data = extract_json_block(resp.content[0].text)

        # Happy-path: six-section answer
        if isinstance(data, dict) and data.get("response_type") == "final_answer" and "sections" in data:
            text = sections_to_text(data["sections"])
            return {
                "response_type": "final_answer",
                "message": text,
                "sections": data["sections"],
            }

        # Fallback to older contract if LLM ignores new schema
        if "response_type" in data and "message" in data:
            return data

        return {
            "response_type": "final_answer",
            "message": resp.content[0].text.strip(),
        }

# ─────────────────────────────────────────────────────────────
# Helper – map layer3 leaf to QueryIntent
# ─────────────────────────────────────────────────────────────
def map_leaf_to_query_intent(leaf: str) -> QueryIntent:
    return INTENT_MAPPING.get(leaf, {}).get("query_intent", QueryIntent.GENERAL_HELP)