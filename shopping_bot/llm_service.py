# shopping_bot/llm_service.py
"""
LLM service module for ShoppingBotCore
──────────────────────────────────────
UPDATED: 4-Intent Classification for Product Queries
- Is this good?
- Which is better?
- Show me alternate
- Show me options
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import anthropic

from .bot_helpers import pick_tool, string_to_function
from .config import get_config
from .enums import BackendFunction, QueryIntent, UserSlot
from .intent_config import (CATEGORY_QUESTION_HINTS, INTENT_MAPPING,
                            SLOT_QUESTIONS)
from .models import (FollowUpPatch, FollowUpResult, ProductData,
                     RequirementAssessment, UserContext)
from .recommendation import get_recommendation_service
from .utils.helpers import extract_json_block

Cfg = get_config()
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# NEW: 4-Intent Classification Tool
# ─────────────────────────────────────────────────────────────

PRODUCT_INTENT_TOOL = {
    "name": "classify_product_intent",
    "description": "Classify serious product-related queries into 4 specific intents",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["is_this_good", "which_is_better", "show_me_alternate", "show_me_options"],
                "description": "The specific product intent"
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence level in classification"
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation for the classification"
            }
        },
        "required": ["intent", "confidence"]
    }
}

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
                "description": "Specific intent from the configured taxonomy",
            },
            "is_product_related": {
                "type": "boolean",
                "description": "Whether this is a serious product-related query"
            }
        },
        "required": ["layer1", "layer2", "layer3", "is_product_related"],
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

PRODUCT_RESPONSE_TOOL = {
    "name": "generate_product_response",
    "description": "Generate structured response with product recommendations and descriptions",
    "input_schema": {
        "type": "object",
        "properties": {
            "response_type": {
                "type": "string",
                "enum": ["final_answer"],
                "description": "Always final_answer for product responses"
            },
            "summary_message": {
                "type": "string",
                "description": "Overall summary addressing the user's query"
            },
            "products": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Product ID from search results"},
                        "text": {"type": "string", "description": "Product name/title"},
                        "description": {"type": "string", "description": "One-liner on why to buy this product"},
                        "price": {"type": "string", "description": "Price with currency"},
                        "special_features": {"type": "string", "description": "Key differentiators"}
                    },
                    "required": ["text", "description"]
                },
                "maxItems": 10,
                "description": "Product list with compelling descriptions"
            }
        },
        "required": ["response_type", "summary_message", "products"]
    }
}

SIMPLE_RESPONSE_TOOL = {
    "name": "generate_simple_response",
    "description": "Generate simple text response for non-product queries",
    "input_schema": {
        "type": "object",
        "properties": {
            "response_type": {
                "type": "string",
                "enum": ["final_answer", "error"],
            },
            "message": {
                "type": "string",
                "description": "Response message"
            }
        },
        "required": ["response_type", "message"]
    }
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
                    "description": "Exactly 3 discrete, actionable options"
                },
            },
            "required": ["message", "type", "options"],
        }
    return {
        "name": "generate_questions",
        "description": "Generate contextual questions for user slots",
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
You are an e-commerce intent classifier.

GOAL:
1. Classify the user's **latest message** into the 3-layer hierarchy
2. Determine if this is a serious product-related query that should use the new 4-intent system

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
   E1. Account     → [Account_Profile_Management]
   E2. Support     → [Technical_Support, General_Help]

### Product-Related Queries:
Set is_product_related=true ONLY for serious product queries like:
- Product_Discovery, Recommendation, Specific_Product_Search, Product_Comparison
- Queries asking for product evaluations, comparisons, alternatives, or options

INPUTS:
- Recent context: {recent_context}
- Latest user message: "{query}"

Return ONLY a tool call to classify_intent.
"""

PRODUCT_INTENT_CLASSIFICATION_PROMPT = """
You are classifying a serious product-related query into exactly 4 intents:

1. **is_this_good** - User asking for evaluation/validation of a specific product or small set
2. **which_is_better** - User comparing 2-3 specific items and wants a recommendation 
3. **show_me_alternate** - User wants alternatives to something they've seen/mentioned
4. **show_me_options** - User wants to explore a category/type with multiple choices

EXAMPLES:
- "Is this protein powder good?" → is_this_good
- "Should I buy Samsung Galaxy or iPhone?" → which_is_better  
- "Show me alternatives to this laptop" → show_me_alternate
- "What are my options for wireless headphones?" → show_me_options

USER QUERY: "{query}"
CONTEXT: {context}

Return ONLY a tool call to classify_product_intent.
"""

