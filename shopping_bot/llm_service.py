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

import asyncio
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
# Avoid top-level import of es_products to prevent circular import at app startup
from .utils.helpers import extract_json_block

Cfg = get_config()
log = logging.getLogger(__name__)
# ─────────────────────────────────────────────────────────────
# NEW: Two-call ES pipeline tools (flag-gated)
# ─────────────────────────────────────────────────────────────

PLAN_ES_SEARCH_TOOL = {
    "name": "plan_es_search",
    "description": "Plan Elasticsearch parameters in one step using conversation context and current text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_product_related": {"type": "boolean"},
            "product_intent": {"type": "string", "enum": [
                "is_this_good", "which_is_better", "show_me_alternate", "show_me_options"
            ]},
            "ask_required": {"type": "boolean"},
            "es_params": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "size": {"type": "integer"},
                    "category_group": {"type": "string"},
                    "category_path": {"type": "string"},
                    "category_paths": {"type": "array", "items": {"type": "string"}},
                    "brands": {"type": "array", "items": {"type": "string"}},
                    "dietary_terms": {"type": "array", "items": {"type": "string"}},
                    "price_min": {"type": "number"},
                    "price_max": {"type": "number"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "phrase_boosts": {"type": "array", "items": {"type": "object"}},
                    "field_boosts": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["q", "category_group"]
            }
        },
        "required": ["is_product_related", "product_intent", "ask_required"]
    }
}

FINAL_ANSWER_UNIFIED_TOOL = {
    "name": "generate_final_answer_unified",
    "description": "Generate final product answer and UX in one step.",
    "input_schema": {
        "type": "object",
        "properties": {
            "response_type": {"type": "string", "enum": ["final_answer"]},
            "summary_message": {"type": "string"},
            "product_ids": {"type": "array", "items": {"type": "string"}},
            "hero_product_id": {"type": "string"},
            "ux": {
                "type": "object",
                "properties": {
                    "ux_surface": {"type": "string", "enum": ["SPM", "MPM"]},
                    "dpl_runtime_text": {"type": "string"},
                    "quick_replies": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 4}
                },
                "required": ["ux_surface", "dpl_runtime_text", "quick_replies"]
            }
        },
        "required": ["response_type", "summary_message", "ux"]
    }
}

# ─────────────────────────────────────────────────────────────
# NEW: Combined Classify+Assess Tool
# ─────────────────────────────────────────────────────────────

COMBINED_CLASSIFY_ASSESS_TOOL = {
    "name": "classify_and_assess",
    "description": "In one step: classify L3 + product_related, if product then 4-intent + two ASK_* questions (3 options each) and fetch_functions=['search_products']; if general, emit simple_response.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_follow_up": {"type": "boolean", "description": "Whether current turn is a follow-up to the immediate previous turn"},
            "is_product_related": {"type": "boolean"},
            "layer3": {"type": "string"},
            "product_intent": {
                "type": "string",
                "enum": [
                    "is_this_good",
                    "which_is_better",
                    "show_me_alternate",
                    "show_me_options"
                ]
            },
            "ask": {
                "type": "object",
                "description": "Map of up to 2 UserSlot keys (ASK_*) to question payloads",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 3,
                            "maxItems": 3
                        }
                    },
                    "required": ["message", "options"]
                }
            },
            "fetch_functions": {
                "type": "array",
                "items": {"type": "string", "enum": [f.value for f in BackendFunction]},
                "description": "If product-related, must include ['search_products']"
            },
            "simple_response": {
                "type": "object",
                "properties": {
                    "response_type": {"type": "string", "enum": ["final_answer"]},
                    "message": {"type": "string"}
                }
            }
        },
        "required": ["is_product_related", "layer3"]
    }
}



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
                "description": "Product list with compelling descriptions (SPM only)"
            },
            "hero_product_id": {
                "type": "string",
                "description": "Optional. ID of the hero product to feature first in product_ids"
            }
        },
        "required": ["response_type", "summary_message"]
    }
}

# Unified ES params generation tool - ONE authoritative call
UNIFIED_ES_PARAMS_TOOL = {
    "name": "generate_unified_es_params",
    "description": "Generate ALL Elasticsearch parameters in one authoritative call using complete context: current text, assessment base, slot answers, and conversation history.",
    "input_schema": {
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "Search query (2-5 words, no prices/currency)"},
            "size": {"type": "integer", "minimum": 1, "maximum": 50},
            "category_group": {"type": "string", "enum": ["f_and_b", "personal_care", "health_nutrition", "home_kitchen", "electronics"]},
            "category_paths": {"type": "array", "items": {"type": "string"}},
            "subcategory": {"type": "string"},
            "brands": {"type": "array", "items": {"type": "string"}},
            "dietary_terms": {"type": "array", "items": {"type": "string"}},
            "price_min": {"type": "number"},
            "price_max": {"type": "number"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "phrase_boosts": {"type": "array", "items": {"type": "object"}},
            "anchor_product_noun": {"type": "string", "description": "The most recent, specific product noun/phrase identified from context that anchors q; if none, a generic noun like 'evening snacks'"}
        },
        "required": ["q", "category_group"]
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


# ─────────────────────────────────────────────────────────────
# Dynamic Slot Selection Tool (LLM-driven)
# ─────────────────────────────────────────────────────────────

SLOT_SELECTION_TOOL = {
    "name": "select_slots_to_ask",
    "description": "Select up to 3 user slots to ask next, optimized to refine ES filters and avoid redundancy.",
    "input_schema": {
        "type": "object",
        "properties": {
            "slots": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "ASK_USER_BUDGET",
                        "ASK_DIETARY_REQUIREMENTS",
                        "ASK_USER_PREFERENCES",
                        "ASK_USE_CASE",
                        "ASK_QUANTITY"
                    ]
                },
                "maxItems": 3
            }
        },
        "required": ["slots"]
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

CLASSIFICATION RULES (MANDATORY):
- Prefer **is_this_good** whenever the query targets a single, concrete product or brand+product (e.g., "how is Veeba ketchup", "review of X", "nutrition of X", "price of X", "tell me about X").
- If the query references 2–3 named items explicitly, choose **which_is_better**.
- If the user asks for variations or alternatives to a known product, choose **show_me_alternate**.
- If the user asks broadly for options without a specific item (category exploration), choose **show_me_options**.

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

Rule: If the CURRENT message introduces a NEW product noun/category versus the last canonical query, set is_follow_up=false (treat as NEW assessment).
Otherwise, set is_follow_up=true only if the message refines/qualifies the previous query (modifier-only deltas like budget/dietary/quality).

Return a tool call to classify_follow_up.
"""

DELTA_ASSESS_PROMPT = """
Determine which backend functions must run after a follow-up.

Inputs:
- Intent L3: {intent_l3}
- Current user text: "{current_text}"
- Canonical query (last turn): "{canonical_query}"
- Last search params (compact): {last_params}
- Last fetched keys: {fetched_keys}
- Patch (delta from follow-up classifier): {patch}
- Detected signals in CURRENT text: {detected_signals}

Decision Rules (MANDATORY):
1) If the user adds/changes any search constraint (query noun, preparation method like baked/fried, quality like premium/healthier, gifting/occasion, dietary terms, brand, price range), you MUST include FETCH "search_products".
2) If the canonical query would change or keywords/phrase_boosts would change, include FETCH "search_products".
3) If price_min/price_max, category_group/path, dietary_terms/labels, brands, or keywords change, include FETCH "search_products".
4) If last_params exist but follow-up narrows/refines (e.g., premium, gifting), include FETCH "search_products".
5) Only return no fetchers if the message is purely conversational with ZERO impact on search (e.g., "thanks"), else include "search_products".

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

### ENRICHED TOP PRODUCTS (full-doc summaries; use for reasoning and evidence)
{enriched_top}

### INSTRUCTIONS
Create a helpful response with:

1. **summary_message** (USE enriched_top for top 3 products):
   Structure the summary as 4 concise bullets/sentences:
   - Positives (from tags_and_sentiments and marketing/usage/occasion signals) for the top 1-3 products
   - Quantitative quality signals (flean_score, key percentiles like protein/wholefood; penalties like sodium/sugar/additives when notable)
   - Caveats or watch-outs (e.g., ultra_processed, high sodium, saturated fat, additives)
   - One lean line summarizing the overall 10-product set (coverage/value/variety)

2. **products**: For each relevant product (up to 10):
   - text: The exact product name from results
   - description: A compelling one-liner about why this product is worth buying
   - price: The price with currency (e.g., "₹60")
   - special_features: Key differentiators (e.g., "High protein, organic")

Guidelines:
- Focus on actual product attributes from the search results and enriched_top.
- Keep it crisp, persuasive, and evidence-driven.
- Use percentiles/penalties explicitly when helpful (e.g., "Top 10% protein" or "Sodium penalty high").
- If no products found, provide helpful message with empty products array.

### STRICT FORMAT RULES (MANDATORY)
- summary_message: EXACTLY 4 bullets, each 15-25 words
- Line 1: Overall verdict with flean score and percentile (translate percentiles: 0.78 → "top 22%")
- Line 2: Top 2-3 nutritional strengths with exact numbers and units (e.g., protein_g, fiber_g)
- Line 3: Honest caveat prefixed with "Note:" or "Caution:" and include a number (e.g., sodium mg, penalty percentile)
- Line 4: Value/variety statement for the set (price span, count, use-cases). Include at least one number.