FOLLOW_UP_PROMPT_TEMPLATE = """
You are determining if the user's NEW message should be treated as a follow-up.

### Last snapshot:
{last_snapshot}

### Current session slots:
{current_slots}

### New user message:
"{query}"

Return a tool call to classify_follow_up.
"""

DELTA_ASSESS_PROMPT = """
Determine which backend functions must run after a follow-up patch.

### Query: "{query}"
### Patch: {patch}
### Current Context Keys:
- permanent: {perm_keys}
- session: {sess_keys}
- fetched: {fetched_keys}

Return ONLY backend FETCH_* functions needed, not ASK_* slots.
"""

REQUIREMENTS_ASSESSMENT_PROMPT = """
Analyze what information is needed for this e-commerce query.

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

Determine what user information (ASK_*) and backend data (FETCH_*) are needed.
"""

CONTEXTUAL_QUESTIONS_PROMPT = """
Generate contextual questions for a shopping query with EXACTLY 3 discrete options for each.

Original Query: "{query}"
Intent: {intent_l3}
Product Category: {product_category}

Question Generation Hints:
{slot_hints}

Category-Specific Hints:
{category_hints}

Generate questions for: {slots_needed}

Requirements:
- EXACTLY 3 discrete, actionable options per question
- Each option should be 1-4 words max
- NO instructional text or examples
"""

PRODUCT_RESPONSE_PROMPT = """
You are an e-commerce assistant helping users find products.

### USER QUERY
{query}

### INTENT
{intent_l3}

### USER CONTEXT
Session: {session}
Permanent: {permanent}

### PRODUCT SEARCH RESULTS
{products_json}

### INSTRUCTIONS
Create a helpful response with:

1. **summary_message**: A natural, conversational summary that:
   - Addresses the user's specific query
   - Mentions how many products were found
   - Highlights the best options briefly
   - Is 1-3 sentences long

2. **products**: For each relevant product (up to 10):
   - text: The exact product name from results
   - description: A compelling one-liner about why this product is worth buying
   - price: The price with currency (e.g., "₹60")
   - special_features: Key differentiators (e.g., "High protein, organic")

Focus on actual product attributes from the search results.
Make descriptions specific and benefit-focused.
If no products found, provide helpful message with empty products array.

Return ONLY a tool call to generate_product_response.
"""

SIMPLE_RESPONSE_PROMPT = """
You are an e-commerce assistant.

### USER QUERY
{query}

### INTENT
layer3 = {intent_l3}
query_intent = {query_intent}

### USER CONTEXT
{permanent}
{session}

### DATA
{fetched}

### Instructions
Write ONE clear, concise reply for this {query_intent} query.
Be specific and actionable (1-3 sentences).

Return ONLY a tool call to generate_simple_response.
"""


# ─────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class IntentResult:
    layer1: str
    layer2: str
    layer3: str
    is_product_related: bool = False

@dataclass
class ProductIntentResult:
    intent: str
    confidence: float
    reasoning: str = ""


# ─────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────

def _strip_keys(obj: Any) -> Any:
    """Recursively trim whitespace around dict keys."""
    if isinstance(obj, dict):
        new: Dict[str, Any] = {}
        for k, v in obj.items():
            key = k.strip() if isinstance(k, str) else k
            new[key] = _strip_keys(v)
        return new
    if isinstance(obj, list):
        return [_strip_keys(x) for x in obj]
    return obj