### EVIDENCE AND COMPARISON REQUIREMENTS
- Every positive claim MUST include a metric: score, grams, percentage, or ranking
- Always compare to category average or benchmark (e.g., "25% less sugar than typical chips")
- Avoid vague terms: replace "healthy" with quantified statements (e.g., "flean score 78/100")
- Keep each sentence ≤20 words; avoid marketing fluff, be professional and conversational

### CRITICAL COMMANDMENTS (NEVER VIOLATE)
- Flean Score: If score ≥ 0, higher is better → "scores 78/100". If score < 0, NEVER present as positive → write "fails quality standards" or "poor quality score".
- Percentiles:
  • BONUSES (protein, fiber, wholefood): higher percentile = GOOD → 0.90 = "top 10% for protein".
  • PENALTIES (sugar, sodium, saturated_fat, trans_fat, sweetener, calories): higher percentile = BAD → 0.90 = "bottom 10% - high sodium warning". NEVER say "top 90%" for penalties.
- Processing Honesty: Always mention "ultra_processed" as a caution; mention "processed" if >50% of products; highlight "minimally_processed" as positive.

### HERO_SELECTION_RULES (MANDATORY FOR MPM)
1) SELECT HERO: From enriched_top, choose the healthiest/cleanest product (highest positive flean; else minimally_processed; else best nutrition profile). If all are poor, pick least problematic and be honest.
2) REORDER IDS: Return hero_product_id and ensure hero appears FIRST in product_ids (followed by #2, #3, then others).
3) SUMMARY STRUCTURE FOR MPM:
   Line 1: "TOP PICK: [Hero Name] (₹[price]) - [score]/100, [best attribute with number]"
   Line 2: "Why it wins: [2-3 specific data points]"
   Line 3: "Other options: [Name 2] ([trait]), [Name 3] ([trait]), [Name 4] ([trait])"
   Line 4: "Overview: [X] total products ₹[min]-[max], [aggregate insight]"
4) DPL FOCUS: Spend ~70% on hero, ~30% on alternatives/filters.
5) BANNED WORDS: elevate, indulge, delight, companion, munchies.