def _safe_get(d: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Get value by key, trying both exact and stripped variants."""
    if key in d:
        return d[key]
    if isinstance(key, str):
        for k in d.keys():
            if isinstance(k, str) and k.strip() == key:
                return d[k]
    return default


# ─────────────────────────────────────────────────────────────
# LLM Service
# ─────────────────────────────────────────────────────────────

class LLMService:
    """Service class for all LLM interactions."""

    def __init__(self) -> None:
        self.anthropic = anthropic.AsyncAnthropic(api_key=Cfg.ANTHROPIC_API_KEY)
        self._recommendation_service = get_recommendation_service()

    # ---------------- UPDATED: INTENT CLASSIFICATION ----------------
    async def classify_intent(self, query: str, ctx: Optional[UserContext] = None) -> IntentResult:
        """Updated intent classification with product-related detection."""
        recent_context: Dict[str, Any] = {}
        try:
            if ctx:
                history = ctx.session.get("history", [])
                if history:
                    last = history[-1]
                    recent_context = {
                        "last_intent_l3": last.get("intent"),
                        "last_slots": {k: v for k, v in (last.get("slots") or {}).items() if v},
                    }
        except Exception as exc:
            log.debug("Failed to build recent_context: %s", exc)

        prompt = INTENT_CLASSIFICATION_PROMPT.format(
            recent_context=json.dumps(recent_context, ensure_ascii=False),
            query=query.strip(),
        )
        
        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[INTENT_CLASSIFICATION_TOOL],
            tool_choice={"type": "tool", "name": "classify_intent"},
            temperature=0.2,
            max_tokens=150,
        )
        
        tool_use = pick_tool(resp, "classify_intent")
        if not tool_use:
            return IntentResult("E", "E2", "General_Help", False)

        args = _strip_keys(tool_use.input or {})
        layer1 = args.get("layer1", "E")
        layer2 = args.get("layer2", "E2")
        layer3 = args.get("layer3", "General_Help")
        is_product_related = bool(args.get("is_product_related", False))

        if layer3 not in INTENT_MAPPING:
            layer1, layer2, layer3, is_product_related = "E", "E2", "General_Help", False

        return IntentResult(layer1, layer2, layer3, is_product_related)

    # ---------------- NEW: PRODUCT INTENT CLASSIFICATION ----------------
    async def classify_product_intent(self, query: str, ctx: UserContext) -> ProductIntentResult:
        """Classify product-related queries into the 4 specific intents."""
        
        # Build context for classification
        context_info = {
            "session_data": {k: v for k, v in ctx.session.items() if k in ['last_recommendation', 'product_category', 'budget']},
            "recent_fetched": list(ctx.fetched_data.keys())[-3:] if ctx.fetched_data else [],
            "conversation_history": ctx.session.get("history", [])[-2:] if ctx.session.get("history") else []
        }

        prompt = PRODUCT_INTENT_CLASSIFICATION_PROMPT.format(
            query=query.strip(),
            context=json.dumps(context_info, ensure_ascii=False)
        )
        
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[PRODUCT_INTENT_TOOL],
                tool_choice={"type": "tool", "name": "classify_product_intent"},
                temperature=0.1,
                max_tokens=200,
            )
            
            tool_use = pick_tool(resp, "classify_product_intent")
            if not tool_use:
                return ProductIntentResult("show_me_options", 0.5, "fallback")

            args = _strip_keys(tool_use.input or {})
            intent = args.get("intent", "show_me_options")
            confidence = float(args.get("confidence", 0.5))
            reasoning = args.get("reasoning", "")
            
            return ProductIntentResult(intent, confidence, reasoning)
            
        except Exception as exc:
            log.warning("Product intent classification failed: %s", exc)
            return ProductIntentResult("show_me_options", 0.3, f"error: {exc}")

    # ---------------- UNIFIED RESPONSE GENERATION ----------------
    async def generate_response(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
        intent_l3: str,
        query_intent: QueryIntent,
        product_intent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Enhanced unified response generation."""
        product_intents = {
            "Product_Discovery", "Recommendation", 
            "Specific_Product_Search", "Product_Comparison"
        }
        
        has_products = self._has_product_results(fetched)
        
        if intent_l3 in product_intents and has_products:
            result = await self._generate_product_response(query, ctx, fetched, intent_l3)
            if product_intent:
                result["product_intent"] = product_intent
            return result
        else:
            return await self._generate_simple_response(query, ctx, fetched, intent_l3, query_intent)

    def _has_product_results(self, fetched: Dict[str, Any]) -> bool:
        """Check if fetched data contains product results."""
        if 'search_products' in fetched:
            search_data = fetched['search_products']
            if isinstance(search_data, dict):
                data = search_data.get('data', search_data)
                return bool(data.get('products'))
        return False

    async def _generate_product_response(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
        intent_l3: str
    ) -> Dict[str, Any]:
        """Generate structured product response with descriptions."""
        products_data = []
        if 'search_products' in fetched:
            search_data = fetched['search_products']
            if isinstance(search_data, dict):
                data = search_data.get('data', search_data)
                products_data = data.get('products', [])[:10]
        
        if not products_data:
            return {
                "response_type": "final_answer",
                "summary_message": "I couldn't find any products matching your search. Please try different keywords.",
                "products": []
            }
        
        prompt = PRODUCT_RESPONSE_PROMPT.format(
            query=query,
            intent_l3=intent_l3,
            session=json.dumps(ctx.session, ensure_ascii=False),
            permanent=json.dumps(ctx.permanent, ensure_ascii=False),
            products_json=json.dumps(products_data, ensure_ascii=False)
        )
        
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[PRODUCT_RESPONSE_TOOL],
                tool_choice={"type": "tool", "name": "generate_product_response"},
                temperature=0.7,
                max_tokens=1500,
            )
            
            tool_use = pick_tool(resp, "generate_product_response")
            if not tool_use:
                return self._create_fallback_product_response(products_data, query)
            
            result = _strip_keys(tool_use.input or {})
            
            for i, product in enumerate(result.get("products", [])):
                if "id" not in product and i < len(products_data):
                    product["id"] = f"prod_{hash(products_data[i].get('name', ''))%1000000}"
            
            return result
            
        except Exception as exc:
            log.error(f"Product response generation failed: {exc}")
            return self._create_fallback_product_response(products_data, query)

    def _create_fallback_product_response(self, products_data: List[Dict], query: str) -> Dict[str, Any]:
        """Create a fallback product response if LLM fails."""
        products = []
        for p in products_data[:5]:
            products.append({
                "id": f"prod_{hash(p.get('name', ''))%1000000}",
                "text": p.get("name", "Product"),
                "description": f"Quality product at {p.get('price', 'great price')}",
                "price": f"₹{p.get('price', 'N/A')}",
                "special_features": ""
            })
        
        return {
            "response_type": "final_answer",
            "summary_message": f"I found {len(products_data)} products for '{query}'.",
            "products": products
        }

    async def _generate_simple_response(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
        intent_l3: str,
        query_intent: QueryIntent
    ) -> Dict[str, Any]:
        """Generate simple text response for non-product queries."""
        prompt = SIMPLE_RESPONSE_PROMPT.format(
            query=query,
            intent_l3=intent_l3,
            query_intent=query_intent.value,
            permanent=json.dumps(ctx.permanent, ensure_ascii=False),
            session=json.dumps(ctx.session, ensure_ascii=False),
            fetched=json.dumps(fetched, ensure_ascii=False),
        )
        
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[SIMPLE_RESPONSE_TOOL],
                tool_choice={"type": "tool", "name": "generate_simple_response"},
                temperature=0.4,
                max_tokens=400,
            )
            
            tool_use = pick_tool(resp, "generate_simple_response")
            if not tool_use:
                return {
                    "response_type": "final_answer",
                    "message": "I can help you with shopping queries. What are you looking for?"
                }
            
            result = _strip_keys(tool_use.input or {})
            return result
            
        except Exception:
            return {
                "response_type": "final_answer",
                "message": "I can help you with shopping queries. What are you looking for?"
            }

    # ---------------- EXISTING METHODS ----------------
    async def generate_answer(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compatibility method."""
        return await self.generate_response(
            query, ctx, fetched, 
            intent_l3="Recommendation",
            query_intent=QueryIntent.RECOMMENDATION
        )

    async def generate_simple_reply(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
        *,
        intent_l3: str,
        query_intent: QueryIntent
    ) -> Dict[str, Any]:
        """Compatibility method."""
        return await self.generate_response(
            query, ctx, fetched, intent_l3, query_intent
        )

    async def classify_follow_up(self, query: str, ctx: UserContext) -> FollowUpResult:
        """Classify if query is a follow-up."""
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
            resp = await self.anthropic.messages.create(
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

            ipt = _strip_keys(tool_use.input or {})
            patch_dict = _safe_get(ipt, "patch", {}) or {}
            slots_dict = patch_dict.get("slots", {})
            
            patch = FollowUpPatch(
                slots=slots_dict,
                intent_override=patch_dict.get("intent_override"),
                reset_context=bool(patch_dict.get("reset_context", False)),
            )
            
            return FollowUpResult(
                bool(ipt.get("is_follow_up", False)),
                patch,
                ipt.get("reason", ""),
            )
        except Exception as exc:
            log.warning("Follow-up classification failed: %s", exc)
            return FollowUpResult(False, FollowUpPatch(slots={}))

    async def assess_delta_requirements(
        self, query: str, ctx: UserContext, patch: FollowUpPatch
    ) -> List[BackendFunction]:
        """Assess what needs to be fetched for a follow-up."""
        prompt = DELTA_ASSESS_PROMPT.format(
            query=query,
            patch=json.dumps(patch.__dict__, ensure_ascii=False),
            perm_keys=list(ctx.permanent.keys()),
            sess_keys=list(ctx.session.keys()),
            fetched_keys=list(ctx.fetched_data.keys()),
        )
        
        try:
            resp = await self.anthropic.messages.create(
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
            
            items_raw = tool_use.input.get("fetch_functions", [])
            out: List[BackendFunction] = []
            for it in items_raw:
                try:
                    out.append(BackendFunction(it.strip() if isinstance(it, str) else it))
                except ValueError:
                    pass
            return out
        except Exception as exc:
            log.warning("Delta assess failed: %s", exc)
            return []

    async def assess_requirements(
        self,
        query: str,
        intent: QueryIntent,
        layer3: str,
        ctx: UserContext,
    ) -> RequirementAssessment:
        """Assess what data is needed for the query."""
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

        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[assessment_tool],
            tool_choice={"type": "tool", "name": "assess_requirements"},
            temperature=0.1,
            max_tokens=500,
        )

        tool_use = pick_tool(resp, "assess_requirements")
        if not tool_use:
            return RequirementAssessment(
                intent=intent, missing_data=[], rationale={}, priority_order=[]
            )

        args = _strip_keys(tool_use.input or {})
        missing_items = args.get("missing_data", []) or []
        priority_items = args.get("priority_order", []) or []

        missing: List[Union[BackendFunction, UserSlot]] = []
        for item in missing_items:
            fn = item.get("function") if isinstance(item, dict) else None
            func = string_to_function(fn) if fn else None
            if func:
                missing.append(func)

        order: List[Union[BackendFunction, UserSlot]] = []
        for f in priority_items:
            func = string_to_function(f)
            if func:
                order.append(func)

        rationale = {}
        try:
            rationale = {
                item.get("function"): item.get("rationale", "")
                for item in missing_items
                if isinstance(item, dict) and item.get("function")
            }
        except Exception:
            pass

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
        """Generate contextual questions."""
        if not slots_needed:
            return {}

        product_category = ctx.session.get("product_category", "general products")

        slot_hints_lines = []
        for slot in slots_needed:
            hint_config = SLOT_QUESTIONS.get(slot, {})
            if "hint" in hint_config:
                slot_hints_lines.append(f"- {slot.value}: {hint_config['hint']}")
        slot_hints = "\n".join(slot_hints_lines) if slot_hints_lines else "No specific hints available."

        category_hints = CATEGORY_QUESTION_HINTS.get(product_category, "Focus on relevant attributes.")
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
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[questions_tool],
                tool_choice={"type": "tool", "name": "generate_questions"},
                temperature=0.3,
                max_tokens=800,
            )

            tool_use = pick_tool(resp, "generate_questions")
            if not tool_use:
                return {}

            questions_data = _strip_keys(tool_use.input.get("questions", {}) or {})
            
            processed_questions = {}
            for slot_value, question_data in questions_data.items():
                options = question_data.get("options", [])
                
                formatted_options = []
                for opt in options[:3]:
                    if isinstance(opt, str):
                        formatted_options.append({"label": opt.strip(), "value": opt.strip()})
                    elif isinstance(opt, dict) and "label" in opt and "value" in opt:
                        formatted_options.append(opt)
                
                while len(formatted_options) < 3:
                    formatted_options.append({"label": "Other", "value": "Other"})
                
                processed_questions[slot_value] = {
                    "message": question_data.get("message", f"What's your {slot_value.lower().replace('_', ' ')}?"),
                    "type": "multi_choice",
                    "options": formatted_options[:3]
                }
            
            return processed_questions
            
        except Exception as exc:
            log.warning("Question generation failed: %s", exc)
            return {}

    async def extract_es_params(self, ctx: UserContext) -> Dict[str, Any]:
        """Extract ES parameters via recommendation service."""
        try:
            params = await self._recommendation_service.extract_es_params(ctx)
            log.debug(f"ES params extracted: {params}")
            return params
        except Exception as exc:
            log.warning("ES param extraction failed: %s", exc)
            return {}


# ─────────────────────────────────────────────────────────────
# Helper function
# ─────────────────────────────────────────────────────────────

def map_leaf_to_query_intent(leaf: str) -> QueryIntent:
    return INTENT_MAPPING.get(leaf, {}).get("query_intent", QueryIntent.GENERAL_HELP)