### VALIDATION CHECKLIST (self-verify before responding)
- Exactly 4 lines in summary_message; DPL ≤3 sentences; numbers have units; penalties described correctly; #1 recommendation clear; no fluff; hero identified and first.

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
        api_key = getattr(Cfg, "ANTHROPIC_API_KEY", "") or ""
        if not api_key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY. Set it in environment or .env file.")
        if not isinstance(api_key, str) or not api_key.startswith("sk-ant-"):
            raise RuntimeError("Invalid ANTHROPIC_API_KEY format. It should start with 'sk-ant-'.")

        self.anthropic = anthropic.AsyncAnthropic(api_key=api_key)
        self._recommendation_service = get_recommendation_service()

    def _extract_noun(self, text: str) -> str:
        try:
            low = (text or "").lower()
            nouns = ["chips","noodles","bread","butter","jam","chocolate","soap","shampoo","juice","biscuit","cookie","ketchup","vermicelli"]
            for n in nouns:
                if n in low:
                    return n
        except Exception:
            pass
        return ""

    def _extract_product_phrase(self, text: str) -> str:
        try:
            t = (text or "").strip()
            low = t.lower()
            # Avoid dietary adjectives as head words
            stop_adj = {"gluten","vegan","palm","sugar","organic","low","less","no","free","sodium","fat"}
            targets = ["chips","noodles","biscuits","biscuit","cookies","cookie","chocolate","shampoo","soap","ketchup","bread","butter","jam","juice","vermicelli"]
            import re
            for noun in targets:
                # capture a single non-stop adjective word immediately before the noun
                m = re.search(r"\b([a-z]+)\s+" + re.escape(noun) + r"\b", low)
                if m:
                    adj = m.group(1)
                    if adj and adj not in stop_adj:
                        phrase = f"{adj} {noun}"
                        return phrase
                # fall back to noun alone
                if re.search(r"\b" + re.escape(noun) + r"\b", low):
                    return noun
        except Exception:
            pass
        return ""

    def _recency_weighted_product(self, convo_history: list[dict[str, str]]) -> str:
        try:
            # weights: most recent first
            weights = [0.5, 0.25, 0.15, 0.07, 0.03]
            scored: dict[str, float] = {}
            # iterate from latest to older
            recent = list(reversed(convo_history[-5:]))  # oldest→latest after reverse
            # We want latest first, so reverse back
            recent = list(reversed(recent))
            for idx, turn in enumerate(recent):
                w = weights[idx] if idx < len(weights) else 0.01
                user_q = str((turn or {}).get("user_query", ""))
                phrase = self._extract_product_phrase(user_q)
                if not phrase:
                    continue
                scored[phrase] = scored.get(phrase, 0.0) + w
            # choose highest score; prefer longer phrase over single noun on tie
            if not scored:
                return ""
            best = sorted(scored.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)[0][0]
            return best
        except Exception:
            return ""

    def _recency_weighted_product_last3(self, convo_history: list[dict[str, str]]) -> str:
        try:
            weights = [0.2, 0.07, 0.03]
            scored: dict[str, float] = {}
            recent = list(reversed(convo_history[-3:]))
            recent = list(reversed(recent))
            for idx, turn in enumerate(recent):
                w = weights[idx] if idx < len(weights) else 0.01
                user_q = str((turn or {}).get("user_query", ""))
                phrase = self._extract_product_phrase(user_q)
                if not phrase:
                    continue
                scored[phrase] = scored.get(phrase, 0.0) + w
            if not scored:
                return ""
            best = sorted(scored.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)[0][0]
            return best
        except Exception:
            return ""

    def _extract_noun_from_texts(self, texts: list[str]) -> str:
        try:
            for t in texts:
                noun = self._extract_noun(t)
                if noun:
                    return noun
        except Exception:
            pass
        return ""

    async def plan_es_search(self, query: str, ctx: UserContext, *, product_intent: str) -> Dict[str, Any]:
        """Two-call pipeline: call 1. Returns {is_product_related, product_intent, ask_required, es_params?}."""
        prompt = (
            "You are the ES-PLANNER. In ONE tool call, build exactly what is needed to run Elasticsearch.\n"
            "STRICT OUTPUT: Return tool plan_es_search with fields: is_product_related, product_intent, ask_required, and es_params (when ask_required=false).\n\n"
            "DECISION TREE (MANDATORY):\n"
            "1) Determine is_product_related from current + recent turns (≤5):\n"
            "   - True only for serious product queries (discovery, recommendation, specific search, comparison).\n"
            "2) Decide product_intent ∈ {is_this_good, which_is_better, show_me_alternate, show_me_options}.\n"
            "3) Decide ask_required (boolean) under these hard rules:\n"
            "   - NEVER ask for is_this_good or which_is_better.\n"
            "   - For show_me_options/show_me_alternate: ask ONLY if BOTH budget AND dietary are UNKNOWN\n"
            "     across (session snapshot) OR (current user text constraints) OR (last_search_params).\n"
            "   - If ask_required=false → es_params MUST be provided.\n"
            "4) If ask_required=true, do NOT provide es_params (server will ask the two slots).\n"
            "5) If ask_required=false and product: construct es_params with DELTA logic from last_search_params:\n"
            "   - q: concise product noun phrase (strip price/currency). If current text is constraint-only (e.g., 'under 200'),\n"
            "     REUSE last noun phrase from recent turns/last_search_params and apply delta.\n"
            "   - category_group: MUST be exactly one of ['f_and_b','personal_care'] — never 'snacks' or other l2/l3.\n"
            "   - category_path(s): derive using the provided F&B taxonomy when applicable; include up to 3 full paths.\n"
            "   - price_min/price_max: parse INR ranges and apply delta if present (e.g., 'under 100' → price_max=100).\n"
            "   - dietary_terms/dietary_labels: UPPERCASE (e.g., 'GLUTEN FREE', 'PALM OIL FREE').\n"
            "   - brands, keywords, phrase_boosts/field_boosts: include only when explicit or strongly implied.\n"
            "   - size: suggest within [1,50] (server clamps).\n\n"
            "FOLLOW-UP BEHAVIOR (MANDATORY):\n"
            "- If an assessment is active, prefer the current assessment's original_query as the anchor.\n"
            "- If the current text is a generic modifier (e.g., 'under 100', 'gluten free'), DO NOT change the q noun phrase; only update constraints in es_params,\n"
            "  using assessment.original_query as the noun anchor when present.\n\n"
            "CATEGORY PATH CONSTRUCTION:\n"
            "- Use the provided F&B taxonomy to choose l2 and l3. Build full paths as 'f_and_b/food/<l2>' or 'f_and_b/food/<l2>/<l3>'.\n"
            "- For personal care, use 'personal_care/<l2>' or 'personal_care/<l2>/<l3>' as appropriate.\n\n"
            "CONSTRAINT REUSE POLICY:\n"
            "- Treat last_search_params and session slots as HINTS only.\n"
            "- If NOT a follow-up, DROP prior constraints unless explicitly present in CURRENT text or fresh slot answers.\n"
            "- If follow-up with modifier-only text, APPLY ONLY the delta; reuse other constraints ONLY if compatible with the same product noun/category (carry_over_constraints_hint=true).\n"
            "- NEVER carry brands into 'options/alternatives' turns unless re-mentioned.\n"
            "- Apply dietary constraints ONLY for f_and_b; DROP for other domains.\n\n"
            "Return ONLY tool plan_es_search.\n"
        )

        # Build compact context
        convo_pairs: List[Dict[str, Any]] = []
        try:
            convo = ctx.session.get("conversation_history", []) or []
            for h in convo[-5:]:
                if isinstance(h, dict):
                    convo_pairs.append({
                        "user_query": str(h.get("user_query", ""))[:120],
                        "bot_reply": str(h.get("bot_reply", ""))[:160],
                    })
        except Exception:
            pass

        # Build a compact, structured session snapshot for deterministic reasoning
        last_params = ((ctx.session.get("debug", {}) or {}).get("last_search_params", {}) or {}) if isinstance(ctx.session.get("debug"), dict) else {}
        assessment_block = ctx.session.get("assessment", {}) or {}
        session_snapshot = {
            "budget": ctx.session.get("budget"),
            "dietary_requirements": ctx.session.get("dietary_requirements"),
            "intent_l3": ctx.session.get("intent_l3"),
            "product_intent": ctx.session.get("product_intent"),
            "canonical_query": ctx.session.get("canonical_query"),
            "last_query": ctx.session.get("last_query"),
            "last_search_params": {k: last_params.get(k) for k in [
                "q", "category_group", "category_path", "category_paths",
                "brands", "dietary_terms", "price_min", "price_max", "keywords"
            ] if k in last_params}
        }
        # Include outstanding asks/answers context if available
        assessment = ctx.session.get("assessment", {}) or {}
        contextual_qs = ctx.session.get("contextual_questions", {}) or {}
        user_answers = (ctx.permanent or {}).get("user_answers") or ctx.session.get("user_answers")

        # Optionally include F&B taxonomy for precise category mapping
        try:
            from .recommendation import ElasticsearchRecommendationEngine
            _engine = ElasticsearchRecommendationEngine()
            fnb_taxonomy = getattr(_engine, "_fnb_taxonomy", {})
        except Exception:
            fnb_taxonomy = {}

        # Compute a simple carry-over hint for the planner (LLM decides reuse vs drop)
        try:
            text_lower = (query or "").lower()
            # Prefer current assessment original_query as the anchor during an active assessment
            anchor_q = str(assessment_block.get("original_query") or session_snapshot.get("canonical_query") or session_snapshot.get("last_query") or "").lower()
            nouns = ["ketchup", "chips", "juice", "soap", "shampoo", "bread", "milk", "biscuit", "cookie", "chocolate"]
            same_noun = any(n in text_lower and n in anchor_q for n in nouns)
            delta_markers = ["under", "over", "below", "above", "cheaper", "premium", "gluten", "vegan", "no palm", "baked", "fried"]
            has_delta = any(m in text_lower for m in delta_markers)
            carry_over_hint = bool(same_noun and has_delta)
        except Exception:
            carry_over_hint = False

        user_block = {
            "query": query.strip(),
            "product_intent": product_intent,
            "recent_turns": convo_pairs,
            "session": session_snapshot,
            # Include original_query so planner can favor the current assessment anchor
            "assessment": {k: assessment.get(k) for k in ["original_query", "missing_data", "priority_order", "currently_asking"]},
            "contextual_questions": {k: {
                "message": v.get("message"),
                "options": [o.get("label") if isinstance(o, dict) else str(o) for o in (v.get("options") or [])][:3]
            } for k, v in contextual_qs.items()} if isinstance(contextual_qs, dict) else {},
            "user_answers": user_answers if isinstance(user_answers, dict) else {},
            "fnb_taxonomy": fnb_taxonomy,
            "carry_over_constraints_hint": carry_over_hint,
        }

        # Generate unified ES params in ONE authoritative call
        try:
            unified_params = await self.generate_unified_es_params(ctx)
            if unified_params:
                # Store in session for immediate use
                ctx.session.setdefault("debug", {})["unified_es_params"] = unified_params
                ctx.session["canonical_query"] = unified_params.get("q", query)
                ctx.session["last_query"] = unified_params.get("q", query)
                log.info(f"UNIFIED_ES_PARAMS | q='{unified_params.get('q')}' | dietary={unified_params.get('dietary_terms')} | category={unified_params.get('category_group')}")
                # Return plan with unified params
                return {
                    "is_product_related": True,
                    "product_intent": product_intent,
                    "ask_required": False,
                    "es_params": unified_params
                }
        except Exception as exc:
            log.warning(f"UNIFIED_ES_PARAMS_FAILED | {exc}")
        
        # Fallback to old path if unified fails
        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt + "\n" + json.dumps(user_block, ensure_ascii=False)}],
            tools=[PLAN_ES_SEARCH_TOOL],
            tool_choice={"type": "tool", "name": "plan_es_search"},
            temperature=0,
            max_tokens=600,
        )
        tool_use = pick_tool(resp, "plan_es_search")
        return tool_use.input if tool_use else {}

    async def generate_final_answer_unified(
        self,
        query: str,
        ctx: UserContext,
        *,
        product_intent: str,
        products_for_llm: List[Dict[str, Any]],
        top_products_brief: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Two-call pipeline: call 2. Unified product answer + UX."""
        prompt = (
            "Generate the final product answer and UX in ONE tool call.\n"
            "Inputs: user_query, product_intent, session snapshot (concise), ES results (top 10), enriched briefs (top 1 for SPM; top 3 for MPM).\n"
            "Output: {response_type:'final_answer', summary_message(4 lines), product_ids(ordered; hero optional), ux:{ux_surface, dpl_runtime_text, quick_replies(3-4)}}.\n"
            "Rules:\n- SPM → clamp to 1 item and include product_ids[1].\n- MPM → choose a hero (healthiest/cleanest) and order product_ids with hero first.\n- Quick replies should be short, actionable pivots (budget/dietary/quality).\n"
        )

        session_snapshot = {
            k: ctx.session.get(k) for k in [
                "budget", "dietary_requirements", "intent_l3", "product_intent"
            ] if k in ctx.session
        }
        payload = {
            "user_query": query.strip(),
            "product_intent": product_intent,
            "session": session_snapshot,
            "products": products_for_llm,
            "briefs": top_products_brief,
        }
        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt + "\n" + json.dumps(payload, ensure_ascii=False)}],
            tools=[FINAL_ANSWER_UNIFIED_TOOL],
            tool_choice={"type": "tool", "name": "generate_final_answer_unified"},
            temperature=0.7,
            max_tokens=900,
        )
        tool_use = pick_tool(resp, "generate_final_answer_unified")
        return _strip_keys(tool_use.input or {}) if tool_use else {}

    async def classify_and_assess(self, query: str, ctx: Optional[UserContext] = None) -> Dict[str, Any]:
        """Combined classifier for latency reduction (flag-gated upstream).

        Returns a dict with keys:
        - is_product_related: bool
        - layer3: str
        - product_intent: Optional[str]
        - ask: Optional[Dict[str, Dict[str, Any]]] (up to 2 slots)
        - fetch_functions: Optional[List[str]]
        - simple_response: Optional[Dict[str, str]] when general
        """
        # Build a compact context to keep token use low
        recent_context: Dict[str, Any] = {}
        convo_pairs: List[Dict[str, Any]] = []
        try:
            if ctx:
                history = ctx.session.get("history", [])
                if history:
                    last = history[-1]
                    recent_context = {
                        "last_intent_l3": last.get("intent"),
                        "last_slots": {k: v for k, v in (last.get("slots") or {}).items() if v},
                    }
                convo = ctx.session.get("conversation_history", []) or []
                if isinstance(convo, list) and convo:
                    for h in convo[-10:]:
                        if isinstance(h, dict):
                            convo_pairs.append({
                                "user_query": str(h.get("user_query", ""))[:120],
                                "bot_reply": str(h.get("bot_reply", ""))[:160],
                            })
        except Exception:
            recent_context = {}

        prompt = (
            "You are an e-commerce assistant. In ONE tool call, do all three tasks.\n"
            "Task 1: Classify the user's latest message into L3 and set is_product_related (true only for serious product queries).\n"
            "Task 2: If product-related, classify one of 4 intents: is_this_good | which_is_better | show_me_alternate | show_me_options.\n"
            "Task 3: If product-related, emit EXACTLY TWO ASK_* questions with EXACTLY 3 options each (short, concrete),\n"
            "         optimized to refine Elasticsearch filters (prefer budget + dietary); and set fetch_functions=['search_products'].\n"
            "FOLLOW-UP GUIDANCE: Decide is_follow_up by comparing the current message to roughly the last 10 interactions, giving much more weight to the most recent turns.\n"
            "- Think in terms of a decaying emphasis (e.g., recent ≫ older) rather than strict weights.\n"
            "- If CURRENT message feels like a modifier-only turn (price/dietary/quality or a short ingredient/flavor), lean towards follow-up (is_follow_up=true).\n"
            "- If CURRENT introduces a clear new product noun/category that diverges from the recent anchor, lean towards is_follow_up=false. Use judgment.\n"
            "ASK DESIGN (MANDATORY):\n"
            "- Infer likely category/subcategory from the latest query + recent turns; tailor ASK_* messages and options to that subcategory.\n"
            "- One of the two asks MUST be ASK_USER_BUDGET. Budget options MUST be in INR (use the '₹' symbol) and calibrated to the subcategory's typical price bands.\n"
            "  Examples:\n"
            "   • chips_and_crisps → ['Under ₹50','₹50–150','Over ₹150']\n"
            "   • sauces_condiments → ['Under ₹100','₹100–300','Over ₹300']\n"
            "   • noodles_pasta → ['Under ₹50','₹50–150','Over ₹150']\n"
            "   • shampoo (personal care) → ['Under ₹99','₹99–299','Over ₹299']\n"
            "- If subcategory is unclear, use labeled bands: ['Budget friendly','Smart choice','Premium'].\n"
            "- For the second ASK_* choose the most useful for the subcategory (dietary for F&B; preferences/brand/use_case otherwise) and ensure options are subcategory-appropriate and concise; include 'No preference' when applicable.\n"
            "ALLOWED ASK_* SLOTS (choose only from this list):\n"
            "- ASK_USER_BUDGET\n- ASK_DIETARY_REQUIREMENTS\n- ASK_USER_PREFERENCES\n- ASK_USE_CASE\n- ASK_QUANTITY\n- ASK_DELIVERY_ADDRESS\n"
            "STRICT: The ask object MUST use keys only from the allowed list. If unsure, pick budget and dietary.\n"
            "If NOT product-related, return simple_response {response_type:'final_answer', message}.\n"
            "Return ONLY the tool call to classify_and_assess.\n\n"
            f"RECENT_CONTEXT: {json.dumps(recent_context, ensure_ascii=False)}\n"
            f"RECENT_TURNS (last up to 10): {json.dumps(convo_pairs, ensure_ascii=False)}\n"
            f"QUERY: {query.strip()}\n"
        )

        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[COMBINED_CLASSIFY_ASSESS_TOOL],
            tool_choice={"type": "tool", "name": "classify_and_assess"},
            temperature=0.2,
            max_tokens=500,
        )

        tool_use = pick_tool(resp, "classify_and_assess")
        if not tool_use:
            return {}
        data = tool_use.input or {}

        # Light normalization of ask payload
        try:
            ask = data.get("ask") or {}
            if isinstance(ask, dict):
                # Canonical mapping for slot keys (food & personal care synonyms)
                def _canon_slot(name: str) -> Optional[str]:
                    if not isinstance(name, str):
                        return None
                    key = name.strip().upper()
                    allowed = {
                        "ASK_USER_BUDGET",
                        "ASK_DIETARY_REQUIREMENTS",
                        "ASK_USER_PREFERENCES",
                        "ASK_USE_CASE",
                        "ASK_QUANTITY",
                        "ASK_DELIVERY_ADDRESS",
                    }
                    if key in allowed:
                        return key
                    # Synonyms
                    if any(t in key for t in ["PRICE", "BUDGET", "UNDER", "OVER", "LESS THAN", "MORE THAN"]):
                        return "ASK_USER_BUDGET"
                    if any(t in key for t in ["DIET", "DIETARY", "PALM", "VEGAN", "GLUTEN", "SUGAR", "OIL"]):
                        return "ASK_DIETARY_REQUIREMENTS"
                    if any(t in key for t in ["SPICE", "SPICY", "FLAVOR", "TASTE", "BRAND", "PREFERENCE", "SKIN", "FRAGRANCE"]):
                        return "ASK_USER_PREFERENCES"
                    if any(t in key for t in ["USE", "OCCASION", "WHEN"]):
                        return "ASK_USE_CASE"
                    if any(t in key for t in ["QTY", "QUANTITY", "COUNT", "PACK"]):
                        return "ASK_QUANTITY"
                    if any(t in key for t in ["ADDRESS", "PIN", "PINCODE", "DELIVERY"]):
                        return "ASK_DELIVERY_ADDRESS"
                    return None

                # Rebuild ask with canonical keys only
                canon_ask: Dict[str, Dict[str, Any]] = {}
                for k, v in list(ask.items()):
                    if not isinstance(v, dict):
                        continue
                    ckey = _canon_slot(k)
                    if not ckey:
                        continue
                    opts = v.get("options") or []
                    # Clamp/pad options to exactly 3 strings
                    norm_opts: List[str] = []
                    for opt in opts[:3]:
                        if isinstance(opt, str) and opt.strip():
                            norm_opts.append(opt.strip())
                    # Pad according to slot semantics
                    if ckey == "ASK_USER_BUDGET":
                        fillers = ["Budget friendly", "Smart choice", "Premium"]
                        while len(norm_opts) < 3 and fillers:
                            norm_opts.append(fillers.pop(0))
                    else:
                        while len(norm_opts) < 3:
                            norm_opts.append("No preference")
                    canon_ask[ckey] = {
                        "message": v.get("message") or f"What's your {ckey.replace('ASK_', '').replace('_', ' ').lower()}?",
                        "options": norm_opts[:3],
                    }
                # Deduplicate and clamp to 2; fallback if empty
                if not canon_ask:
                    canon_ask = {
                        "ASK_USER_BUDGET": {
                            "message": "What's your budget range? (in ₹)",
                            "options": ["Under ₹100", "₹100–300", "Over ₹300"],
                        },
                        "ASK_DIETARY_REQUIREMENTS": {
                            "message": "Any dietary requirements?",
                            "options": ["GLUTEN FREE", "LOW SODIUM", "No preference"],
                        },
                    }
                else:
                    # Keep insertion order; take first two
                    canon_ask = {k: canon_ask[k] for k in list(canon_ask.keys())[:2]}
                data["ask"] = canon_ask
        except Exception:
            pass

        return data

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
            result = await self._generate_product_response(query, ctx, fetched, intent_l3, product_intent)
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
        intent_l3: str,
        product_intent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate structured product+UX response using a single LLM call."""
        products_data = []
        if 'search_products' in fetched:
            search_data = fetched['search_products']
            if isinstance(search_data, dict):
                data = search_data.get('data', search_data)
                # For SPM (is_this_good) we only need one product end-to-end
                if product_intent and product_intent == "is_this_good":
                    products_data = data.get('products', [])[:1]
                else:
                    products_data = data.get('products', [])[:10]
        
        if not products_data:
            return {
                "response_type": "final_answer",
                "summary_message": "I couldn't find any products matching your search. Please try different keywords.",
                "products": []
            }
        
        # Suppressed: verbose top-3 preview logging

        # Build enrichment: fetch full docs for top-3 (or top-1 for SPM) via ES mget
        top_k = 1 if (product_intent and product_intent == "is_this_good") else 3
        top_ids: List[str] = []
        try:
            for p in products_data[:top_k]:
                pid = str(p.get("id") or "").strip()
                if pid:
                    top_ids.append(pid)
        except Exception:
            top_ids = []

        top_products_brief: List[Dict[str, Any]] = []
        if top_ids:
            try:
                try:
                    log.info(f"UX_ENRICH_START | top_ids_count={len(top_ids)} | ids_sample={top_ids[:3]}")
                except Exception:
                    pass
                from .data_fetchers.es_products import get_es_fetcher  # type: ignore
                loop = asyncio.get_running_loop()
                log.info("UX_ENRICH_FETCHER_GET")
                fetcher = get_es_fetcher()
                log.info("UX_ENRICH_BEFORE_MGET")
                full_docs = await loop.run_in_executor(None, lambda: fetcher.mget_products(top_ids))
                log.info(f"UX_ENRICH_AFTER_MGET | full_docs_count={len(full_docs) if isinstance(full_docs, list) else 'N/A'}")
                # Suppressed: verbose doc content logging
                # Build compact briefs for LLM (avoid token bloat) with rich attributes
                for doc in full_docs[:top_k]:
                    try:
                        stats = doc.get("stats", {}) or {}
                        def _pp(key: str) -> Optional[float]:
                            try:
                                return (stats.get(key, {}) or {}).get("subcategory_percentile")
                            except Exception:
                                return None
                        # Extract rich attributes
                        pkg = (doc.get("package_claims", {}) or {})
                        cat = (doc.get("category_data", {}) or {})
                        nutr = (cat.get("nutritional", {}) or {}).get("nutri_breakdown", {})
                        tags = (doc.get("tags_and_sentiments", {}) or {})
                        ingredients = (doc.get("ingredients", {}) or {})
                        flean = (doc.get("flean_score", {}) or {})
                        brief = {
                            "id": doc.get("id"),
                            "name": doc.get("name"),
                            "brand": doc.get("brand"),
                            "price": doc.get("price"),
                            "description": doc.get("description"),
                            "dietary_labels": pkg.get("dietary_labels", []),
                            "health_claims": pkg.get("health_claims", []),
                            "package_text": pkg.get("text"),
                            "nutri": nutr,
                            "processing_type": cat.get("processing_type"),
                            "dietary_label": cat.get("dietary_label"),
                            "ingredients_raw": ingredients.get("raw_text") or ingredients.get("raw_text_new"),
                            "tags_and_sentiments": {
                                "usage_tags": ((tags.get("tags", {}) or {}).get("usage_tags", []) if isinstance(tags.get("tags"), dict) else []),
                                "occasion_tags": ((tags.get("tags", {}) or {}).get("occasion_tags", []) if isinstance(tags.get("tags"), dict) else []),
                                "time_of_day_tags": ((tags.get("tags", {}) or {}).get("time_of_day_tags", []) if isinstance(tags.get("tags"), dict) else []),
                                "social_context_tags": ((tags.get("tags", {}) or {}).get("social_context_tags", []) if isinstance(tags.get("tags"), dict) else []),
                                "weather_season_tags": ((tags.get("tags", {}) or {}).get("weather_season_tags", []) if isinstance(tags.get("tags"), dict) else []),
                                "emotional_trigger_tags": ((tags.get("tags", {}) or {}).get("emotional_trigger_tags", []) if isinstance(tags.get("tags"), dict) else []),
                                "health_positioning_tags": ((tags.get("tags", {}) or {}).get("health_positioning_tags", []) if isinstance(tags.get("tags"), dict) else []),
                                "processing_level_tags": ((tags.get("tags", {}) or {}).get("processing_level_tags", []) if isinstance(tags.get("tags"), dict) else []),
                                "marketing_tags": ((tags.get("tags", {}) or {}).get("marketing_tags", []) if isinstance(tags.get("tags"), dict) else []),
                            },
                            "flean_score": flean.get("adjusted_score"),
                            "bonuses": flean.get("bonuses"),
                            "penalties": flean.get("penalties"),
                            "percentiles": {
                                "flean": _pp("adjusted_score_percentiles"),
                                "protein": _pp("protein_percentiles"),
                                "fiber": _pp("fiber_percentiles"),
                                "wholefood": _pp("wholefood_percentiles"),
                                "fortification": _pp("fortification_percentiles"),
                                "simplicity": _pp("simplicity_percentiles"),
                                "sugar_penalty": _pp("sugar_penalty_percentiles"),
                                "sodium_penalty": _pp("sodium_penalty_percentiles"),
                                "trans_fat_penalty": _pp("trans_fat_penalty_percentiles"),
                                "saturated_fat_penalty": _pp("saturated_fat_penalty_percentiles"),
                                "oil_penalty": _pp("oil_penalty_percentiles"),
                                "sweetener_penalty": _pp("sweetener_penalty_percentiles"),
                                "calories_penalty": _pp("calories_penalty_percentiles"),
                                "empty_food_penalty": _pp("empty_food_penalty_percentiles"),
                            },
                        }
                        top_products_brief.append(brief)
                        try:
                            log.info(
                                f"UX_ENRICHMENT_BRIEF | id={brief.get('id')} | has_nutri={bool(brief.get('nutri'))} | tags_keys={list((brief.get('tags_and_sentiments') or {}).keys())} | bonuses={bool(brief.get('bonuses'))} | penalties={bool(brief.get('penalties'))}"
                            )
                        except Exception:
                            pass
                    except Exception:
                        continue
            except Exception:
                top_products_brief = []
                log.error("UX_ENRICH_ERROR | failed during enrichment (import/fetch/mget/brief-build)", exc_info=True)

        # Narrow LLM input: prefer small top-K for SPM to enable brand-aware selection later
        spm_mode = bool(product_intent and product_intent == "is_this_good")
        products_for_llm = products_data[:5] if spm_mode else products_data[:10]

        # Unified product + UX prompt and tool
        try:
            try:
                log.info(
                    f"UX_ENRICHMENT_COUNTS | top_ids={len(top_ids)} | briefs={len(top_products_brief)} | products_for_llm={len(products_for_llm)}"
                )
            except Exception:
                pass

            unified_context = {
                "user_query": query,
                "intent_l3": intent_l3,
                "product_intent": product_intent or ctx.session.get("product_intent") or "show_me_options",
                "session": {k: ctx.session.get(k) for k in ["budget", "dietary_requirements", "preferences"] if k in ctx.session},
                "products": products_for_llm,
                "enriched_top": top_products_brief,
            }
            unified_prompt = (
                "You are producing BOTH the product answer and the UX block in a SINGLE tool call.\n"
                "Inputs:\n- user_query\n- intent_l3\n- product_intent (one of is_this_good, which_is_better, show_me_alternate, show_me_options)\n- session snapshot (budget, dietary, preferences)\n- products (top 5-10)\n- enriched_top (top 1 for SPM; top 3 for MPM)\n\n"
                "Output JSON (tool generate_final_answer_unified):\n"
                "{response_type:'final_answer', summary_message, product_ids?, hero_product_id?, ux:{ux_surface, dpl_runtime_text, quick_replies(3-4)}}\n\n"
                "Rules (MANDATORY):\n"
                "- For is_this_good (SPM): choose 1 best item → ux_surface='SPM'; product_ids=[that_id]; dpl_runtime_text should read like a concise expert verdict; keep summary to 4 lines with evidence.\n"
                "- For others (MPM): choose a hero (healthiest/cleanest using enriched_top), set hero_product_id and order product_ids with hero first; ux_surface='MPM'.\n"
                "- Quick replies: short and actionable pivots (budget ranges like 'Under ₹100', dietary like 'GLUTEN FREE', or quality pivots).\n"
                "- Evidence: use flean score/percentiles, nutrition grams, and penalties correctly (penalties high = bad).\n"
                "- Keep summary_message EXACTLY 4 sentences, evidence-based; avoid fluff.\n"
                "Return ONLY the tool call.\n"
            )

            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": unified_prompt + "\n" + json.dumps(unified_context, ensure_ascii=False)}],
                tools=[FINAL_ANSWER_UNIFIED_TOOL],
                tool_choice={"type": "tool", "name": "generate_final_answer_unified"},
                temperature=0.7,
                max_tokens=900,
            )

            tool_use = pick_tool(resp, "generate_final_answer_unified")
            if not tool_use:
                # Fallback to old two-step product response path
                return self._create_fallback_product_response(products_data, query)

            result = _strip_keys(tool_use.input or {})

            # Enforce exactly one product for SPM in final result
            if spm_mode:
                one = []
                if isinstance(result.get("products"), list) and result["products"]:
                    one = [result["products"][0]]
                # If LLM didn't return products, synthesize minimal from ES top-1 (text-only)
                # Brand-aware selection among top-K using session brand hints
                chosen_index = 0
                try:
                    brand_hints = []
                    try:
                        dbg = (ctx.session.get('debug', {}) or {})
                        last_params = dbg.get('last_search_params', {}) or {}
                        bval = last_params.get('brands')
                        if isinstance(bval, list):
                            brand_hints = [str(x).strip().lower() for x in bval if str(x).strip()]
                        elif isinstance(bval, str) and bval.strip():
                            brand_hints = [bval.strip().lower()]
                    except Exception:
                        brand_hints = []
                    if brand_hints and isinstance(products_data, list):
                        for idx, cand in enumerate(products_data[:5]):
                            try:
                                cbrand = str(cand.get('brand') or '').strip().lower()
                                if cbrand and any(h in cbrand for h in brand_hints):
                                    chosen_index = idx
                                    break
                            except Exception:
                                continue
                except Exception:
                    chosen_index = 0

                if not one and products_data:
                    p = products_data[chosen_index]
                    one = [{
                        "id": str(p.get("id") or "").strip(),
                        "text": p.get("name", "Product"),
                        "description": f"Solid choice at ₹{p.get('price','N/A')}",
                        "price": f"₹{p.get('price','N/A')}",
                        "special_features": ""
                    }]
                result["products"] = one
                # Ensure SPM carries a real ES product_id and propagate it consistently
                spm_id: str = ""
                try:
                    if products_data and isinstance(products_data[chosen_index], dict):
                        spm_id = str(products_data[chosen_index].get("id") or "").strip()
                    if not spm_id and isinstance(top_products_brief, list) and top_products_brief:
                        spm_id = str(top_products_brief[0].get("id") or "").strip()
                except Exception:
                    spm_id = ""
                if spm_id:
                    result["product_ids"] = [spm_id]
                    try:
                        if isinstance(result.get("products"), list) and result["products"]:
                            if not result["products"][0].get("id"):
                                result["products"][0]["id"] = spm_id
                    except Exception:
                        pass
            
            # Ensure product_ids exist and hero-first ordering
            try:
                # If tool returned product_ids, validate/order with hero
                if isinstance(result.get("product_ids"), list) and result["product_ids"]:
                    hero_id = str(result.get("hero_product_id", "")).strip()
                    ids = [str(x) for x in result["product_ids"] if str(x).strip()]
                    if hero_id and hero_id in ids:
                        ids.remove(hero_id)
                        ids = [hero_id] + ids
                    # Replace with ES-backed ids if any missing
                    es_ids = [str(p.get("id")) for p in products_data if p.get("id")]
                    mapped = []
                    for x in ids:
                        mapped.append(x if x in es_ids else (es_ids[0] if es_ids else x))
                    # Dedup and clamp
                    seen = set()
                    final_ids: List[str] = []
                    for x in mapped:
                        if x not in seen:
                            seen.add(x)
                            final_ids.append(x)
                    result["product_ids"] = final_ids[:10]
                else:
                    # Build from ES results
                    result["product_ids"] = [str(p.get("id")) for p in products_data if p.get("id")] [:10]
            except Exception:
                pass

            # SPM clamp and ensure single id
            if spm_mode:
                result["product_ids"] = result.get("product_ids", [])[:1]
                # Drop products list to reduce payload; FE uses product_ids
                if "products" in result:
                    result.pop("products", None)

            # Attach ux_response in top-level expected key and ensure product_ids are included for FE
            if isinstance(result.get("ux"), dict):
                result["ux_response"] = result.pop("ux")
            try:
                if isinstance(result.get("ux_response"), dict):
                    ux_pid = result["ux_response"].get("product_ids") if isinstance(result["ux_response"].get("product_ids"), list) else None
                    final_ids = result.get("product_ids") if isinstance(result.get("product_ids"), list) else []
                    if (not ux_pid) or (isinstance(ux_pid, list) and len(ux_pid) == 0):
                        result["ux_response"]["product_ids"] = final_ids
            except Exception:
                pass

            # Belt-and-suspenders: if product_ids still missing but ES fetched has products, backfill
            try:
                if (not result.get("product_ids")) and isinstance(products_data, list) and products_data:
                    backfill_ids: List[str] = []
                    for p in products_data[:10]:
                        pid = p.get("id") or f"prod_{hash(p.get('name','') or p.get('title',''))%1000000}"
                        sid = str(pid)
                        if sid and sid not in backfill_ids:
                            backfill_ids.append(sid)
                    if backfill_ids:
                        result["product_ids"] = backfill_ids
            except Exception:
                pass
            
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
        """Assess what needs to be fetched for a follow-up (robust, context-rich)."""
        # Build rich context for reliable decisions
        session = ctx.session or {}
        debug_block = (session.get("debug", {}) or {})
        last_params = debug_block.get("last_search_params", {}) or {}
        canonical_query = str(session.get("canonical_query") or last_params.get("q") or "").strip()
        current_text = str(getattr(ctx, "current_user_text", "") or session.get("current_user_text") or "").strip()
        # Detected signals from current text if present
        detected_signals = {}
        try:
            from .recommendation import ElasticsearchRecommendationEngine  # type: ignore
            _tmp_engine = ElasticsearchRecommendationEngine()
            detected_signals = await _tmp_engine._extract_constraints_from_text(current_text)
        except Exception:
            detected_signals = {}

        prompt = DELTA_ASSESS_PROMPT.format(
            intent_l3=session.get("intent_l3"),
            current_text=current_text,
            canonical_query=canonical_query,
            last_params=json.dumps({k: last_params.get(k) for k in ["q", "category_group", "category_path", "price_min", "price_max", "brands", "dietary_terms", "keywords", "phrase_boosts"] if k in last_params}, ensure_ascii=False),
            fetched_keys=list(ctx.fetched_data.keys()),
            patch=json.dumps(patch.__dict__, ensure_ascii=False),
            detected_signals=json.dumps(detected_signals, ensure_ascii=False),
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
            items_raw = tool_use.input.get("fetch_functions", []) if tool_use else []
            out: List[BackendFunction] = []
            for it in items_raw:
                try:
                    out.append(BackendFunction(it.strip() if isinstance(it, str) else it))
                except ValueError:
                    pass
            # Fallback safety: if no fetchers but current text changes constraints vs last_params, fetch search_products
            try:
                should_fetch = False
                if not out:
                    # Compare key fields for potential delta
                    lp = last_params or {}
                    # Basic signals
                    if current_text and canonical_query:
                        # if new method/quality/occasion words present
                        low = current_text.lower()
                        delta_markers = ["baked", "fried", "premium", "gift", "gifting", "healthier", "cleaner", "vegan", "gluten", "brand"]
                        if any(m in low for m in delta_markers):
                            should_fetch = True
                    # If detected signals present
                    if isinstance(detected_signals, dict) and any(k in detected_signals for k in ["price_min", "price_max", "dietary_terms", "dietary_labels", "brands", "keywords"]):
                        should_fetch = True
                if should_fetch:
                    out.append(BackendFunction.SEARCH_PRODUCTS)
            except Exception:
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
            slots_needed = []

        # ── Derive domain and last ES params from session for gating/normalization
        session_debug = (ctx.session or {}).get("debug", {}) or {}
        last_params = session_debug.get("last_search_params", {}) or {}
        category_group = (last_params.get("category_group") or ctx.session.get("category_group") or "").strip()
        has_cat_path = bool(last_params.get("category_path"))
        has_price = (last_params.get("price_min") is not None) or (last_params.get("price_max") is not None)

        # Gate out slots we already know from ES params/classification
        filtered_slots: List[UserSlot] = []
        product_intents_l3 = {"Product_Discovery", "Recommendation", "Specific_Product_Search", "Product_Comparison"}
        for slot in slots_needed:
            if slot == UserSlot.PRODUCT_CATEGORY and (category_group or has_cat_path):
                continue
            # Also skip category ask for product-related intents; taxonomy classifier will infer it
            if slot == UserSlot.PRODUCT_CATEGORY and intent_l3 in product_intents_l3:
                continue
            if slot == UserSlot.USER_BUDGET and has_price:
                continue
            filtered_slots.append(slot)
        # LLM-driven slot selection to ensure relevance (max 3)
        if intent_l3 in product_intents_l3:
            dynamic_slots = await self._select_slots_to_ask(
                query=query,
                intent_l3=intent_l3,
                domain=category_group or "",
                last_params=last_params
            )
            # Merge with filtered and keep order preference: dynamic first
            merged: List[UserSlot] = []
            # map strings to enum safely
            def _to_slot(s: str) -> Optional[UserSlot]:
                try:
                    return UserSlot(s)
                except Exception:
                    return None
            for s in (dynamic_slots or []):
                sl = _to_slot(s)
                if sl and sl not in merged:
                    merged.append(sl)
            for sl in filtered_slots:
                if sl not in merged:
                    merged.append(sl)
            # Apply gating again
            final_list: List[UserSlot] = []
            for sl in merged:
                if sl == UserSlot.PRODUCT_CATEGORY:
                    continue
                if sl == UserSlot.USER_BUDGET and has_price:
                    continue
                final_list.append(sl)
            filtered_slots = final_list[:3]
        # If still nothing to ask but this is a product flow, ask smart defaults to aid ES
        if not filtered_slots and intent_l3 in product_intents_l3:
            filtered_slots = [UserSlot.USER_BUDGET, UserSlot.DIETARY_REQUIREMENTS, UserSlot.USER_PREFERENCES]
        if not filtered_slots:
            return {}

        # Determine domain for category-specific hints
        domain = category_group if category_group in ("f_and_b", "personal_care") else "general"
        product_category = domain  # reuse existing variable name used in hints

        # Build slot hint lines from intent_config (if any)
        slot_hints_lines = []
        for slot in filtered_slots:
            hint_config = SLOT_QUESTIONS.get(slot, {})
            if "hint" in hint_config:
                slot_hints_lines.append(f"- {slot.value}: {hint_config['hint']}")
        slot_hints = "\n".join(slot_hints_lines) if slot_hints_lines else "Use domain-specific ranges/options."

        # Use category-domain hints and INR budget ranges
        domain_hints = CATEGORY_QUESTION_HINTS.get(product_category, CATEGORY_QUESTION_HINTS.get("general", {}))
        slots_needed_desc = ", ".join([slot.value for slot in filtered_slots])

        # Stronger instructions for INR/currency and relevance
        prompt = (
            CONTEXTUAL_QUESTIONS_PROMPT
            + "\nPersona & goal:\n"
            + "- You are an advanced shopping buddy. For f_and_b, think like a friendly dietician; for personal_care, like a dermatologist friend.\n"
            + "- Ask only what is necessary to refine search and improve ES filters.\n"
            + "\nStrict rules:\n"
            + "- Currency MUST be INR with symbol '₹'. Never use '$' or other currencies.\n"
            + "- For budget questions, choose ONLY from these INR buckets: "
            + ", ".join(domain_hints.get("budget_ranges", []))
            + ".\n- Questions must directly aid Elasticsearch filtering (budget, dietary labels/health claims like 'NO PALM OIL', brand preference as a multiple-choice concept), avoid asking category if already known.\n"
            + "- Options must be 1-4 words, discrete, non-overlapping.\n"
            + "\nContext examples:\n"
            + "- Query: 'spicy chips' → Ask budget (₹ ranges), oil preference (No palm oil), heat level.\n"
            + "- Query: 'face wash for oily skin' → Ask skin type, fragrance preference, budget (₹ ranges).\n"
        ).format(
            query=query,
            intent_l3=intent_l3,
            product_category=product_category,
            slot_hints=slot_hints,
            category_hints=json.dumps(domain_hints, ensure_ascii=False),
            slots_needed=slots_needed_desc,
        )

        try:
            questions_tool = build_questions_tool(filtered_slots)
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[questions_tool],
                tool_choice={"type": "tool", "name": "generate_questions"},
                temperature=0.2,
                max_tokens=800,
            )

            tool_use = pick_tool(resp, "generate_questions")
            if not tool_use:
                return {}

            questions_data = _strip_keys(tool_use.input.get("questions", {}) or {})

            # ── Post-process to enforce INR and domain-allowed ranges/options
            def _coerce_budget_options_genz() -> List[Dict[str, str]]:
                # Fixed, non-variable Gen-Z style budget options
                return [
                    {"label": "Budget-friendly", "value": "Budget-friendly"},
                    {"label": "Smart value", "value": "Smart value"},
                    {"label": "Premium", "value": "Premium"},
                ]

            processed_questions: Dict[str, Dict[str, Any]] = {}
            for slot_value, question_data in questions_data.items():
                options = question_data.get("options", [])

                formatted_options: List[Dict[str, str]] = []
                # Coerce options to dicts {label,value} and normalize currency to INR
                for opt in options[:3]:
                    if isinstance(opt, str):
                        label = opt.strip()
                        # Replace $ with ₹ and normalize common patterns
                        label = label.replace("$", "₹")
                        formatted_options.append({"label": label, "value": label})
                    elif isinstance(opt, dict) and "label" in opt and "value" in opt:
                        label = str(opt["label"]).strip().replace("$", "₹")
                        value = str(opt["value"]).strip().replace("$", "₹")
                        formatted_options.append({"label": label, "value": value})

                # Pad or clamp to 3 options
                while len(formatted_options) < 3:
                    formatted_options.append({"label": "Other", "value": "Other"})
                formatted_options = formatted_options[:3]

                # Budget-specific override: always use fixed Gen-Z style buckets
                if slot_value == UserSlot.USER_BUDGET.value:
                    formatted_options = _coerce_budget_options_genz()

                processed_questions[slot_value] = {
                    "message": question_data.get(
                        "message",
                        f"What's your {slot_value.lower().replace('_', ' ')}?",
                    ),
                    "type": "multi_choice",
                    "options": formatted_options,
                }

            return processed_questions

        except Exception as exc:
            log.warning("Question generation failed: %s", exc)
            return {}

    async def _select_slots_to_ask(
        self,
        *,
        query: str,
        intent_l3: str,
        domain: str,
        last_params: Dict[str, Any],
    ) -> List[str]:
        """Use LLM to pick up to 3 ES-relevant user slots to ask next."""
        # Build guidance: what we already know
        known = {
            "category_group": domain,
            "has_category_path": bool(last_params.get("category_path")),
            "has_price": (last_params.get("price_min") is not None) or (last_params.get("price_max") is not None),
            "dietary_terms": last_params.get("dietary_terms") or last_params.get("dietary_labels") or [],
        }
        prompt = (
            "Select up to 3 user slots to ask next for a shopping query.\n\n"
            f"QUERY: {query}\n"
            f"INTENT_L3: {intent_l3}\n"
            f"DOMAIN: {domain or 'unknown'}\n"
            f"KNOWN: {json.dumps(known, ensure_ascii=False)}\n\n"
            "Allowed slots (choose any up to 3):\n"
            "- ASK_USER_BUDGET (₹ ranges)\n"
            "- ASK_DIETARY_REQUIREMENTS (e.g., NO PALM OIL, VEGAN, GLUTEN FREE)\n"
            "- ASK_USER_PREFERENCES (e.g., taste/brand/features)\n"
            "- ASK_USE_CASE (e.g., party, daily use)\n"
            "- ASK_QUANTITY\n\n"
            "Rules:\n"
            "- Do NOT include ASK_PRODUCT_CATEGORY (category is inferred).\n"
            "- Prefer budget and dietary for FOOD queries; skin-type/preferences for PERSONAL_CARE.\n"
            "- Avoid slots already known (e.g., price range exists).\n"
            "Return ONLY a tool call to select_slots_to_ask."
        )
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[SLOT_SELECTION_TOOL],
                tool_choice={"type": "tool", "name": "select_slots_to_ask"},
                temperature=0,
                max_tokens=200,
            )
            tool_use = pick_tool(resp, "select_slots_to_ask")
            if not tool_use:
                return []
            data = _strip_keys(tool_use.input or {})
            slots = data.get("slots", [])
            # sanitize
            out: List[str] = []
            for s in slots:
                if isinstance(s, str) and s.strip() and s.strip() != UserSlot.PRODUCT_CATEGORY.value:
                    out.append(s.strip())
            return out[:3]
        except Exception as exc:
            log.debug(f"SLOT_SELECTION_FAILED | {exc}")
            return []

    async def extract_es_params(self, ctx: UserContext) -> Dict[str, Any]:
        """Extract ES parameters via recommendation service."""
        try:
            params = await self._recommendation_service.extract_es_params(ctx)
            log.debug(f"ES params extracted: {params}")
            return params
        except Exception as exc:
            log.warning("ES param extraction failed: %s", exc)
            return {}

    async def generate_unified_es_params(self, ctx: UserContext) -> Dict[str, Any]:
        """Generate ALL ES parameters in one authoritative LLM call with complete context."""
        try:
            session = ctx.session or {}
            assessment = session.get("assessment", {}) or {}
            ask_only_mode = bool(getattr(Cfg, "USE_ASSESSMENT_FOR_ASK_ONLY", False))

            current_text = str(getattr(ctx, "current_user_text", "") or session.get("current_user_text") or session.get("last_user_message") or "").strip()

            # Follow-up detection (Redis-driven; assessment state ignored if ASK_ONLY_MODE)
            if ask_only_mode:
                is_follow_up = self._is_follow_up_from_redis(ctx)
            else:
                is_follow_up = bool(assessment)
            try:
                print(f"CORE:FOLLOWUP_DECIDE | ask_only={ask_only_mode} | follow_up={is_follow_up}")
            except Exception:
                pass

            # Interactions: use up to 10 for follow-up, 5 otherwise
            hist_limit = 10 if is_follow_up else 5
            convo_history = self._build_last_interactions(ctx, limit=hist_limit)
            try:
                prev = (convo_history[-1]["user_query"] if convo_history else "")
                print(f"CORE:HIST_READ | count={len(convo_history)} | last_user='{str(prev)[:60]}'")
                dump = [
                    {
                        "i": i + 1,
                        "user": str(h.get("user_query", ""))[:80],
                        "bot": str(h.get("bot_reply", "") or "")[:100],
                    }
                    for i, h in enumerate(convo_history)
                ]
                print(f"CORE:HIST_READ_DUMP | {json.dumps(dump, ensure_ascii=False)}")
            except Exception:
                pass
            last_user_query = ""
            try:
                raw_hist = (session.get("conversation_history", []) or [])
                if raw_hist and isinstance(raw_hist[-1], dict):
                    last_user_query = str(raw_hist[-1].get("user_query", "")).strip()
            except Exception:
                last_user_query = ""

            slot_answers = {
                "product_intent": session.get("product_intent"),
                "budget": session.get("budget"),
                "dietary_requirements": session.get("dietary_requirements"),
                "preferences": session.get("preferences"),
            }

            # Build prompts per your outline (no base_query)
            interactions_json = json.dumps(convo_history, ensure_ascii=False)
            product_intent = str(session.get("product_intent") or "").strip()

            if is_follow_up:
                prompt = (
                    "You are expert at extracting Elasticsearch parameters.\n\n"
                    f"FOLLOW-UP QUERY: \"{current_text}\"\n"
                    f"PRODUCT_INTENT: {product_intent}\n"
                    f"LAST {hist_limit} INTERACTIONS (user↔bot incl. asks/answers): {interactions_json}\n\n"
                    "Task: Figure out the delta and regenerate adapted ES params.\n"
                    "Return fields: q, category_group, subcategory, category_paths, brands[], dietary_terms[], price_min, price_max, keywords[], phrase_boosts[], size, anchor_product_noun.\n"
                    "Guidance: Keep q to product nouns only. Dietary/price belong in their own fields. Subcategory should be a taxonomy leaf (e.g., 'chips_and_crisps').\n"
                    "Recency weighting: Consider the last ~10 interactions, giving substantially more weight to the most recent turns. You may think in terms of a decaying pattern (e.g., 0.5 → 0.25 → 0.15 …) but use judgment rather than strict rules.\n"
                    "Also RETURN anchor_product_noun: the most recent, specific product noun/phrase you identify as the anchor. CONSTRUCT q using anchor_product_noun plus any CURRENT modifiers (e.g., ingredient→combine with parent: 'tomato' + 'sauce' → 'tomato sauce'). If no product noun exists, set anchor_product_noun to a generic intent noun (e.g., 'breakfast options', 'evening snacks', 'healthy snacks') and construct q from that generic noun.\n\n"
                    "Heuristic: If CURRENT_USER_TEXT looks like a modifier-only message (price/dietary/quality or a short ingredient/flavor), prefer to ANCHOR q to the most recent product noun/phrase. Prefer specific noun-phrases over generic parents.\n\n"
                    "Examples:\n"
                    "1) History: 'want some good sauces' → anchor=sauces; Current: 'tomato' → q='tomato sauce', category_group='f_and_b', subcategory='sauces_condiments'.\n"
                    "2) History: 'chips' → anchor=chips; Current: 'banana' → q='banana chips'.\n"
                    "3) History: 'noodles' → anchor=noodles; Current: 'gluten free under 100' → q='noodles', dietary_terms=['GLUTEN FREE'], price_max=100.\n"
                )
            else:
                prompt = (
                    "You are expert at extracting Elasticsearch parameters.\n\n"
                    f"USER QUERY: \"{current_text}\"\n"
                    f"CONTEXT SO FAR (last {hist_limit} interactions): {interactions_json}\n"
                    f"PRODUCT_INTENT: {product_intent}\n\n"
                    "Task: Extract params for the user's latest concern.\n"
                    "Return fields: q, category_group, subcategory, category_paths, brands[], dietary_terms[], price_min, price_max, keywords[], phrase_boosts[], size, anchor_product_noun.\n"
                    "Guidance: Keep q to product nouns only. Dietary/price belong in their own fields. Subcategory should be a taxonomy leaf (e.g., 'chips_and_crisps').\n"
                    "Recency weighting (non-follow-up): Give the most weight to CURRENT_USER_TEXT; also consider the last 3–5 interactions with a lighter, decaying emphasis. Prefer more specific noun-phrases (e.g., 'banana chips') over generic parents ('chips'). If CURRENT_USER_TEXT is generic or ambiguous, fall back to a reasonable weighted phrase from history.\n"
                    "Also RETURN anchor_product_noun: the specific product noun/phrase you identify (or a generic intent noun like 'breakfast options', 'evening snacks', 'healthy snacks' if no specific product is present). CONSTRUCT q using anchor_product_noun plus any CURRENT modifiers if appropriate.\n"
                )

            # CORE log: LLM2 input
            try:
                print(f"CORE:LLM2_IN | follow_up={is_follow_up} | current='{current_text[:60]}' | pi='{product_intent}' | interactions={len(convo_history)}")
            except Exception:
                pass

            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[UNIFIED_ES_PARAMS_TOOL],
                tool_choice={"type": "tool", "name": "generate_unified_es_params"},
                temperature=0,
                max_tokens=900,
            )
            tool_use = pick_tool(resp, "generate_unified_es_params")
            if not tool_use:
                return {}

            params = _strip_keys(tool_use.input or {})

            # CORE log: raw out keys
            try:
                kset = list(params.keys())
                print(f"CORE:LLM2_OUT_KEYS | keys={kset}")
            except Exception:
                pass

            if isinstance(params.get("dietary_terms"), list):
                params["dietary_terms"] = [str(x).strip().upper() for x in params["dietary_terms"] if str(x).strip()]

            # Sanitize q from dietary words
            try:
                q_val_raw = str(params.get("q") or "")
                q_val = q_val_raw.lower()
                dts = params.get("dietary_terms") or []
                diet_phrases = {
                    "GLUTEN FREE": ["gluten free"],
                    "VEGAN": ["vegan"],
                    "PALM OIL FREE": ["palm oil free", "no palm oil"],
                    "SUGAR FREE": ["sugar free", "no sugar", "no added sugar"],
                    "ORGANIC": ["organic"],
                }
                for term in dts:
                    for ph in diet_phrases.get(term, []):
                        if ph in q_val:
                            q_val = q_val.replace(ph, " ")
                cleaned = " ".join(q_val.split()).strip()
                if cleaned:
                    params["q"] = cleaned
            except Exception:
                pass

            # Guard: avoid generic q (no base_query fallback)
            try:
                bad = {"products","items","product","thing","things"}
                q_after = str(params.get("q") or "").strip()
                if q_after.lower() in bad or len(q_after) < 3:
                    # prefer last user query noun, then from recent user queries, then current text
                    recent_user_texts = [h.get("user_query", "") for h in convo_history if isinstance(h, dict)]
                    fallback_noun = self._extract_noun(last_user_query) or self._extract_noun_from_texts(list(reversed(recent_user_texts))) or self._extract_noun(current_text)
                    if fallback_noun:
                        params["q"] = fallback_noun
            except Exception:
                pass

            # CORE log: compact values
            try:
                print(f"CORE:LLM2_OUT | q='{params.get('q')}' | cg='{params.get('category_group')}' | subcat='{params.get('subcategory')}' | price=({params.get('price_min')},{params.get('price_max')}) | dietary={params.get('dietary_terms')} | anchor='{params.get('anchor_product_noun')}' | paths={len(params.get('category_paths') or [])}")
            except Exception:
                pass

            # Size bump
            try:
                if (params.get("dietary_terms") or (params.get("price_min") is not None) or (params.get("price_max") is not None)) and (params.get("category_path") or params.get("category_paths")):
                    params["size"] = max(int(params.get("size", 20) or 20), 50)
            except Exception:
                pass

            # Prefer more specific phrase from recency weighting when LLM produced a generic parent
            try:
                weighted = self._recency_weighted_product(convo_history) if is_follow_up else self._recency_weighted_product_last3(convo_history)
                q_now = str(params.get("q") or "").strip().lower()
                current_phrase = self._extract_product_phrase(current_text)
                # For non-follow-up, prefer current phrase first
                if not is_follow_up and current_phrase:
                    cp = current_phrase.strip().lower()
                    if q_now and (len(cp) > len(q_now)) and cp.endswith(q_now):
                        params["q"] = current_phrase
                        print("CORE:Q_REFINE | source=current_text_phrase")
                # Otherwise use weighted phrase if it is a more specific suffix
                q_now = str(params.get("q") or "").strip().lower()
                if weighted:
                    w = weighted.strip().lower()
                    if q_now and (len(w) > len(q_now)) and w.endswith(q_now):
                        params["q"] = weighted
                        print("CORE:Q_REFINE | source=recency_weighted")
            except Exception:
                pass

            # Compose modifier + anchor noun for follow-ups when current text is likely a modifier (ingredient/flavor)
            try:
                if is_follow_up:
                    tokenized = (current_text or "").strip().lower().split()
                    ingredient_like = {"tomato","banana","mango","garlic","onion","pepper","peri","peri peri","peri-peri","lemon","lime","masala","chilli","chili","mint","aloe","oats","wheat","saffron","turmeric","coconut","vanilla","strawberry","chocolate"}
                    looks_modifier = (len(tokenized) <= 2) and any(tok in ingredient_like for tok in tokenized)
                    anchor = self._recency_weighted_product(convo_history) or ""
                    parent = anchor.split()[-1] if anchor else ""
                    if looks_modifier and parent and parent not in ingredient_like:
                        candidate = f"{current_text.strip()} {parent}".strip()
                        # Only override if candidate is longer and meaningful
                        q_now = str(params.get("q") or "").strip()
                        if candidate and len(candidate) > len(q_now):
                            params["q"] = candidate
                            print("CORE:Q_REFINE | source=compose_modifier_anchor")
            except Exception:
                pass

            return params
        except Exception as exc:
            log.error(f"UNIFIED_ES_PARAMS_ERROR | {exc}")
            return {}

    def _build_last_interactions(self, ctx: UserContext, limit: int = 5) -> list[dict[str, str]]:
        """Build last N interactions, including ASK/answers if present."""
        out: list[dict[str, str]] = []
        try:
            hist = (ctx.session.get("conversation_history", []) or [])
            # take last N
            for h in hist[-limit:]:
                if isinstance(h, dict):
                    fa = (h.get("final_answer", {}) or {})
                    bot_text = (
                        fa.get("message_full")
                        or fa.get("summary_message")
                        or fa.get("message_preview")
                        or h.get("bot_reply")
                        or ""
                    )
                    out.append({
                        "user_query": str(h.get("user_query", ""))[:160],
                        "bot_reply": str(bot_text)[:240]
                    })
        except Exception:
            out = []
        return out

    def _is_follow_up_from_redis(self, ctx: UserContext) -> bool:
        """Heuristic follow-up detection using session (Redis-backed)."""
        try:
            session = ctx.session or {}
            current = str(getattr(ctx, "current_user_text", "") or session.get("current_user_text") or session.get("last_user_message") or "").strip().lower()
            # If there are recent conversation turns and current text looks like a modifier, treat as follow-up
            hist = session.get("conversation_history", []) or []
            has_history = isinstance(hist, list) and len(hist) > 0
            modifier_markers = ["under","over","below","above","budget","gluten","vegan","no palm","sugar free","organic","healthier","baked","fried"]
            ingredient_markers = ["tomato","banana","mango","garlic","onion","pepper","peri","peri peri","lemon","lime","masala","chilli","chili","mint","aloe","oats","wheat","saffron","turmeric","vanilla","strawberry","chocolate"]
            product_nouns = ["chips","noodles","bread","butter","jam","chocolate","soap","shampoo","juice","biscuit","cookie","ketchup","vermicelli","sauce","sauces","condiment","condiments"]
            looks_modifier = (any(m in current for m in modifier_markers) or any(i in current for i in ingredient_markers)) and not any(n in current for n in product_nouns)
            # Single-word or very short inputs that are ingredient-like should be follow-ups if history exists
            short_and_ingredient = (len(current.split()) <= 2) and any(i in current for i in ingredient_markers)
            decision = bool(has_history and (looks_modifier or short_and_ingredient))
            try:
                print(f"CORE:FOLLOWUP_RULE | has_hist={has_history} | looks_modifier={looks_modifier} | short_ing={short_and_ingredient} | current='{current}'")
            except Exception:
                pass
            return decision
        except Exception:
            return False

    def _get_fnb_taxonomy(self) -> Dict[str, Any]:
        """Get F&B taxonomy for LLM context."""
        try:
            from .recommendation import ElasticsearchRecommendationEngine
            engine = ElasticsearchRecommendationEngine()
            return getattr(engine, "_fnb_taxonomy", {})
        except Exception:
            return {}


# ─────────────────────────────────────────────────────────────
# Helper function
# ─────────────────────────────────────────────────────────────

def map_leaf_to_query_intent(leaf: str) -> QueryIntent:
    return INTENT_MAPPING.get(leaf, {}).get("query_intent", QueryIntent.GENERAL_HELP)

DIETARY_CHANGE_TOOL = {
    "name": "assess_dietary_change",
    "description": "Given CURRENT_USER_TEXT and previous dietary_terms, decide if dietary needs change. Return {change:boolean, dietary_terms?:string[]}.",
    "input_schema": {
        "type": "object",
        "properties": {
            "change": {"type": "boolean"},
            "dietary_terms": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["change"]
    }
}