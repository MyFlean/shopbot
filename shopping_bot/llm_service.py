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
            "summary_message_part_1": {"type": "string"},
            "summary_message_part_2": {"type": "string"},
            "summary_message_part_3": {"type": "string"},
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

# Personal Care ES params tool - 2025 unified schema
PERSONAL_CARE_ES_PARAMS_TOOL_2025 = {
    "name": "generate_personal_care_es_params",
    "description": "Extract Elasticsearch parameters for personal care products. Maintains product focus across conversation turns.",
    "input_schema": {
        "type": "object",
        "properties": {
            # Core fields (required)
            "anchor_product_noun": {
                "type": "string",
                "description": "Primary product being searched (2-6 words). Examples: 'shampoo', 'face serum', 'body lotion'",
                "minLength": 2,
                "maxLength": 60
            },
            "category_group": {
                "type": "string",
                "enum": ["personal_care"],
                "description": "Category group (always personal_care)"
            },
            
            # Taxonomy
            "category_paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
                "description": "Full paths like 'personal_care/hair/shampoo'"
            },
            
            # Domain-specific compatibility
            "skin_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["oily", "dry", "combination", "sensitive", "normal"]
                },
                "maxItems": 3,
                "description": "Detected or inferred skin types"
            },
            "hair_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["dry", "oily", "normal", "curly", "straight", "wavy", "frizzy", "thin", "thick"]
                },
                "maxItems": 3,
                "description": "Detected or inferred hair types"
            },
            
            # Positive signals (like keywords for food)
            "efficacy_terms": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
                "description": "MANDATORY: Desired benefits/efficacy (anti-dandruff, hydration, brightening). NEVER leave empty."
            },
            "skin_concerns": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
                "description": "Specific skin concerns (acne, pigmentation, dryness, aging)"
            },
            "hair_concerns": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
                "description": "Specific hair concerns (dandruff, hair fall, frizz, split ends)"
            },
            
            # Negative signals (clean-ingredient focus)
            "avoid_terms": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "sulfates", "parabens", "silicones", "mineral oil", "fragrance",
                        "alcohol", "phthalates", "formaldehyde", "harsh chemicals",
                        "artificial colors", "comedogenic"
                    ]
                },
                "maxItems": 4,
                "description": "Ingredients/attributes to avoid (clean-ingredient focus)"
            },
            "avoid_ingredients": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
                "description": "Specific ingredients to exclude (SLS, SLES, DEA, etc.)"
            },
            
            # Filters
            "price_min": {
                "type": "number",
                "minimum": 0,
                "description": "Minimum price in INR"
            },
            "price_max": {
                "type": "number",
                "minimum": 0,
                "description": "Maximum price in INR"
            },
            "brands": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
                "description": "Brand names mentioned"
            },
            
            # Soft ranking (like food keywords)
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
                "description": "MANDATORY: Quality attributes for reranking (gentle, nourishing, lightweight). NEVER leave empty."
            },
            "must_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
                "description": "Hard requirements (product variants like 'rose water', 'tea tree', 'aloe vera')"
            },
            
            # Product form
            "product_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["serum", "cream", "lotion", "oil", "gel", "foam", "mask", "scrub", "cleanser", "toner", "balm"]
                },
                "maxItems": 3,
                "description": "Product form factors"
            },
            
            # Metadata
            "size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 15,
                "default": 10,
                "description": "Number of results (max 15 for personal care)"
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of anchor, concerns, and clean-ingredient decisions (1 sentence)"
            }
        },
        "required": ["anchor_product_noun", "category_group", "category_paths", "efficacy_terms", "keywords"]
    }
}

# Legacy skin tool (kept for backward compatibility)
SKIN_ES_PARAMS_TOOL = {
    "name": "emit_skin_es_params",
    "description": "Extract Elasticsearch parameters for personal care/skin products based on user query and context.",
    "input_schema": {
        "type": "object",
        "properties": {
            "q": {"type": "string"},
            "size": {"type": "integer", "minimum": 1, "maximum": 50},
            "category_group": {"type": "string", "enum": ["personal_care"]},
            "brands": {"type": "array", "items": {"type": "string"}},
            "price_min": {"type": "number"},
            "price_max": {"type": "number"},
            "anchor_product_noun": {"type": "string"},
            "skin_types": {"type": "array", "items": {"type": "string"}},
            "hair_types": {"type": "array", "items": {"type": "string"}},
            "efficacy_terms": {"type": "array", "items": {"type": "string"}},
            "avoid_terms": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["q", "category_group"]
    }
}

# ============================================================================
# PERSONAL CARE 2025 OPTIMIZATION - IMPLEMENTATION STATE
# ============================================================================
# 
# COMPLETED:
# ✅ Step 1: Created PERSONAL_CARE_ES_PARAMS_TOOL_2025 unified schema
#    - Added comprehensive schema with domain-specific fields
#    - Includes skin_types, hair_types, efficacy_terms, avoid_terms
#    - Added keywords/must_keywords for soft/hard filtering
#    - Added product_types, skin_concerns, hair_concerns
#    - Clean-ingredient focus with avoid_terms enum
#
# NEXT STEPS TO IMPLEMENT:
# 🔄 Step 2: Write _build_personal_care_optimized_prompt method
#    - 7 priority rules following 2025 best practices
#    - Generic anchor refinement for personal care
#    - Proactive skin/hair type suggestions
#    - Mandatory keyword extraction
#    - Clean-ingredient awareness
#
# 🔄 Step 3: Implement _generate_personal_care_es_params_2025 method
#    - Session persistence and merge logic
#    - Generic anchor refinement logic
#    - Proactive skin/hair type suggestions
#    - Mandatory keyword generation
#
# 🔄 Step 4: Add session persistence for PC fields
#    - Store in ctx.session["debug"]["personal_care_es_params"]
#    - Merge user-provided vs LLM-suggested values
#    - Clear PC-specific slots on new assessments
#
# 🔄 Step 5: Add fuzzy matching to nested efficacy/skin_type queries
#    - Update _build_skin_es_query in es_products.py
#    - Change exact terms queries to fuzzy multi_match
#
# 🔄 Step 6: Route personal_care traffic to 2025 method
#    - Update generate_skin_es_params to use new method
#    - Maintain backward compatibility
#
# 🔄 Step 7: Clean up logging (keep only essential outputs)
#    - Remove verbose logging, keep only LLM outputs
#    - Show extracted keys like food path
#
# 🔄 Step 8: Run lints and validate
#    - Fix any syntax errors
#    - Test integration
#
# ============================================================================

# Personal care v2 tool schemas (initial vs follow-up)
FOLLOWUP_SKIN_PARAMS_TOOL = {
    "name": "extract_followup_skin_params",
    "description": "Extract search parameters for personal care follow-up queries",
    "input_schema": {
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "Product noun only (e.g., shampoo)"},
            "anchor_product_noun": {"type": "string"},
            "category_group": {"type": "string", "enum": ["personal_care"], "default": "personal_care"},
            "skin_types": {"type": "array", "items": {"type": "string", "enum": ["oily","dry","combination","sensitive","normal"]}},
            "hair_types": {"type": "array", "items": {"type": "string"}},
            "efficacy_terms": {"type": "array", "items": {"type": "string"}},
            "avoid_terms": {"type": "array", "items": {"type": "string"}},
            "brands": {"type": "array", "items": {"type": "string"}},
            "price_min": {"type": "number"},
            "price_max": {"type": "number"},
            "size": {"type": "integer", "maximum": 10, "default": 10}
        },
        "required": ["q", "category_group"]
    }
}

INITIAL_SKIN_PARAMS_TOOL = {
    "name": "extract_initial_skin_params",
    "description": "Extract search parameters for personal care initial queries",
    "input_schema": {
        "type": "object",
        "properties": {
            "q": {"type": "string"},
            "anchor_product_noun": {"type": "string"},
            "category_group": {"type": "string", "enum": ["personal_care"], "default": "personal_care"},
            "skin_types": {"type": "array", "items": {"type": "string"}},
            "hair_types": {"type": "array", "items": {"type": "string"}},
            "efficacy_terms": {"type": "array", "items": {"type": "string"}},
            "avoid_terms": {"type": "array", "items": {"type": "string"}},
            "brands": {"type": "array", "items": {"type": "string"}},
            "price_min": {"type": "number"},
            "price_max": {"type": "number"},
            "size": {"type": "integer", "maximum": 10, "default": 10}
        },
        "required": ["q", "category_group"]
    }
}

# ─────────────────────────────────────────────────────────────
# NEW: Combined Classify+Assess Tool (updated LLM1 schema)
# ─────────────────────────────────────────────────────────────

COMBINED_CLASSIFY_ASSESS_TOOL = {
    "name": "classify_and_assess",
    "description": (
        "Single-call classifier for WhatsApp shopping bot. Routes queries to product search, "
        "support, or general responses. For product queries, classifies domain/category/intent "
        "and generates contextual clarifying questions (ASK slots). "
        "IMPORTANT: If route='product', you MUST provide: domain, category, product_intent, ask_slots, and fetch_functions. "
        "If route='support' or 'general', provide simple_response instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            # === ROUTING & CLASSIFICATION ===
            "reasoning": {
                "type": "string",
                "description": "Brief chain-of-thought explaining classification decisions (2-3 sentences)"
            },
            "route": {
                "type": "string",
                "enum": ["product", "support", "general"],
                "description": "Primary routing decision"
            },

            # === FOLLOW-UP DETECTION ===
            "is_follow_up": {
                "type": "boolean",
                "description": "True if current query modifies/refines previous product search"
            },
            "follow_up_confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Confidence in follow-up detection"
            },

            # === PRODUCT CLASSIFICATION (required if route=product) ===
            "domain": {
                "type": "string",
                "enum": ["f_and_b", "personal_care", "other"],
                "description": "High-level product domain"
            },
            "category": {
                "type": "string",
                "description": "Specific product category (e.g., 'chips_and_crisps', 'shampoo')"
            },
            "subcategories": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
                "description": "Up to 3 likely subcategories for personal_care (from taxonomy)"
            },
            "product_intent": {
                "type": "string",
                "enum": ["is_this_good", "which_is_better", "show_me_alternate", "show_me_options"],
                "description": "User's product query intent"
            },

            # === ASK SLOTS (contextual questions) ===
            "ask_slots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slot_name": {
                            "type": "string",
                            "enum": [
                                "ASK_USER_BUDGET",
                                "ASK_DIETARY_REQUIREMENTS",
                                "ASK_USER_PREFERENCES",
                                "ASK_USE_CASE",
                                "ASK_QUANTITY",
                                "ASK_PC_CONCERN",
                                "ASK_PC_COMPATIBILITY",
                                "ASK_INGREDIENT_AVOID"
                            ],
                            "description": "Slot type: BUDGET (prices), DIETARY (gluten-free/vegan), PREFERENCES (flavor/brand), USE_CASE (daily/party), QUANTITY (servings/people), PC_CONCERN (acne/dandruff), PC_COMPATIBILITY (skin/hair type), INGREDIENT_AVOID (sulfate/paraben-free)"
                        },
                        "message": {
                            "type": "string",
                            "description": "Natural language question to ask user (conversational tone)"
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 3,
                            "description": "Up to 3 discrete, actionable options (2-5 words each)"
                        },
                        "needs_options": {
                            "type": "boolean",
                            "description": "True if this slot needs predefined options from backend"
                        }
                    },
                    "required": ["slot_name", "message", "options"]
                },
                "minItems": 2,
                "maxItems": 4,
                "description": "Ordered list of questions to ask (2-4 based on domain)"
            },

            # === BACKEND ACTIONS ===
            "fetch_functions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Backend functions to call (e.g., ['search_products'])"
            },

            # === NON-PRODUCT RESPONSES ===
            "simple_response": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Direct response message (for support/general queries)"
                    },
                    "response_type": {
                        "type": "string",
                        "enum": ["support_routing", "friendly_chat", "clarification_needed", "bot_identity", "out_of_category"]
                    }
                },
                "required": ["message", "response_type"]
            }
        },
        "required": ["reasoning", "route"]
    }
}

# ─────────────────────────────────────────────────────────────
# ASK enrichment constants (moved from prompt to code)
# ─────────────────────────────────────────────────────────────

BUDGET_BANDS_MAP = {
    # Food & Beverage
    "chips_and_crisps": ["Under ₹50", "₹50–150", "Over ₹150"],
    "noodles_pasta": ["Under ₹50", "₹50–150", "Over ₹150"],
    "cookies_biscuits": ["Under ₹60", "₹60–180", "Over ₹180"],
    "sauces_condiments": ["Under ₹100", "₹100–300", "Over ₹300"],
    "beverages": ["Under ₹80", "₹80–200", "Over ₹200"],
    "dairy_products": ["Under ₹60", "₹60–150", "Over ₹150"],
    "protein_bars": ["Under ₹100", "₹100–300", "Over ₹300"],
    "breakfast_cereals": ["Under ₹150", "₹150–400", "Over ₹400"],

    # Personal Care
    "shampoo": ["Under ₹99", "₹99–299", "Over ₹299"],
    "conditioner": ["Under ₹99", "₹99–299", "Over ₹299"],
    "face_wash": ["Under ₹99", "₹99–249", "Over ₹249"],
    "moisturizer": ["Under ₹199", "₹199–499", "Over ₹499"],
    "sunscreen": ["Under ₹199", "₹199–599", "Over ₹599"],
    "body_wash": ["Under ₹99", "₹99–299", "Over ₹299"],
    "hair_oil": ["Under ₹99", "₹99–299", "Over ₹299"],
    "serum": ["Under ₹299", "₹299–799", "Over ₹799"],

    # Fallback
    "default": ["Budget friendly", "Smart choice", "Premium"]
}

DIETARY_OPTIONS = ["Gluten free", "Vegan", "Low sodium", "Low sugar", "No palm oil", "No preference"]

# Personal Care specific
PC_CONCERN_SKIN = ["Acne", "Dark spots", "Aging", "Dryness", "No specific concern"]
PC_CONCERN_HAIR = ["Dandruff", "Hairfall", "Frizz", "Dryness", "No specific concern"]
PC_SKIN_TYPE = ["Oily", "Dry", "Combination", "Sensitive", "Not sure"]
PC_HAIR_TYPE = ["Curly", "Wavy", "Straight", "Oily scalp", "Dry scalp"]
PC_AVOID = ["Fragrance-free", "Sulfate-free", "Paraben-free", "Silicone-free", "No preference"]

# Quantity options (context-aware)
QUANTITY_PARTY = ["10-20 people", "20-30 people", "30+ people", "Not sure yet"]
QUANTITY_PERSONAL = ["Just for me", "2-3 people", "Family (4-6)", "Not sure"]
QUANTITY_BULK = ["1-2 packs", "3-5 packs", "Bulk order (6+)", "Flexible"]

# Use case options (category-specific)
USE_CASE_SNACKS = ["Party/gathering", "Daily snacking", "Kids lunch box", "Travel/on-the-go"]
USE_CASE_BEVERAGES = ["Morning boost", "Post-workout", "Throughout the day", "Special occasions"]
USE_CASE_PERSONAL_CARE = ["Daily use", "Special occasions", "Specific concern", "Trying something new"]

# User preferences (generic fallback)
USER_PREFERENCES_BRAND = ["Popular brands", "Budget-friendly", "Premium/imported", "No preference"]
USER_PREFERENCES_FLAVOR = ["Sweet", "Savory", "Tangy/spicy", "No preference"]
USER_PREFERENCES_TEXTURE = ["Crunchy", "Soft", "Creamy", "No preference"]



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
    "description": "Classify whether user message is a follow-up or a new query with confidence and rationale.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_follow_up": {"type": "boolean", "description": "True if message refines/continues previous search"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "detected_product_focus": {"type": "string"},
            "previous_product_context": {"type": "string"},
            "reason": {"type": "string"},
            "patch": {
                "type": "object",
                "properties": {
                    "slots": {"type": "object"},
                    "intent_override": {"type": "string"},
                    "reset_context": {"type": "boolean"},
                },
            },
        },
        "required": ["is_follow_up", "confidence", "reason"],
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

# Taxonomy-backed categorization examples (2025 best practices)
TAXONOMY_CATEGORIZATION_EXAMPLES = """
<taxonomy_examples>
<example name="exact_l3_single">
Query: "banana chips"
Reasoning: Specific flavor variant → exact L3 match in light_bites
Paths: ["f_and_b/food/light_bites/chips_and_crisps"]
</example>

<example name="generic_ranked_alternatives">
Query: "ice cream"
Reasoning: Generic query → rank by popularity (tubs most common, then alternatives)
Paths: [
  "f_and_b/food/frozen_treats/ice_cream_tubs",
  "f_and_b/food/frozen_treats/ice_cream_cups",
  "f_and_b/food/frozen_treats/kulfi"
]
</example>

<example name="l2_fallback_ambiguous">
Query: "frozen snacks"
Reasoning: Ambiguous L3 → use L2-only path
Paths: ["f_and_b/food/frozen_foods"]
</example>

<example name="beverages_branch">
Query: "cold coffee"
Reasoning: Beverage domain, iced variant
Paths: ["f_and_b/beverages/tea_coffee_and_more/iced_coffee_and_tea"]
</example>

<example name="beverages_generic">
Query: "soft drinks"
Reasoning: Beverage, carbonated category
Paths: ["f_and_b/beverages/sodas_juices_and_more/soft_drinks"]
</example>

<example name="cross_l2_breakfast">
Query: "healthy breakfast"
Reasoning: Multiple L2 possibilities → rank cereals first, then dairy
Paths: [
  "f_and_b/food/breakfast_essentials/muesli_and_oats",
  "f_and_b/food/dairy_and_bakery/yogurt_and_shrikhand"
]
</example>

<example name="sweet_category">
Query: "chocolate"
Reasoning: Sweet treats, regular vs premium unclear → include both
Paths: [
  "f_and_b/food/sweet_treats/chocolates",
  "f_and_b/food/sweet_treats/premium_chocolates"
]
</example>

<example name="noodles_pasta">
Query: "noodles"
Reasoning: Distinct L2 for noodles
Paths: ["f_and_b/food/noodles_and_vermicelli/vermicelli_and_noodles"]
</example>

<example name="biscuits_variant">
Query: "digestive biscuits"
Reasoning: Specific biscuit type
Paths: ["f_and_b/food/biscuits_and_crackers/digestive_biscuits"]
</example>

<example name="dairy_cheese">
Query: "cheese"
Reasoning: Dairy category
Paths: ["f_and_b/food/dairy_and_bakery/cheese"]
</example>

<example name="spreads">
Query: "peanut butter"
Reasoning: Spreads category
Paths: ["f_and_b/food/spreads_and_condiments/peanut_butter"]
</example>

<example name="packaged_meals">
Query: "ready to eat meals"
Reasoning: Convenience foods
Paths: ["f_and_b/food/packaged_meals/ready_to_eat_meals"]
</example>

<example name="frozen_veg">
Query: "frozen vegetables"
Reasoning: Frozen foods, veg variant
Paths: ["f_and_b/food/frozen_foods/frozen_vegetables_and_pulp"]
</example>

<example name="tea_variants">
Query: "green tea"
Reasoning: Tea category, herbal variant
Paths: ["f_and_b/beverages/tea_coffee_and_more/green_and_herbal_tea"]
</example>

<example name="juice">
Query: "fruit juice"
Reasoning: Beverages, juice subcategory
Paths: ["f_and_b/beverages/sodas_juices_and_more/fruit_juices"]
</example>
</taxonomy_examples>
"""

# Unified ES params generation tool - ONE authoritative call (2025 best practices)
UNIFIED_ES_PARAMS_TOOL = {
    "name": "generate_unified_es_params",
    "description": "Extract Elasticsearch parameters from user query and conversation context. Maintains product focus across follow-up turns.",
    "input_schema": {
        "type": "object",
        "properties": {
            # Core fields (required)
            "anchor_product_noun": {
                "type": "string",
                "description": "Primary product being searched (2-6 words). Examples: 'chips', 'banana chips', 'dry scalp shampoo'",
                "minLength": 2,
                "maxLength": 60
            },
            "category_group": {
                "type": "string",
                "enum": ["f_and_b", "personal_care"],
                "description": "Top-level category"
            },
            
            # Taxonomy
            "category_paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
                "description": (
                    "Ranked category paths from provided taxonomy (MOST relevant first). "
                    "Format: 'f_and_b/{food|beverages}/{l2}/{l3}' or 'f_and_b/{food|beverages}/{l2}'. "
                    "MUST exist in taxonomy. Return 1-3 paths ordered by relevance/likelihood."
                )
            },
            
            # Filters (extracted, not in anchor)
            "dietary_terms": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "GLUTEN FREE", "VEGAN", "VEGETARIAN", "PALM OIL FREE",
                        "SUGAR FREE", "LOW SODIUM", "LOW SUGAR", "ORGANIC",
                        "NO ADDED SUGAR", "DAIRY FREE", "NUT FREE", "SOY FREE",
                        "KETO", "HIGH PROTEIN", "LOW FAT", "ETC", "ETC"
                    ]
                },
                "maxItems": 5,
                "description": "Dietary constraints extracted from query (UPPERCASE only)"
            },
            "price_min": {
                "type": "number",
                "minimum": 0,
                "description": "Minimum price in INR"
            },
            "price_max": {
                "type": "number",
                "minimum": 0,
                "description": "Maximum price in INR"
            },
            "brands": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
                "description": "Brand names mentioned"
            },
            
            # Boost signals
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
                "description": "Additional search keywords NOT in anchor (for reranking)"
            },
            "must_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
                "description": "Hard requirements (e.g., flavor variants like 'orange', 'peri peri')"
            },
            
            # Metadata
            "size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
                "description": "Number of results"
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of anchor choice and follow-up detection (1 sentence)"
            }
        },
        "required": ["anchor_product_noun", "category_group","category_paths","dietary_terms","price_min","price_max","brands",
        "keywords","must_keywords","size"]
    }
}

SIMPLE_RESPONSE_TOOL = {
    "name": "generate_simple_response",
    "description": "Generate simple text response for non-product queries, including support detection",
    "input_schema": {
        "type": "object",
        "properties": {
            "response_type": {
                "type": "string",
                "enum": ["final_answer", "error", "support"],
                "description": "final_answer for normal responses, error for errors, support for customer support queries"
            },
            "message": {
                "type": "string",
                "description": "Response message"
            },
            "is_support_query": {
                "type": "boolean",
                "description": "True if this is a customer support query (order issues, complaints, help requests, etc.)"
            }
        },
        "required": ["response_type", "message", "is_support_query"]
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

FOLLOW_UP_PROMPT_TEMPLATE = """You are a precise conversation analyzer for a shopping assistant specializing in food and personal care products.

Your task: Determine if the user's new message continues their previous search (follow-up) or starts a completely new product search.

<conversation_context>
Previous conversation snapshot:
{last_snapshot}

Current session state:
{current_slots}

New user message: "{query}"
</conversation_context>

<classification_framework>
FOLLOW-UP indicators (refinement of existing search):
- Adds/modifies constraints without changing core product (e.g., "make it organic", "under $20", "fragrance-free version")
- References previous context implicitly (e.g., "cheaper ones", "in vanilla flavor", "travel size")
- Asks for alternatives within same category (e.g., "show me more brands", "any sulfate-free options?")
- Adjusts quantity/size/form of same product (e.g., "family pack", "liquid instead", "bulk size")

NEW QUERY indicators (different product search):
- Introduces distinctly different product category (e.g., shampoo → face serum, pasta → deodorant)
- Contains complete product specification unrelated to previous (e.g., "I need toothpaste" after discussing shampoo)
- Explicitly signals topic change (e.g., "actually, let me look for...", "forget that, show me...")
- Time-based context shift (e.g., moving from breakfast items to dinner items)
</classification_framework>

<analysis_steps>
1. Extract the core product noun from the previous query (if any)
2. Identify the product intent in the new message
3. Compare semantic relationship between previous and current product contexts
4. Weight recency - more recent turns have stronger continuity signals
5. Consider domain-specific patterns (personal care items often have modifier-heavy follow-ups)
</analysis_steps>

<examples>
Previous: "Show me shampoos"
New: "sulfate-free ones" → FOLLOW-UP (adds constraint to shampoo)
New: "face moisturizer" → NEW (different product category)

Previous: "organic pasta under $5"
New: "what about rice?" → NEW (different food category)
New: "any whole wheat options?" → FOLLOW-UP (modifies pasta type)

Previous: "vitamin C serums"
New: "with hyaluronic acid" → FOLLOW-UP (adds ingredient constraint)
New: "sunscreen SPF 50" → NEW (different skincare category)
</examples>

Analyze the conversation and call the classify_follow_up tool with your determination.
Focus your reason on specific textual evidence comparing previous vs current product context."""

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
1) First, determine if this is a customer support query by checking for:
   - Order-related issues: "where is my order", "order status", "tracking", "delivery", "shipping"
   - Complaints/problems: "problem with", "issue with", "not working", "broken", "wrong item"
   - Help requests: "help", "support", "customer service", "contact support", "connect me to support"
   - Account issues: "account problem", "login issue", "payment problem", "refund"
   - General support: "how to", "troubleshooting", "can't find", "need assistance"

2) If it's a support query:
   - Set response_type = "support"
   - Set is_support_query = true
   - Set message = "Hello! Please contact support at 6388977169."

3) If it's NOT a support query:
   - Set response_type = "final_answer" 
   - Set is_support_query = false
   - Write ONE clear, concise reply for this {query_intent} query (1-3 sentences, specific and actionable)

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
            "Before deciding, think silently through 5–8 steps comparing CURRENT text with recent context; weigh the latest turns more.\n"
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
            "   - q: a concise, noun-led product phrase (strip price/currency). If CURRENT text is modifier-only (e.g., 'under 200'),\n"
            "     REUSE the most recent anchor noun phrase from recent turns/assessment.original_query and apply only the delta.\n"
            "   - category_group: MUST be exactly one of ['f_and_b','personal_care'] — never 'snacks' or other l2/l3.\n"
            "   - category_path(s): derive using the provided F&B taxonomy when applicable; include up to 3 full paths.\n"
            "   - price_min/price_max: parse INR ranges and apply delta if present (e.g., 'under 100' → price_max=100).\n"
            "   - dietary_terms/dietary_labels: UPPERCASE (e.g., 'GLUTEN FREE', 'PALM OIL FREE').\n"
            "   - brands, keywords, phrase_boosts/field_boosts: include only when explicit or strongly implied.\n"
            "   - size: suggest within [1,50] (server clamps).\n\n"
            "FOLLOW-UP BEHAVIOR (MANDATORY):\n"
            "- Keep the same noun for follow-ups unless the user clearly switches category.\n"
            "- If an assessment is active, prefer assessment.original_query as the noun anchor.\n"
            "- OPTIONS/ALTERNATIVES: DROP prior brand constraints but KEEP the noun.\n"
            "- Generic modifiers ('under 100', 'gluten free', 'baked only'): DO NOT change the noun. Only update constraints in es_params.\n\n"
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
            max_tokens=2000,
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
            "You are Flean's WhatsApp copywriter. Write one concise message that proves we understood the user, explains why the picks fit, and ends with exactly three short follow-ups. Tone: friendly, plain English.\n\n"
            "ABSOLUTE PRIVACY RULE (MANDATORY): NEVER include actual product IDs, SKUs, or internal identifiers in ANY text. If referring to an ID per instructions, include exactly the literal token '{product_id}' and DO NOT replace it with a real value.\n\n"
            "FORMAT TAGS (MANDATORY): Use only <bold>...</bold> for emphasis and <newline> to indicate line breaks. DO NOT use any other HTML/Markdown tags or entities. The output will be post-processed for WhatsApp formatting.\n\n"
            "Generate the final product answer and UX in ONE tool call.\n"
            "Inputs: user_query, product_intent, session snapshot (concise), last 5 user/bot pairs (10 turns), ES results (top 10), enriched briefs (top 1 for SPM; top 3 for MPM).\n"
            "Output: {response_type:'final_answer', summary_message (constructed from 3 parts), summary_message_part_1, summary_message_part_2, summary_message_part_3, product_ids(ordered; hero optional), ux:{ux_surface, dpl_runtime_text, quick_replies(3-4)}}.\n"
            "3-PART SUMMARY (MANDATORY for both food & skin):\n"
            "- summary_message_part_1: Mirror the brief (1–2 lines). Place 1 emoji to signal alignment (e.g., ✅ or 🔍). NEVER include actual product IDs.\n"
            "- summary_message_part_2: Hero pick (2–3 lines): state one crisp reason it fits (protein/fiber/less oil/spice/budget). After the product name/brand, insert '{product_id}' as literal text (DO NOT substitute the real ID). If citing a percentile, use plain language with parentheses, e.g., 'higher in protein than most chips (top 10%).'. Append star rating as ⭐ repeated N times based on review_stats.average (rounded to nearest integer, clamp 1–5). If rating missing, omit stars. NEVER include actual product IDs.\n"
            "- summary_message_part_3: Other picks (1–2 lines): group with one shared reason (e.g., 'also lower oil & budget-friendly'). Place 1 emoji here (e.g., 💡). Append star ratings for each product mentioned using ⭐ repeated N times from review_stats.average (rounded 1–5); if missing, omit stars. NEVER include actual product IDs.\n\n"
            "Rules:\n- SPM → clamp to 1 item and include product_ids[1].\n- MPM → choose a hero (healthiest/cleanest) and order product_ids with hero first.\n- Quick replies should be short, actionable pivots (budget/dietary/quality).\n- Evidence: use flean score/percentiles, nutrition grams, and penalties correctly (penalties high = bad).\n"
            "- REDACTION RULE: NEVER reveal product IDs/SKUs/internal identifiers anywhere in the output. If the model generates one, replace it with '{product_id}'.\n"
        )

        session_snapshot = {
            k: ctx.session.get(k) for k in [
                "budget", "dietary_requirements"
            ] if k in ctx.session
        }
        # Include last 10 conversation turns as 5 user/bot pairs
        conversation_pairs = []
        try:
            convo = ctx.session.get("conversation_history", []) or []
            if isinstance(convo, list) and convo:
                for h in convo[-10:]:
                    if isinstance(h, dict):
                        conversation_pairs.append({
                            "user_query": str(h.get("user_query", ""))[:160],
                            "bot_reply": str(h.get("bot_reply", ""))[:240],
                        })
        except Exception:
            conversation_pairs = []
        payload = {
            "user_query": query.strip(),
            "product_intent": product_intent,
            "session": session_snapshot,
            "conversation_history": conversation_pairs,
            "products": products_for_llm,
            "briefs": top_products_brief,
        }
        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt + "\n" + json.dumps(payload, ensure_ascii=False)}],
            tools=[FINAL_ANSWER_UNIFIED_TOOL],
            tool_choice={"type": "tool", "name": "generate_final_answer_unified"},
            temperature=0,
            max_tokens=2000,
        )
        tool_use = pick_tool(resp, "generate_final_answer_unified")
        result = _strip_keys(tool_use.input or {}) if tool_use else {}
        # Assemble 3-part summary into summary_message if parts present
        try:
            p1 = (result.get("summary_message_part_1") or "").strip()
            p2 = (result.get("summary_message_part_2") or "").strip()
            p3 = (result.get("summary_message_part_3") or "").strip()
            if any([p1, p2, p3]):
                joined = "\n".join([s for s in [p1, p2, p3] if s])
                # Sanitize: If any accidental IDs appear, replace patterns like ids or numbers near braces with literal token
                try:
                    import re
                    # Replace anything like {id: 123}, (#12345), or alphanumeric IDs following 'id' with '{product_id}' token
                    joined = re.sub(r"\{\s*id\s*[:=]\s*[^}]+\}", "{product_id}", joined, flags=re.IGNORECASE)
                    joined = re.sub(r"\b(id|sku)\s*[:#-]?\s*[A-Za-z0-9_-]{3,}\b", "{product_id}", joined, flags=re.IGNORECASE)
                    joined = re.sub(r"#[0-9]{4,}", "{product_id}", joined)
                except Exception:
                    pass
                # Preserve key name summary_message
                result["summary_message"] = joined
        except Exception:
            pass
        return result

    async def classify_and_assess(self, query: str, ctx: Optional[UserContext] = None) -> Dict[str, Any]:
        """Single-call classifier with staged reasoning and minimal integration changes."""
        # ============== STAGE 1: Build Minimal Context ==============
        context_summary = {
            "has_history": False,
            "last_intent": None,
            "last_category": None,
            "last_slots": {},
            "recent_turns": []
        }
        try:
            if ctx:
                history = ctx.session.get("history", [])
                if history:
                    last = history[-1]
                    context_summary.update({
                        "has_history": True,
                        "last_intent": last.get("intent"),
                        "last_category": last.get("category"),
                        "last_slots": {k: v for k, v in (last.get("slots") or {}).items() if v}
                    })
                convo = ctx.session.get("conversation_history", []) or []
                if isinstance(convo, list) and convo:
                    for turn in convo[-6:]:
                        if isinstance(turn, dict):
                            context_summary["recent_turns"].append({
                                "user": str(turn.get("user_query", ""))[:100],
                                "bot": str(turn.get("bot_reply", ""))[:120]
                            })
        except Exception as e:
            log.warning(f"Context extraction failed: {e}")

        # ============== STAGE 2: Build Structured Prompt ==============
        personal_care_taxonomy = self._get_personal_care_taxonomy()
        prompt = f"""You are a classification engine for a WhatsApp shopping bot selling food/beverages and personal care products.

Your job: Analyze the user's message and classify it in ONE tool call using chain-of-thought reasoning.

<bot_identity>
Name: Flean
Purpose: Shopping assistant specializing EXCLUSIVELY in food, beverages, and personal care products
Scope: Only handles product searches and recommendations within these two categories
Personality: Helpful, friendly, polite, and honest about limitations
</bot_identity>

<context>
Previous conversation:
{json.dumps(context_summary, ensure_ascii=False, indent=2)}

Current user message: "{query.strip()}"
</context>

<personal_care_taxonomy>
{json.dumps(personal_care_taxonomy, ensure_ascii=False)}
</personal_care_taxonomy>

<critical_instructions>
1. Always start with "reasoning" field explaining your classification
2. Be decisive - avoid hedging in classifications
3. For follow-up detection, heavily weight the most recent turn (last 1-2 exchanges)
4. Write ASK messages in natural, conversational tone (not robotic)
5. Question count (MANDATORY):
   - If domain == personal_care: return EXACTLY 4 ask_slots (no more, no less)
   - Else (food & beverages/other): return EXACTLY 2 ask_slots (no more, no less)
6. Options (MANDATORY for each ask_slot):
   - Provide EXACTLY 3 options per question
   - Each option must be 2-5 words, discrete, and actionable
   - Avoid generic placeholders like "Option 1" or "Other"
   - Include a flexible option when relevant (e.g., "No preference", "Not sure yet", "Flexible")
7. Order ask_slots by priority (most important first)
8. For support queries, be warm and provide the phone number clearly
9. For general queries, be friendly and redirect to product search

<special_routing_rules>
**BOT IDENTITY QUERIES:**
If the user asks about Flean itself (e.g., "What is Flean?", "Who are you?", "What do you do?"):
- route = "general"
- response_type = "bot_identity"
- Provide a warm introduction explaining Flean is a shopping assistant for food and personal care products
- Example: "Hi! I'm Flean, your shopping assistant 😊 I help you discover and find the perfect food, beverages, and personal care products. What are you looking for today?"

**OUT-OF-CATEGORY PRODUCT REQUESTS:**
If the user requests products that are NOT food/beverages/personal care (e.g., electronics, clothing, home appliances, furniture, books, toys):
- route = "general"
- response_type = "out_of_category"
- Politely acknowledge their request but explain Flean only handles food and personal care
- Be friendly and encouraging (don't make them feel rejected)
- Example: "I'd love to help, but I specialize only in food and personal care products 🙂 Is there anything from these categories I can help you find?"

IMPORTANT: These special cases take PRIORITY over regular product routing. Check for these FIRST in your reasoning.
</special_routing_rules>
</critical_instructions>

<ask_slot_guidance>
**Smart Question Selection (choose 2-4 most relevant):**

For Food & Beverage products:
- ASK_USER_BUDGET: Always valuable for price filtering
- ASK_DIETARY_REQUIREMENTS: For health-conscious users (gluten-free, vegan, etc.)
- ASK_QUANTITY: If context suggests party/event/bulk (e.g., "party tonight", "gathering", "need many")
- ASK_USE_CASE: For snacks/beverages (daily vs party vs travel)
- ASK_USER_PREFERENCES: For flavor/texture preferences when category is broad

For Personal Care products:
- ASK_USER_BUDGET: Always useful for cosmetics/skincare
- ASK_PC_CONCERN: Critical for skincare/haircare (acne, dandruff, aging, etc.)
- ASK_PC_COMPATIBILITY: Hair type (curly, oily) or skin type (oily, dry, sensitive)
- ASK_INGREDIENT_AVOID: For sensitive users (fragrance-free, sulfate-free, etc.)

**Examples:**
- Query: "chips for party tonight" → ASK_USER_BUDGET, ASK_QUANTITY, ASK_DIETARY_REQUIREMENTS, ASK_USER_PREFERENCES
- Query: "shampoo for my hair" → ASK_USER_BUDGET, ASK_PC_CONCERN, ASK_PC_COMPATIBILITY, ASK_INGREDIENT_AVOID  
- Query: "healthy breakfast cereal" → ASK_USER_BUDGET, ASK_DIETARY_REQUIREMENTS, ASK_USE_CASE
- Query: "face cream" → ASK_USER_BUDGET, ASK_PC_CONCERN, ASK_PC_COMPATIBILITY, ASK_INGREDIENT_AVOID

**Message writing tips:**
- Reference the user's query naturally (e.g., "Great! For your party tonight, how many guests...")
- Keep questions short and conversational (10-15 words max)
- Use friendly, helpful tone (not interrogative)
- Make options feel guided, not restrictive
</ask_slot_guidance>

<classification_examples>
**Example 1: Bot Identity Query**
User: "What is Flean?"
Classification:
- reasoning: "User is asking about the bot itself, not requesting any products."
- route: "general"
- simple_response:
  - message: "Hi! I'm Flean, your shopping assistant 😊 I help you discover and find the perfect food, beverages, and personal care products. What are you looking for today?"
  - response_type: "bot_identity"

**Example 2: Out-of-Category Product Request**
User: "I need a laptop"
Classification:
- reasoning: "User is requesting electronics, which is outside our food and personal care scope."
- route: "general"
- simple_response:
  - message: "I'd love to help, but I specialize only in food and personal care products 🙂 Is there anything from these categories I can help you find?"
  - response_type: "out_of_category"

**Example 3: Out-of-Category Product Request (clothing)**
User: "Show me t-shirts"
Classification:
- reasoning: "User wants clothing, which is not in our food/personal care categories."
- route: "general"
- simple_response:
  - message: "I focus on food and personal care products, so I can't help with clothing 😊 But I'd be happy to help you find snacks, beverages, skincare, or haircare items!"
  - response_type: "out_of_category"

**Example 4: Valid Food Product Request**
User: "I want chips"
Classification:
- reasoning: "User wants chips, which is a food product. Need to assess preferences."
- route: "product"
- domain: "f_and_b"
- category: "chips_and_crisps"
- product_intent: "show_me_options"
- ask_slots: [ASK_USER_BUDGET, ASK_USER_PREFERENCES with 3 options each]
- fetch_functions: ["search_products"]

**Example 5: Valid Personal Care Request**
User: "Need shampoo for my hair"
Classification:
- reasoning: "User wants shampoo, which is a personal care product. Should ask 4 questions."
- route: "product"
- domain: "personal_care"
- category: "shampoo"
- product_intent: "show_me_options"
- ask_slots: [ASK_USER_BUDGET, ASK_PC_CONCERN, ASK_PC_COMPATIBILITY, ASK_INGREDIENT_AVOID with 3 options each]
- fetch_functions: ["search_products"]
</classification_examples>

Now classify the user's current message. Return ONLY the tool call."""

        # ============== STAGE 3: LLM Call ==============
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[COMBINED_CLASSIFY_ASSESS_TOOL],
                tool_choice={"type": "tool", "name": "classify_and_assess"},
                temperature=0,
                max_tokens=2000,
            )
            tool_use = pick_tool(resp, "classify_and_assess")
            if not tool_use:
                return self._fallback_response()
            data = tool_use.input or {}
        except Exception as e:
            log.error(f"LLM classification failed: {e}")
            return self._fallback_response()

        # ============== STAGE 4: Validate and passthrough ASK slots ==============
        route = data.get("route")
        if route == "product":
            ask_slots = list(data.get("ask_slots", []) or [])
            domain = str(data.get("domain", "")).lower()

            # Enforce exact question count by domain
            expected_count = 4 if domain == "personal_care" else 2
            if len(ask_slots) != expected_count:
                log.warning(
                    "ASK slot count mismatch for domain=%s; expected=%s got=%s",
                    domain,
                    expected_count,
                    len(ask_slots),
                )
            # Trim or keep as-is (do not synthesize new questions)
            if len(ask_slots) > expected_count:
                ask_slots = ask_slots[:expected_count]

            enriched_asks: Dict[str, Dict[str, Any]] = {}
            for slot in ask_slots:
                slot_name = slot.get("slot_name")
                message = slot.get("message")
                options = slot.get("options") or []
                if not isinstance(options, list):
                    options = [str(options)]
                # Enforce MAX 3 options per question
                if len(options) > 3:
                    options = options[:3]

                enriched_asks[slot_name] = {"message": message, "options": options}

            data["ask"] = enriched_asks
            data.pop("ask_slots", None)

            # Back-compat flags used elsewhere
            data["is_product_related"] = True
            data["layer3"] = str(data.get("category", ""))
        else:
            data["is_product_related"] = False
            data["layer3"] = "general"

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
            temperature=0,
            max_tokens=2000,
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
                temperature=0,
                max_tokens=2000,
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
        
        # Log which path we're taking
        try:
            print(f"CORE:LLM1_PATH_DECISION | intent_l3={intent_l3} | has_products={has_products} | product_intents={intent_l3 in product_intents}")
        except Exception:
            pass
        
        if intent_l3 in product_intents and has_products:
            result = await self._generate_product_response(query, ctx, fetched, intent_l3, product_intent)
            if product_intent:
                result["product_intent"] = product_intent
            return result
        else:
            return await self._generate_simple_response(query, ctx, fetched, intent_l3, query_intent)

    def _fallback_response(self) -> Dict[str, Any]:
        """Fallback used when LLM1 classification fails; aligns with existing response types."""
        return {
            "reasoning": "LLM failure, using fallback",
            "route": "general",
            "is_product_related": False,
            "layer3": "error",
            "simple_response": {
                "message": "I'm having trouble understanding. Could you rephrase what you're looking for?",
                "response_type": "clarification_needed",
            },
        }

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
        
        # Build compact briefs directly from search hits (no mget for personal care)
        def _first_25_words(text: str) -> str:
            if not text:
                return ""
            words = str(text).split()
            return " ".join(words[:25])
        
        top_k = 1 if (product_intent and product_intent == "is_this_good") else 3
        top_products_brief: List[Dict[str, Any]] = []
        for p in products_data[:top_k]:
            try:
                        brief = {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "brand": p.get("brand"),
                    "price": p.get("price"),
                    "review_stats": p.get("review_stats", {}),
                    "skin_compatibility": p.get("skin_compatibility", {}),
                    "efficacy": p.get("efficacy", {}),
                    "side_effects": p.get("side_effects", {}),
                    "claims": {
                        "health_claims": ((p.get("package_claims", {}) or {}).get("health_claims") or []),
                        "dietary_labels": ((p.get("package_claims", {}) or {}).get("dietary_labels") or []),
                    },
                    "review_snippet": _first_25_words(p.get("review_text", "")),
                        }
                        top_products_brief.append(brief)
            except Exception:
                continue

        # Narrow LLM input: prefer small top-K for SPM to enable brand-aware selection later
        spm_mode = bool(product_intent and product_intent == "is_this_good")
        products_for_llm = products_data[:5] if spm_mode else products_data[:10]

        # Unified product + UX prompt and tool
        try:
            try:
                log.info(
                    f"UX_ENRICHMENT_COUNTS | top_ids={len(top_products_brief)} | briefs={len(top_products_brief)} | products_for_llm={len(products_for_llm)}"
                )
            except Exception:
                pass

            # Include last 10 conversation turns as 5 user/bot pairs
            _conversation_pairs = []
            try:
                _convo = ctx.session.get("conversation_history", []) or []
                if isinstance(_convo, list) and _convo:
                    for _h in _convo[-10:]:
                        if isinstance(_h, dict):
                            _conversation_pairs.append({
                                "user_query": str(_h.get("user_query", ""))[:160],
                                "bot_reply": str(_h.get("bot_reply", ""))[:240],
                            })
            except Exception:
                _conversation_pairs = []

            unified_context = {
                "user_query": query,
                "intent_l3": intent_l3,
                "product_intent": product_intent or ctx.session.get("product_intent") or "show_me_options",
                "session": {k: ctx.session.get(k) for k in ["budget", "dietary_requirements"] if k in ctx.session},
                "conversation_history": _conversation_pairs,
                "products": products_for_llm,
                "enriched_top": top_products_brief,
                "personal_care": {
                    "efficacy_terms": (ctx.session.get("debug", {}).get("last_skin_search_params", {}).get("efficacy_terms") if isinstance(ctx.session.get("debug", {}), dict) else None),
                    "avoid_terms": (ctx.session.get("debug", {}).get("last_skin_search_params", {}).get("avoid_terms") if isinstance(ctx.session.get("debug", {}), dict) else None),
                    "suitability": {
                        "skin_types": (ctx.session.get("debug", {}).get("last_skin_search_params", {}).get("skin_types") if isinstance(ctx.session.get("debug", {}), dict) else None),
                        "hair_types": (ctx.session.get("debug", {}).get("last_skin_search_params", {}).get("hair_types") if isinstance(ctx.session.get("debug", {}), dict) else None)
                    },
                    "reviews_hint": "Provide a short 'Reviews' line if two reviews are provided"
                }
            }
            unified_prompt = (
                "You are Flean's WhatsApp copywriter. Write one concise message that proves we understood the user, explains why the picks fit, and ends with exactly three short follow-ups. Tone: friendly, plain English.\n\n"
                "ABSOLUTE PRIVACY RULE (MANDATORY): NEVER include actual product IDs, SKUs, or internal identifiers in ANY text. If referring to an ID per instructions, include exactly the literal token '{product_id}' and DO NOT replace it with a real value.\n\n"
                "FORMAT TAGS (MANDATORY): Use only <bold>...</bold> for emphasis and <newline> to indicate line breaks. DO NOT use any other HTML/Markdown tags or entities. The output will be post-processed for WhatsApp formatting.\n\n"
                "You are producing BOTH the product answer and the UX block in a SINGLE tool call.\n"
                "Inputs:\n- user_query\n- intent_l3\n- product_intent (one of is_this_good, which_is_better, show_me_alternate, show_me_options)\n- session snapshot (budget, dietary)\n- last 5 user/bot pairs (10 turns)\n- products (top 5-10)\n- enriched_top (top 1 for SPM; top 3 for MPM)\n\n"
                "Output JSON (tool generate_final_answer_unified):\n"
                "{response_type:'final_answer', summary_message (constructed from 3 parts), summary_message_part_1, summary_message_part_2, summary_message_part_3, product_ids?, hero_product_id?, ux:{ux_surface, dpl_runtime_text, quick_replies(3-4)}}\n\n"
                "3-PART SUMMARY (MANDATORY for both food & skin):\n"
                "- summary_message_part_1: Mirror the brief (1–2 lines). Place 1 emoji to signal alignment (e.g., ✅ or 🔍). NEVER include actual product IDs.\n"
                "- summary_message_part_2: Hero pick (2–3 lines): state one crisp reason it fits (protein/fiber/less oil/spice/budget). After the product name/brand, insert '{product_id}' as literal text (DO NOT substitute the real ID). If citing a percentile, use plain language with parentheses, e.g., 'higher in protein than most chips (top 10%).'. Append star rating as ⭐ repeated N times based on review_stats.average (rounded to nearest integer, clamp 1–5). If rating missing, omit stars. NEVER include actual product IDs.\n"
                "- summary_message_part_3: Other picks (1–2 lines): group with one shared reason (e.g., 'also lower oil & budget-friendly'). Place 1 emoji here (e.g., 💡). Append star ratings for each product mentioned using ⭐ repeated N times from review_stats.average (rounded 1–5); if missing, omit stars. NEVER include actual product IDs.\n\n"
                "Rules (MANDATORY):\n"
                "- For is_this_good (SPM): choose 1 best item → ux_surface='SPM'; product_ids=[that_id]; dpl_runtime_text should read like a concise expert verdict.\n"
                "- For others (MPM): choose a hero (healthiest/cleanest using enriched_top), set hero_product_id and order product_ids with hero first; ux_surface='MPM'.\n"
                "- Quick replies: short and actionable pivots (budget ranges like 'Under ₹100', dietary like 'GLUTEN FREE', or quality pivots).\n"
                "- Evidence: use flean score/percentiles, nutrition grams, and penalties correctly (penalties high = bad).\n"
                "- REDACTION RULE: NEVER reveal product IDs/SKUs/internal identifiers anywhere in the output. If the model generates one, replace it with '{product_id}'.\n"
                "Return ONLY the tool call.\n"
            )

            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": unified_prompt + "\n" + json.dumps(unified_context, ensure_ascii=False)}],
                tools=[FINAL_ANSWER_UNIFIED_TOOL],
                tool_choice={"type": "tool", "name": "generate_final_answer_unified"},
                temperature=0,
                max_tokens=2000,
            )

            tool_use = pick_tool(resp, "generate_final_answer_unified")
            if not tool_use:
                # Fallback to old two-step product response path
                return self._create_fallback_product_response(products_data, query)

            result = _strip_keys(tool_use.input or {})
            # Assemble 3-part summary into summary_message if parts present
            try:
                _p1 = (result.get("summary_message_part_1") or "").strip()
                _p2 = (result.get("summary_message_part_2") or "").strip()
                _p3 = (result.get("summary_message_part_3") or "").strip()
                if any([_p1, _p2, _p3]):
                    _joined = "\n".join([s for s in [_p1, _p2, _p3] if s])
                    # Sanitize accidental IDs
                    try:
                        import re as _re
                        _joined = _re.sub(r"\{\s*id\s*[:=]\s*[^}]+\}", "{product_id}", _joined, flags=_re.IGNORECASE)
                        _joined = _re.sub(r"\b(id|sku)\s*[:#-]?\s*[A-Za-z0-9_-]{3,}\b", "{product_id}", _joined, flags=_re.IGNORECASE)
                        _joined = _re.sub(r"#[0-9]{4,}", "{product_id}", _joined)
                    except Exception:
                        pass
                    result["summary_message"] = _joined
            except Exception:
                pass

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
            
            # Build product_ids strictly from ES results; hero-first ordering; clamp to max 10
            try:
                es_ids: List[str] = [str(p.get("id")) for p in products_data if p.get("id")]
                hero_id = str(result.get("hero_product_id", "")).strip()
                ordered: List[str] = list(es_ids)
                if hero_id and hero_id in es_ids:
                    ordered = [hero_id] + [x for x in es_ids if x != hero_id]
                # Deduplicate while preserving order
                seen_ids: set[str] = set()
                unique_ids: List[str] = []
                for pid in ordered:
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        unique_ids.append(pid)
                result["product_ids"] = unique_ids[:10]
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

            # Note: No backfilling beyond ES results; fewer than 10 is acceptable
            # Optional enrichment: if summary_message lacks stars, ask LLM to add them
            try:
                summary_text = str(result.get("summary_message", "")).strip()
                if summary_text and ("⭐" not in summary_text):
                    # Prefer the same top-K list we sent to the LLM for context
                    products_for_stars = products_for_llm if isinstance(products_for_llm, list) else products_data
                    enriched = await self._add_stars_if_missing(summary_text, products_for_stars)
                    if isinstance(enriched, str) and enriched.strip():
                        result["summary_message"] = enriched.strip()
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
                temperature=0,
                max_tokens=2000,
            )
            
            tool_use = pick_tool(resp, "generate_simple_response")
            if not tool_use:
                return {
                    "response_type": "final_answer",
                    "message": "I can help you with shopping queries. What are you looking for?"
                }
            
            result = _strip_keys(tool_use.input or {})
            
            # Log the is_support_query value for debugging
            try:
                is_support = result.get("is_support_query", "NOT_FOUND")
                response_type = result.get("response_type", "NOT_FOUND")
                print(f"CORE:LLM1_SUPPORT_DETECTION | is_support_query={is_support} | response_type={response_type}")
            except Exception:
                pass
            
            return result
            
        except Exception:
            return {
                "response_type": "final_answer",
                "message": "I can help you with shopping queries. What are you looking for?"
            }

    async def _add_stars_if_missing(self, summary_text: str, products: List[Dict[str, Any]]) -> str:
        """Ask the LLM to append star ratings (⭐) in the summary text when missing.

        Use avg_rating when available (round to nearest integer 1–5). If missing,
        infer a reasonable rating (3–4). Only insert stars after product names' first
        occurrence. Do not change other wording.
        """
        try:
            compact_products = []
            for p in (products or [])[:5]:
                try:
                    compact_products.append({
                        "name": p.get("name") or p.get("text"),
                        "avg_rating": (p.get("review_stats", {}) or {}).get("avg_rating") or p.get("avg_rating"),
                        "total_reviews": (p.get("review_stats", {}) or {}).get("total_reviews") or p.get("total_reviews"),
                    })
                except Exception:
                    continue

            instructions = (
                "Insert star ratings (⭐ repeated 1–5) immediately after each product name in the summary.\n"
                "Use avg_rating when provided: round to nearest integer, clamp 1–5. If missing, infer 3–4 stars.\n"
                "Preserve all other text and emojis exactly. Return ONLY the updated summary text."
            )
            payload = {
                "role": "user",
                "content": [
                    {"type": "text", "text": instructions},
                    {"type": "text", "text": "Products context:"},
                    {"type": "text", "text": json.dumps({"products": compact_products}, ensure_ascii=False)},
                    {"type": "text", "text": "Original summary:"},
                    {"type": "text", "text": summary_text},
                ],
            }

            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[payload],
                temperature=0,
                max_tokens=2000,
            )

            parts = []
            for block in (resp.content or []):
                if getattr(block, "type", None) == "text":
                    parts.append(getattr(block, "text", ""))
            updated = "".join(parts).strip()
            return updated or summary_text
        except Exception:
            return summary_text

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
        recent_history = history[-5:] if isinstance(history, list) else []
        formatted_history = []
        for i, snap in enumerate(recent_history):
            weight = "recent" if i >= max(0, len(recent_history) - 2) else "older"
            try:
                formatted_history.append({
                    "turn": i + 1,
                    "weight": weight,
                    "user_query": (snap.get("query") or snap.get("user_query") or ""),
                    "detected_intent": snap.get("intent") or snap.get("intent_l3") or "",
                    "product_focus": snap.get("product_noun") or snap.get("anchor_product_noun") or "",
                })
            except Exception:
                formatted_history.append({"turn": i + 1, "weight": weight, "user_query": str(snap)[:120]})

        prompt = FOLLOW_UP_PROMPT_TEMPLATE.format(
            last_snapshot=json.dumps(formatted_history, ensure_ascii=False, indent=2),
            current_slots=json.dumps(ctx.session, ensure_ascii=False, indent=2),
            query=query,
        )

        try:
            resp = await self.anthropic.messages.create(
                model=getattr(Cfg, "LLM_CLASSIFIER_MODEL", Cfg.LLM_MODEL),
                messages=[{"role": "user", "content": prompt}],
                tools=[FOLLOW_UP_TOOL],
                tool_choice={"type": "tool", "name": "classify_follow_up"},
                temperature=0,
                max_tokens=2000,
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
                reset_context=bool(patch_dict.get("reset_context", not bool(ipt.get("is_follow_up", False)))),
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
                temperature=0,
                max_tokens=2000,
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
            temperature=0,
            max_tokens=2000,
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
                temperature=0,
                max_tokens=2000,
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
                max_tokens=2000,
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

            # 2025 unified ES params fast-path
            if current_text:
                return await self._generate_unified_es_params_2025(ctx, current_text)

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

            # Attempt Food-specific extraction first (anchor-as-q, must vs rerank) and adapt
            try:
                food_params = await self._try_food_es_extraction(ctx, current_text, convo_history, is_follow_up)
                if isinstance(food_params, dict) and food_params.get("q"):
                    try:
                        print("CORE:LLM2_FOOD_ADAPTED | " + json.dumps(food_params, ensure_ascii=False))
                    except Exception:
                        pass
                    return food_params
            except Exception as _food_exc:
                try:
                    print(f"CORE:LLM2_FOOD_FALLBACK | {str(_food_exc)[:120]}")
                except Exception:
                    pass

            # Format history with explicit recency indicators
            formatted_history = []
            total = len(convo_history)
            for idx, turn in enumerate(convo_history):
                if idx >= total - 2:
                    weight = "MOST_RECENT"
                elif idx >= total - 5:
                    weight = "RECENT"
                else:
                    weight = "OLDER"
                formatted_history.append({
                    "weight": weight,
                    "user": turn.get("user_query", "")[:120],
                    "bot": turn.get("bot_reply", "")[:100]
                })

            # Load F&B taxonomy for categorization
            fnb_taxonomy = self._get_fnb_taxonomy_hierarchical()
            
            if is_follow_up:
                prompt = f"""<task_definition>
Extract ALL Elasticsearch parameters in ONE call, maintaining product focus across turns.
</task_definition>

<conversation_type>{"FOLLOW_UP" if is_follow_up else "NEW_QUERY"}</conversation_type>

<inputs>
<current_message>{current_text}</current_message>

<conversation_history turns="{len(formatted_history)}">
{json.dumps(formatted_history, ensure_ascii=False, indent=2)}
</conversation_history>

<product_intent>{product_intent}</product_intent>

<user_slots>{json.dumps(slot_answers, ensure_ascii=False, indent=2)}</user_slots>
</inputs>

<fnb_taxonomy>
{json.dumps(fnb_taxonomy, ensure_ascii=False, indent=2)}
</fnb_taxonomy>

<reasoning_framework>
Think through these steps:
1. PRODUCT ANCHOR: What product noun appeared in last turns?
2. MESSAGE TYPE: MODIFIER_ONLY vs CATEGORY_SWITCH?
3. BUILD QUERY: Combine constraint + anchor for modifiers
4. EXTRACT CATEGORY: Map to category_group and paths using taxonomy
5. EXTRACT FILTERS: brands, dietary_terms, price, keywords
6. SET ANCHOR: Core product being searched
</reasoning_framework>

<rules>
<rule id="anchor_persistence" priority="CRITICAL">
Keep same product noun for follow-ups unless explicitly changed.
- "shampoo" → "dry scalp" → q: "dry scalp shampoo" ✓
- "chips" → "banana" → q: "banana chips" ✓
</rule>

<rule id="field_separation" priority="CRITICAL">
NEVER put budget/dietary/brand in q field.
- ❌ q: "gluten free chips under 100"
- ✓ q: "gluten free chips", price_max: 100
</rule>

<rule id="category_group" priority="CRITICAL">
category_group MUST be: f_and_b | personal_care | health_nutrition | home_kitchen | electronics
NEVER use subcategory names like "chips" or "snacks"
</rule>

<rule id="category_paths_taxonomy" priority="CRITICAL">
category_paths MUST come from provided fnb_taxonomy only.
- Return 1-3 paths ranked by relevance (MOST likely first)
- Format: "f_and_b/{{food|beverages}}/{{l2}}/{{l3}}" or "f_and_b/{{food|beverages}}/{{l2}}"
- For ambiguous queries, include multiple plausible L3s
- Never hallucinate paths not in taxonomy
</rule>

<rule id="dietary_normalization" priority="HIGH">
Normalize to UPPERCASE:
- "no palm oil" → ["PALM OIL FREE"]
- "gluten free" → ["GLUTEN FREE"]
- "vegan" → ["VEGAN"]
</rule>
</rules>

{TAXONOMY_CATEGORIZATION_EXAMPLES}

<example name="follow_up_attribute">
Scenario: User: "shampoo" → Bot: shows shampoos → User: "dry scalp"
Output:
{{
  "q": "dry scalp shampoo",
  "category_group": "personal_care",
  "size": 15,
  "anchor_product_noun": "dry scalp shampoo",
  "keywords": ["dry", "scalp"]
}}
</example>

<example name="follow_up_flavor">
Scenario: User: "chips" → Bot: shows chips → User: "banana"
Output:
{{
  "q": "banana chips",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
  "size": 20,
  "anchor_product_noun": "banana chips"
}}
</example>

<example name="follow_up_dietary">
Scenario: User: "noodles" → Bot: shows noodles → User: "gluten free under 100"
Output:
{{
  "q": "gluten free noodles",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/noodles_and_vermicelli/vermicelli_and_noodles"],
  "dietary_terms": ["GLUTEN FREE"],
  "price_max": 100,
  "size": 20,
  "anchor_product_noun": "gluten free noodles"
}}
</example>

<example name="options_request">
Scenario: User: "Lays chips" → Bot: shows Lays → User: "show me options"
Output:
{{
  "q": "chips",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
  "size": 25,
  "anchor_product_noun": "chips",
  "brands": []
}}
</example>

<output>
Return tool call to generate_unified_es_params with complete JSON.
Validation: q has product noun (2-6 words), no prices/brands, category_group is valid, category_paths from taxonomy, dietary_terms UPPERCASE
</output>"""
            else:
                prompt = f"""<task_definition>
Extract ALL Elasticsearch parameters in ONE call, maintaining product focus across turns.
</task_definition>

<conversation_type>{"FOLLOW_UP" if is_follow_up else "NEW_QUERY"}</conversation_type>

<inputs>
<current_message>{current_text}</current_message>

<conversation_history turns="{len(formatted_history)}">
{json.dumps(formatted_history, ensure_ascii=False, indent=2)}
</conversation_history>

<product_intent>{product_intent}</product_intent>

<user_slots>{json.dumps(slot_answers, ensure_ascii=False, indent=2)}</user_slots>
</inputs>

<fnb_taxonomy>
{json.dumps(fnb_taxonomy, ensure_ascii=False, indent=2)}
</fnb_taxonomy>

<reasoning_framework>
Think through these steps:
1. PRODUCT ANCHOR: What product noun appeared in last turns?
2. MESSAGE TYPE: MODIFIER_ONLY vs CATEGORY_SWITCH?
3. BUILD QUERY: Combine constraint + anchor for modifiers
4. EXTRACT CATEGORY: Map to category_group and paths using taxonomy
5. EXTRACT FILTERS: brands, dietary_terms, price, keywords
6. SET ANCHOR: Core product being searched
</reasoning_framework>

<rules>
<rule id="anchor_persistence" priority="CRITICAL">
Keep same product noun for follow-ups unless explicitly changed.
- "shampoo" → "dry scalp" → q: "dry scalp shampoo" ✓
- "chips" → "banana" → q: "banana chips" ✓
</rule>

<rule id="field_separation" priority="CRITICAL">
NEVER put budget/dietary/brand in q field.
- ❌ q: "gluten free chips under 100"
- ✓ q: "gluten free chips", price_max: 100
</rule>

<rule id="category_group" priority="CRITICAL">
category_group MUST be: f_and_b | personal_care | health_nutrition | home_kitchen | electronics
NEVER use subcategory names like "chips" or "snacks"
</rule>

<rule id="category_paths_taxonomy" priority="CRITICAL">
category_paths MUST come from provided fnb_taxonomy only.
- Return 1-3 paths ranked by relevance (MOST likely first)
- Format: "f_and_b/{{food|beverages}}/{{l2}}/{{l3}}" or "f_and_b/{{food|beverages}}/{{l2}}"
- For ambiguous queries, include multiple plausible L3s
- Never hallucinate paths not in taxonomy
</rule>

<rule id="dietary_normalization" priority="HIGH">
Normalize to UPPERCASE:
- "no palm oil" → ["PALM OIL FREE"]
- "gluten free" → ["GLUTEN FREE"]
- "vegan" → ["VEGAN"]
</rule>
</rules>

{TAXONOMY_CATEGORIZATION_EXAMPLES}

<example name="follow_up_attribute">
Scenario: User: "shampoo" → Bot: shows shampoos → User: "dry scalp"
Output:
{{
  "q": "dry scalp shampoo",
  "category_group": "personal_care",
  "size": 15,
  "anchor_product_noun": "dry scalp shampoo",
  "keywords": ["dry", "scalp"]
}}
</example>

<example name="follow_up_flavor">
Scenario: User: "chips" → Bot: shows chips → User: "banana"
Output:
{{
  "q": "banana chips",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
  "size": 20,
  "anchor_product_noun": "banana chips"
}}
</example>

<example name="follow_up_dietary">
Scenario: User: "noodles" → Bot: shows noodles → User: "gluten free under 100"
Output:
{{
  "q": "gluten free noodles",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/noodles_and_vermicelli/vermicelli_and_noodles"],
  "dietary_terms": ["GLUTEN FREE"],
  "price_max": 100,
  "size": 20,
  "anchor_product_noun": "gluten free noodles"
}}
</example>

<example name="options_request">
Scenario: User: "Lays chips" → Bot: shows Lays → User: "show me options"
Output:
{{
  "q": "chips",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
  "size": 25,
  "anchor_product_noun": "chips",
  "brands": []
}}
</example>

<output>
Return tool call to generate_unified_es_params with complete JSON.
Validation: q has product noun (2-6 words), no prices/brands, category_group is valid, category_paths from taxonomy, dietary_terms UPPERCASE
</output>"""

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
                max_tokens=2000,
            )
            tool_use = pick_tool(resp, "generate_unified_es_params")
            if not tool_use:
                return {}

            params = _strip_keys(tool_use.input or {})

            # CORE log: full raw output and keys
            try:
                print("CORE:LLM2_OUT_FULL | " + json.dumps(params, ensure_ascii=False))
                kset = list(params.keys())
                print(f"CORE:LLM2_OUT_KEYS | keys={kset}")
            except Exception:
                pass

            if isinstance(params.get("dietary_terms"), list):
                params["dietary_terms"] = [str(x).strip().upper() for x in params["dietary_terms"] if str(x).strip()]

            # Anchor-as-q override: if anchor exists, force q = anchor (trimmed)
            try:
                anchor = str(params.get("anchor_product_noun") or "").strip()
                if anchor:
                    params["q"] = anchor
            except Exception:
                pass

            # As a defensive fallback only, strip dietary phrases from q if model violated the rule
            try:
                q_val_raw = str(params.get("q") or "")
                q_low = q_val_raw.lower()
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
                        if ph in q_low:
                            q_low = q_low.replace(ph, " ")
                cleaned = " ".join(q_low.split()).strip()
                if cleaned and cleaned != q_val_raw:
                    params["q"] = cleaned
            except Exception:
                pass

            # CORE log: final params after anchor/q normalization
            try:
                print("CORE:LLM2_OUT_FINAL | " + json.dumps(params, ensure_ascii=False))
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

    async def _generate_unified_es_params_2025(self, ctx: UserContext, current_text: str) -> Dict[str, Any]:
        """2025 best-practices: schema-first, concise prompt, forced tool call, minimal post-processing."""
        session = ctx.session or {}
        is_follow_up = bool(session.get("assessment", {}) or {})
        
        # Canonical path→noun mapping for de-genericization
        CATEGORY_PATH_TO_NOUNS = {
            "light_bites/savory_namkeen": "namkeen",
            "light_bites/chips_and_crisps": "chips",
            "light_bites/popcorn": "popcorn",
            "light_bites/dry_fruit_and_nut_snacks": "dry fruits",
            "light_bites/energy_bars": "protein bar",
            "biscuits_and_crackers/cookies": "cookies",
            "biscuits_and_crackers/cream_filled_biscuits": "biscuits",
            "biscuits_and_crackers/glucose_and_marie_biscuits": "biscuits",
            "biscuits_and_crackers/rusks_and_khari": "rusks",
            "biscuits_and_crackers/digestive_biscuits": "digestive biscuits",
            "sweet_treats/chocolates": "chocolates",
            "sweet_treats/premium_chocolates": "chocolates",
            "sweet_treats/candies_gums_and_mints": "candies",
            "sweet_treats/indian_mithai": "sweets",
            "breakfast_essentials/breakfast_cereals": "cereals",
            "breakfast_essentials/muesli_and_oats": "oats",
            "dairy_and_bakery/bread_and_buns": "bread",
            "dairy_and_bakery/butter": "butter",
            "dairy_and_bakery/cheese": "cheese",
            "sodas_juices_and_more/fruit_juices": "juice",
            "sodas_juices_and_more/soft_drinks": "soft drinks",
            "sodas_juices_and_more/flavored_milk_drinks": "flavored milk",
            "noodles_and_vermicelli/vermicelli_and_noodles": "noodles",
            "spreads_and_condiments/ketchup_and_sauces": "sauce",
            "spreads_and_condiments/peanut_butter": "peanut butter",
        }
        GENERIC_ANCHORS = {
            "snacks", "treats", "items", "products", "something", "options",
            "sweet treats", "savory snacks", "evening snacks", "breakfast items"
        }

        # Build conversation context (recency-weighted)
        hist_limit = 10 if is_follow_up else 2
        convo_history = self._build_last_interactions(ctx, limit=hist_limit)

        # Extract slot answers
        slot_answers = {
            "product_intent": session.get("product_intent"),
            "budget": session.get("budget"),
            "dietary": session.get("dietary_requirements"),
            "preferences": session.get("preferences"),
        }

        # Format history with explicit recency signals
        history_turns: list[dict[str, Any]] = []
        total = len(convo_history)
        for idx, turn in enumerate(convo_history):
            recency = "MOST_RECENT" if idx >= total - 2 else ("RECENT" if idx >= total - 5 else "OLDER")
            history_turns.append({
                "recency": recency,
                "user": turn.get("user_query", "")[:100],
                "bot_summary": turn.get("bot_reply", "")[:80]
            })

        # Build optimized prompt WITH taxonomy injection and strict rules
        fnb_taxonomy = self._get_fnb_taxonomy_hierarchical()
        prompt = self._build_optimized_prompt(
            current_text=current_text,
            history=history_turns,
            is_follow_up=is_follow_up,
            product_intent=str(session.get("product_intent") or ""),
            slots=slot_answers,
        ) + (
            "\n<fnb_taxonomy>\n" +
            json.dumps(fnb_taxonomy, ensure_ascii=False, indent=2) +
            "\n</fnb_taxonomy>\n\n" +
            TAXONOMY_CATEGORIZATION_EXAMPLES +
            "\n<taxonomy_rule priority=\"CRITICAL\">\n"
            "Use ONLY the categories provided in <fnb_taxonomy> for f_and_b.\n"
            "- Return 1-3 category_paths ordered by relevance.\n"
            "- Format: 'f_and_b/{food|beverages}/{l2}/{l3}' (or L2-only when L3 unknown).\n"
            "- NEVER output health_nutrition or any category not present in taxonomy.\n"
            "</taxonomy_rule>\n"
        )

        # Call LLM with forced tool use
        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[UNIFIED_ES_PARAMS_TOOL],
            tool_choice={"type": "tool", "name": "generate_unified_es_params"},
            temperature=0,
            max_tokens=2000,
        )
        tool_use = pick_tool(resp, "generate_unified_es_params")
        if not tool_use:
            return {}

        params: Dict[str, Any] = tool_use.input or {}

        # Minimal post-processing (schema handles most validation)
        anchor = str(params.get("anchor_product_noun") or "").strip()
        if anchor:
            params["q"] = anchor
        if isinstance(params.get("dietary_terms"), list):
            params["dietary_terms"] = [
                str(x).strip().upper()
                for x in params["dietary_terms"]
                if str(x).strip()
            ]

        # Clamp size
        try:
            s = int(ctx.session.get("size_hint", 20) or 20)
            params["size"] = max(1, min(50, s))
        except Exception:
            params["size"] = 20

        # De-genericize anchor using category_paths or history (2025 enhancement)
        anchor_lower = anchor.lower()
        if anchor_lower in GENERIC_ANCHORS:
            # Strategy 1: Derive 2-3 nouns from category_paths for broader search surface
            # Enforce taxonomy: strip full prefix to L2/L3 and reject unknowns
            fnb_tax = self._get_fnb_taxonomy_hierarchical()
            cat_paths = params.get("category_paths") or []
            refined_nouns = []
            if isinstance(cat_paths, list) and cat_paths:
                for cp in cat_paths[:3]:  # Take up to 3 paths
                    # Strip full prefix if present
                    cp_str = str(cp)
                    rel_path = cp_str.replace("f_and_b/food/", "").replace("f_and_b/beverages/", "").replace("personal_care/", "")
                    # Validate against taxonomy (L2 or L2/L3)
                    valid = False
                    parts = [p for p in rel_path.split("/") if p]
                    if len(parts) == 1:
                        l2 = parts[0]
                        if l2 in (fnb_tax.get("food", {}) | fnb_tax.get("beverages", {})):
                            valid = True
                    elif len(parts) == 2:
                        l2, l3 = parts
                        valid = (
                            l3 in (fnb_tax.get("food", {}).get(l2, []) + fnb_tax.get("beverages", {}).get(l2, []))
                        )
                    if not valid:
                        continue
                    if rel_path in CATEGORY_PATH_TO_NOUNS:
                        noun = CATEGORY_PATH_TO_NOUNS[rel_path]
                        if noun not in refined_nouns:  # Avoid duplicates
                            refined_nouns.append(noun)
            
            # Strategy 2: Carry-over from history if topic unchanged
            if not refined_nouns and is_follow_up and convo_history:
                try:
                    last_params = (session.get("debug", {}) or {}).get("last_search_params", {}) or {}
                    last_anchor = str(last_params.get("anchor_product_noun") or "").strip()
                    if last_anchor and last_anchor.lower() not in GENERIC_ANCHORS:
                        refined_nouns.append(last_anchor)
                except Exception:
                    pass
            
            if refined_nouns:
                # Use first as anchor_product_noun, all in q for broader search
                params["anchor_product_noun"] = refined_nouns[0]
                params["q"] = ", ".join(refined_nouns[:3])  # Max 3 for search surface

        # Optional: Only show 2nd LLM outputs when explicitly requested
        import os
        if os.getenv("ONLY_LLM2_OUTPUTS", "false").lower() in {"1", "true", "yes", "on"}:
            try:
                print(params)
            except Exception:
                pass

        # Persist unified and last_search_params snapshot into session for next turn reuse
        try:
            dbg = ctx.session.setdefault("debug", {})
            # Full unified snapshot
            dbg["unified_es_params"] = dict(params)
            # Curated last_search_params used by downstream and follow-up deltas
            safe_keys = [
                "q",
                "anchor_product_noun",
                "category_group",
                "category_paths",
                "price_min",
                "price_max",
                "brands",
                "dietary_terms",
                "keywords",
                "must_keywords",
                "size",
            ]
            dbg["last_search_params"] = {k: params.get(k) for k in safe_keys if k in params}
            dbg["last_params_updated_at"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"

            # FIX: Smart merge logic - preserve user-provided values, merge with LLM suggestions
            assessment = session.get("assessment", {}) or {}
            user_provided_slots = assessment.get("user_provided_slots", [])
            
            # Promote important fields to session-level slots for broader access
            if params.get("category_group"):
                ctx.session["category_group"] = params.get("category_group")
            if params.get("category_paths"):
                ctx.session["category_paths"] = params.get("category_paths")
                try:
                    cp_list = params.get("category_paths") or []
                    if isinstance(cp_list, list) and cp_list:
                        ctx.session["category_path"] = str(cp_list[0])
                except Exception:
                    pass
            if params.get("brands"):
                ctx.session["brands"] = params.get("brands")
            
            # FIX: Merge dietary_terms (don't overwrite user-provided)
            if params.get("dietary_terms"):
                llm_dietary = params.get("dietary_terms", [])
                if "ASK_DIETARY_REQUIREMENTS" in user_provided_slots:
                    # User explicitly provided dietary - merge with LLM suggestions
                    user_dietary = ctx.session.get("dietary_requirements", [])
                    if isinstance(user_dietary, str):
                        user_dietary = [user_dietary]
                    if not isinstance(user_dietary, list):
                        user_dietary = []
                    # Union merge (deduplicate)
                    merged_dietary = list(set(user_dietary + llm_dietary))
                    ctx.session["dietary_requirements"] = merged_dietary
                else:
                    # No user input - just use LLM suggestions
                    ctx.session["dietary_requirements"] = llm_dietary
            
            if params.get("price_min") is not None:
                ctx.session["price_min"] = params.get("price_min")
            if params.get("price_max") is not None:
                ctx.session["price_max"] = params.get("price_max")
            if params.get("size"):
                ctx.session["size_hint"] = int(params.get("size") or 20)
        except Exception:
            pass

        return params

    def _build_optimized_prompt(
        self,
        *,
        current_text: str,
        history: list[dict[str, Any]],
        is_follow_up: bool,
        product_intent: str,
        slots: dict[str, Any]
    ) -> str:
        """Build optimized prompt using 2025 best practices."""
        import json
        history_json = json.dumps(history, ensure_ascii=False, indent=2)
        slots_json = json.dumps({k: v for k, v in slots.items() if v}, ensure_ascii=False)

        return (
            "<task>\n"
            "Extract Elasticsearch parameters from user query while maintaining product continuity across conversation turns.\n"
            "</task>\n\n"
            "<context>\n"
            f"<conversation_mode>{'FOLLOW_UP' if is_follow_up else 'NEW_QUERY'}</conversation_mode>\n"
            f"<current_query>{current_text}</current_query>\n"
            f"<history>{history_json if history else '[]'}</history>\n"
            f"<intent>{product_intent or 'show_me_options'}</intent>\n"
            f"<user_preferences>{slots_json}</user_preferences>\n"
            "</context>\n\n"

            "<reasoning_steps>\n"
            "1. IDENTIFY ANCHOR: Extract product noun from current OR most recent history\n"
            "2. DETECT MODE: Is current a modifier (flavor/price/attribute) or new product?\n"
            "3. COMPOSE ANCHOR: If modifier, combine with historical anchor; else use current\n"
            "4. EXTRACT FILTERS: Separate dietary/price/brand from anchor\n"
            "5. MAP TAXONOMY: Assign category_group and paths\n"

            "</reasoning_steps>\n\n"
            "<critical_rules>\n"
            "<rule priority=\"1\">\n"

            "ANCHOR COMPOSITION\n"
            "- Modifier + History → combine: \"banana\" + \"chips\" = \"banana chips\"\n"
            "- New Product → replace: \"pasta\" after \"chips\" = \"pasta\"\n"
            "- Modifiers: flavors (banana, tomato), attributes (baked, crunchy), concerns (dry scalp)\n"
            "- New Products: different categories or explicit requests\n"
            "</rule>\n\n"
            "<rule priority=\"2\">\n"
            "FIELD SEPARATION (extract to separate fields, NOT anchor)\n"
            "✓ EXTRACT: prices, dietary terms, brands, generic attributes\n"
            "✗ KEEP IN ANCHOR: product noun, flavors, specific concerns\n\n"
            "Examples:\n"
            "- \"gluten free chips under 100\" → anchor:\"chips\" + dietary:[\"GLUTEN FREE\"] + price_max:100\n"
            "- \"Lays banana chips\" → anchor:\"banana chips\" + brands:[\"Lays\"]\n"
            "- \"baked chips\" → anchor:\"baked chips\" (baked is product-specific)\n"
            "</rule>\n\n"
            "<rule priority=\"3\">\n"
            "CATEGORY MAPPING\n"
            "- f_and_b: food, snacks, beverages, condiments\n"
            "- personal_care: skincare, haircare, oral care, body care\n"

            "</rule>\n\n"
            "<rule priority=\"4\">\n"
            "DIETARY NORMALIZATION\n"
            "Map to UPPERCASE enum values:\n"
            "- \"no palm oil\", \"palm free\" → [\"PALM OIL FREE\"]\n"
            "- \"gluten free\", \"no gluten\" → [\"GLUTEN FREE\"]\n"
            "- \"sugar free\", \"no sugar\" → [\"SUGAR FREE\"]\n"
            "- \"low sodium\", \"less salt\" → [\"LOW SODIUM\"]\n"
            "</rule>\n\n"
            "<rule priority=\"5\">\n"
            "GENERIC ANCHOR REFINEMENT (CRITICAL FOR SEARCH QUALITY)\n"
            "If anchor is generic (snacks, treats, items, something) BUT category_paths is specific:\n"
            "→ DERIVE 2-3 concrete nouns from category_paths for broader search surface\n\n"
            "Path-to-Noun Examples:\n"
            "- light_bites/savory_namkeen → \"namkeen\"\n"
            "- biscuits_and_crackers/cookies → \"cookies\"\n"
            "- sweet_treats/chocolates → \"chocolates\"\n"
            "- breakfast_essentials/breakfast_cereals → \"cereals\"\n\n"
            "Multi-Path Strategy (IMPORTANT):\n"
            "- When multiple category_paths present → derive noun from EACH path\n"
            "- Example: [savory_namkeen, cookies] → use 2-3 nouns for better coverage\n"
            "- Post-processing will join them: q = \"namkeen, cookies, biscuits\"\n\n"
            "Context-Based Queries (chai, evening, breakfast):\n"
            "- \"with chai\" + [savory_namkeen, cookies] → anchor: \"namkeen\" + q: \"namkeen, cookies\"\n"
            "- \"evening snacks\" + [chips, popcorn] → anchor: \"chips\" + q: \"chips, popcorn\"\n"
            "- \"breakfast\" + [cereals, oats] → anchor: \"cereals\" + q: \"cereals, oats\"\n\n"
            "Carry-Over Strategy:\n"
            "- If follow-up AND history has concrete anchor AND topic unchanged → reuse history anchor\n"
            "- Example: History: \"chips\" → Current: \"something under 100\" → Keep: \"chips\"\n"
            "</rule>\n\n"
            "<rule priority=\"6\">\n"
            "PROACTIVE HEALTH-ORIENTED DIETARY SUGGESTIONS (PRODUCT VISION)\n"
            "Our mission: Surface healthier alternatives by default.\n"
            "When user hasn't specified dietary constraints, intelligently suggest 1-2 health-relevant terms based on product category.\n\n"
            "Category-Specific Health Priorities:\n"
            "- Chips/Namkeen/Savory Snacks → [\"LOW SODIUM\", \"PALM OIL FREE\"] or [\"BAKED\"] (use keywords for baked)\n"
            "- Chocolates/Candies/Sweets → [\"LOW SUGAR\"] or [\"ORGANIC\"]\n"
            "- Juice/Beverages → [\"NO ADDED SUGAR\"]\n"
            "- Noodles/Pasta → [\"LOW SODIUM\"] (also consider \"whole wheat\" in keywords)\n"
            "- Biscuits/Cookies → [\"LOW SUGAR\", \"DIGESTIVE\"] (digestive as keyword)\n"
            "- Bread/Bakery → Use keywords [\"whole grain\", \"multigrain\"] instead of dietary_terms\n"
            "- Dairy (Butter/Cheese) → [\"LOW FAT\"] or [\"ORGANIC\"]\n\n"
            "Decision Logic:\n"
            "1. Check user_preferences for existing dietary constraints\n"
            "2. If user explicitly mentioned dietary needs → USE THOSE (don't override)\n"
            "3. If user said \"healthy\"/\"healthier\" → amplify with 2 suggestions\n"
            "4. If no user dietary specified → suggest 1-2 common health-oriented terms\n"
            "5. Never suggest more than 2 terms to avoid over-restriction\n\n"
            "Smart Suggestions:\n"
            "- \"chips\" → [\"LOW SODIUM\"] (most common health concern)\n"
            "- \"juice\" → [\"NO ADDED SUGAR\"] (critical for beverages)\n"
            "- \"chocolates\" + \"healthy\" keyword → [\"LOW SUGAR\", \"ORGANIC\"]\n"
            "- \"namkeen\" → [\"PALM OIL FREE\"] (common request in Indian market)\n\n"
            "DON'T Suggest When:\n"
            "- User already specified dietary (e.g., \"vegan chips\" → keep [\"VEGAN\"], don't add sodium)\n"
            "- Product intent is brand-specific (\"Lays chips\" → user wants specific brand taste)\n"
            "- Query is very generic without health context (\"evening snacks\" → maybe suggest 1, not 2)\n"
            "</rule>\n\n"
            "<rule priority=\"7\">\n"
            "KEYWORD EXTRACTION (Hard Filters vs Soft Reranking)\n"
            "CRITICAL: Generate keywords and must_keywords for EVERY query. Never leave both empty.\n\n"
            "must_keywords (Hard Filters - ES MUST clauses):\n"
            "- PURPOSE: Enforce exact flavor/variant/type; filter out non-matches\n"
            "- ES BEHAVIOR: Products WITHOUT these tokens are EXCLUDED from results\n"
            "- WHEN TO USE:\n"
            "  • Flavor modifiers: banana, tomato, mango, orange, peri peri, masala\n"
            "  • Critical variants: dark/milk/white (chocolate), hakka/schezwan (noodles)\n"
            "  • Specific types: whole wheat, basmati, jasmine\n"
            "- MAX: 3 tokens\n"
            "- EXTRACTION: From current query OR anchor_product_noun\n\n"
            "keywords (Soft Reranking - ES SHOULD clauses):\n"
            "- PURPOSE: Boost products with attributes; don't exclude others\n"
            "- ES BEHAVIOR: Products WITH these rank higher; products without still show\n"
            "- WHEN TO USE:\n"
            "  • Textural attributes: crispy, crunchy, soft, smooth, creamy\n"
            "  • Quality signals: premium, artisanal, fresh, natural\n"
            "  • Preparation methods: baked, roasted, fried, grilled\n"
            "  • Health attributes: light, wholesome, multigrain, cold-pressed\n"
            "- MAX: 4 tokens\n"
            "- EXTRACTION: From query OR infer common attributes for category\n\n"
            "Category-Specific Extraction Guide:\n"
            "1. Chips/Namkeen/Savory:\n"
            "   - must: banana, tomato, peri peri, masala, garlic, onion, pudina, jeera\n"
            "   - keywords: baked, crispy, crunchy, light, roasted, multigrain\n\n"
            "2. Chocolates/Candies/Sweets:\n"
            "   - must: dark, milk, white, hazelnut, almond, orange, mint\n"
            "   - keywords: premium, artisanal, smooth, rich, Belgian, sugar-free\n\n"
            "3. Juice/Beverages:\n"
            "   - must: orange, mango, apple, mixed fruit, pomegranate, grape\n"
            "   - keywords: fresh, cold-pressed, pulpy, no-pulp, natural, organic\n\n"
            "4. Noodles/Pasta:\n"
            "   - must: hakka, schezwan, penne, fusilli, macaroni, spaghetti\n"
            "   - keywords: whole wheat, instant, masala, ready-to-cook\n\n"
            "5. Biscuits/Cookies:\n"
            "   - must: chocolate chip, butter, oat, coconut, digestive\n"
            "   - keywords: crunchy, soft, cream-filled, sugar-free\n\n"
            "6. Ketchup/Sauces:\n"
            "   - must: tomato, chilli, garlic, mint, tamarind\n"
            "   - keywords: no-onion, jain, tangy, sweet, spicy\n\n"
            "Decision Flowchart:\n"
            "STEP 1: Extract explicit tokens from current query\n"
            "STEP 2: Classify each token:\n"
            "  → Flavor/variant/fruit/spice → must_keywords\n"
            "  → Texture/quality/method → keywords\n"
            "STEP 3: If NO explicit tokens:\n"
            "  → Infer 1-2 common keywords for category (never leave empty)\n"
            "  → Example: \"chips\" → keywords: [\"crunchy\"] (common expectation)\n"
            "  → Example: \"juice\" → keywords: [\"fresh\"] (quality signal)\n"
            "STEP 4: If health context (\"healthy\", \"light\"):\n"
            "  → Amplify keywords with health attributes\n"
            "  → Example: \"healthy chips\" → keywords: [\"baked\", \"light\"]\n"
            "STEP 5: Dedup against anchor_product_noun\n"
            "STEP 6: Validate counts (must≤3, keywords≤4)\n\n"
            "Override & Edge Cases:\n"
            "- User says \"plain chips\" → must_keywords: [], keywords: [] (respect 'plain')\n"
            "- Brand-specific → reduce keyword suggestions (user wants brand taste)\n"
            "- \"baked tomato chips\" → must: [\"tomato\"], keywords: [\"baked\"] (classify correctly)\n"
            "- Flavor already in anchor → don't duplicate in must_keywords\n"
            "</rule>\n"
            "</critical_rules>\n\n"
            "<examples>\n"
            "<example type=\"new_query\">\n"
            "<input>\n"
            "current: \"chips\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"dietary_terms\": [\"LOW SODIUM\"],\n"
            "  \"keywords\": [\"crunchy\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"New chips query; suggesting LOW SODIUM + inferred 'crunchy' keyword for common expectation\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"follow_up_flavor\">\n"
            "<input>\n"
            "current: \"banana\"\n"
            "history: [{recency:\"MOST_RECENT\", user:\"chips\", bot_summary:\"Showing chips...\"}]\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"banana chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"must_keywords\": [\"banana\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Follow-up: flavor modifier 'banana' combined with anchor 'chips'\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"follow_up_constraints\">\n"
            "<input>\n"
            "current: \"under 100 gluten free\"\n"
            "history: [{recency:\"MOST_RECENT\", user:\"noodles\", bot_summary:\"Showing noodles...\"}]\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"noodles\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/noodles_and_vermicelli/vermicelli_and_noodles\"],\n"
            "  \"dietary_terms\": [\"GLUTEN FREE\"],\n"
            "  \"keywords\": [\"instant\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"price_max\": 100,\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Follow-up: applying dietary + price filters; inferred 'instant' keyword for noodles category\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"category_switch\">\n"
            "<input>\n"
            "current: \"show me pasta\"\n"
            "history: [{recency:\"RECENT\", user:\"chips under 100\", bot_summary:\"Showed chips...\"}]\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"pasta\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/packaged_meals/pasta_and_soups\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"New product: 'pasta' replaces previous 'chips' anchor\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"personal_care\">\n"
            "<input>\n"
            "current: \"dry scalp\"\n"
            "history: [{recency:\"MOST_RECENT\", user:\"shampoo\", bot_summary:\"Showing shampoos...\"}]\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"dry scalp shampoo\",\n"
            "  \"category_group\": \"personal_care\",\n"
            "  \"category_paths\": [\"personal_care/hair/shampoo\"],\n"
            "  \"keywords\": [\"moisturizing\", \"anti-dandruff\"],\n"
            "  \"size\": 15,\n"
            "  \"reasoning\": \"Follow-up: concern 'dry scalp' combined with anchor 'shampoo'\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"generic_to_concrete_path\">\n"
            "<input>\n"
            "current: \"want sweet treats\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"chocolates\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/sweet_treats/chocolates\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Refined generic 'sweet treats' to concrete 'chocolates' based on category_paths\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"context_based_chai\">\n"
            "<input>\n"
            "current: \"want something to eat with chai\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"namkeen\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/savory_namkeen\", \"f_and_b/food/biscuits_and_crackers/cookies\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Context 'with chai' mapped to multiple paths; using 2-3 concrete nouns for broader search surface\"\n"
            "}\n"
            "</output>\n"
            "<note>Post-processing will expand q to 'namkeen, cookies' for better coverage</note>\n"
            "</example>\n\n"
            "<example type=\"carry_over_concrete\">\n"
            "<input>\n"
            "current: \"something under 100\"\n"
            "history: [{recency:\"MOST_RECENT\", user:\"chips\", bot_summary:\"Showed chips results\"}]\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"price_max\": 100,\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Follow-up: carried over concrete anchor 'chips' from history since topic unchanged\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"health_aware_chips\">\n"
            "<input>\n"
            "current: \"chips\"\n"
            "history: []\n"
            "user_preferences: {}\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"dietary_terms\": [\"LOW SODIUM\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"New query for chips; proactively suggesting LOW SODIUM for healthier alternatives\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"health_aware_juice\">\n"
            "<input>\n"
            "current: \"fruit juice\"\n"
            "history: []\n"
            "user_preferences: {}\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"juice\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/refreshing_beverages/fruit_juices\"],\n"
            "  \"dietary_terms\": [\"NO ADDED SUGAR\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Juice query; suggesting NO ADDED SUGAR for healthier beverage options\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"health_aware_with_user_context\">\n"
            "<input>\n"
            "current: \"want healthy chocolates\"\n"
            "history: []\n"
            "user_preferences: {}\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"chocolates\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/sweet_treats/chocolates\"],\n"
            "  \"dietary_terms\": [\"LOW SUGAR\", \"ORGANIC\"],\n"
            "  \"keywords\": [\"dark\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Health-focused query; suggesting LOW SUGAR + ORGANIC + 'dark' keyword for healthier chocolate options\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"respect_user_explicit\">\n"
            "<input>\n"
            "current: \"vegan chips\"\n"
            "history: []\n"
            "user_preferences: {}\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"dietary_terms\": [\"VEGAN\"],\n"
            "  \"keywords\": [\"crunchy\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"User explicitly requested vegan; respecting intent + inferred 'crunchy' for quality boost\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"flavor_extraction_must\">\n"
            "<input>\n"
            "current: \"tomato ketchup\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"ketchup\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/spreads_and_condiments/ketchup_and_sauces\"],\n"
            "  \"dietary_terms\": [],\n"
            "  \"keywords\": [\"tangy\"],\n"
            "  \"must_keywords\": [\"tomato\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Tomato variant required (must_keywords); 'tangy' for quality ranking (keywords)\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"attribute_extraction_soft\">\n"
            "<input>\n"
            "current: \"baked chips\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"baked chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"dietary_terms\": [\"LOW SODIUM\"],\n"
            "  \"keywords\": [\"baked\", \"light\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Baked is preparation method (soft keyword); LOW SODIUM for health; 'light' inferred\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"combined_hard_soft\">\n"
            "<input>\n"
            "current: \"crispy banana chips\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"banana chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"dietary_terms\": [],\n"
            "  \"keywords\": [\"crispy\"],\n"
            "  \"must_keywords\": [\"banana\"],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Banana is flavor variant (must filter); crispy is texture (soft ranking boost)\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<example type=\"health_context_keywords\">\n"
            "<input>\n"
            "current: \"healthy juice\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"juice\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/refreshing_beverages/fruit_juices\"],\n"
            "  \"dietary_terms\": [\"NO ADDED SUGAR\"],\n"
            "  \"keywords\": [\"fresh\", \"natural\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"size\": 20,\n"
            "  \"reasoning\": \"Health-focused juice query; NO ADDED SUGAR + fresh/natural keywords for quality ranking\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            "<contrastive_examples>\n"
            "❌ INCORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"gluten free noodles under 100\",\n"
            "  \"dietary_terms\": [],\n"
            "  \"price_max\": null\n"
            "}\n"
            "Problem: Constraints in anchor instead of separate fields\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"noodles\",\n"
            "  \"dietary_terms\": [\"GLUTEN FREE\"],\n"
            "  \"price_max\": 100\n"
            "}\n\n"
            "❌ INCORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_group\": \"snacks\"\n"
            "}\n"
            "Problem: Invalid category_group (must use enum)\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_group\": \"f_and_b\"\n"
            "}\n\n"
            "❌ INCORRECT (Generic Anchor with Specific Paths):\n"
            "{\n"
            "  \"anchor_product_noun\": \"sweet treats\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/sweet_treats/chocolates\"]\n"
            "}\n"
            "Problem: Generic anchor when category_paths clearly indicates \"chocolates\"\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chocolates\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/sweet_treats/chocolates\"]\n"
            "}\n\n"
            "❌ INCORRECT (Context Query with Generic Anchor):\n"
            "{\n"
            "  \"anchor_product_noun\": \"snacks\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/savory_namkeen\"]\n"
            "}\n"
            "Problem: Should derive \"namkeen\" from the specific category_path\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"namkeen\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/savory_namkeen\"]\n"
            "}\n\n"
            "❌ INCORRECT (Missed Carry-Over):\n"
            "{\n"
            "  \"anchor_product_noun\": \"items\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"price_max\": 50\n"
            "}\n"
            "Context: Follow-up after user searched \"chips\"\n"
            "Problem: Should reuse concrete \"chips\" from history\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_paths\": [\"f_and_b/food/light_bites/chips_and_crisps\"],\n"
            "  \"price_max\": 50\n"
            "}\n\n"
            "❌ INCORRECT (Missed Health Suggestion):\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"dietary_terms\": [],\n"
            "  \"keywords\": []\n"
            "}\n"
            "Problem: No health-oriented suggestion when user hasn't specified dietary needs\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"category_group\": \"f_and_b\",\n"
            "  \"dietary_terms\": [\"LOW SODIUM\"],\n"
            "  \"reasoning\": \"Proactively suggesting LOW SODIUM for healthier chips\"\n"
            "}\n\n"
            "❌ INCORRECT (Over-Suggesting):\n"
            "{\n"
            "  \"anchor_product_noun\": \"chocolates\",\n"
            "  \"dietary_terms\": [\"LOW SUGAR\", \"ORGANIC\", \"VEGAN\", \"GLUTEN FREE\"],\n"
            "}\n"
            "Problem: Too many suggestions (max 2 unless user explicitly requests)\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chocolates\",\n"
            "  \"dietary_terms\": [\"LOW SUGAR\"],\n"
            "  \"reasoning\": \"Suggesting LOW SUGAR as primary health concern for chocolates\"\n"
            "}\n\n"
            "❌ INCORRECT (Overriding User Intent):\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"dietary_terms\": [\"VEGAN\", \"LOW SODIUM\"],\n"
            "}\n"
            "Context: User said \"vegan chips\"\n"
            "Problem: Added LOW SODIUM when user only asked for vegan\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"dietary_terms\": [\"VEGAN\"],\n"
            "  \"reasoning\": \"Respecting user's explicit vegan requirement\"\n"
            "}\n\n"
            "❌ INCORRECT (Brand-Specific with Health Override):\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"brands\": [\"Lays\"],\n"
            "  \"dietary_terms\": [\"LOW SODIUM\"]\n"
            "}\n"
            "Context: User said \"Lays chips\"\n"
            "Problem: User wants specific brand taste; don't restrict with health filters\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"brands\": [\"Lays\"],\n"
            "  \"dietary_terms\": [],\n"
            "  \"keywords\": [],\n"
            "  \"must_keywords\": [],\n"
            "  \"reasoning\": \"Brand-specific query; respecting user's brand preference\"\n"
            "}\n\n"
            "❌ INCORRECT (Empty Keywords):\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"keywords\": [],\n"
            "  \"must_keywords\": []\n"
            "}\n"
            "Problem: No keywords generated when common attributes exist for category\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"keywords\": [\"crunchy\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"reasoning\": \"Inferred 'crunchy' as common quality attribute for chips\"\n"
            "}\n\n"
            "❌ INCORRECT (Flavor in Soft Keywords):\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"keywords\": [\"banana\"],\n"
            "  \"must_keywords\": []\n"
            "}\n"
            "Problem: Flavor should be must_keywords (hard filter), not soft keyword\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"banana chips\",\n"
            "  \"keywords\": [\"crunchy\"],\n"
            "  \"must_keywords\": [\"banana\"],\n"
            "  \"reasoning\": \"Banana is critical variant (must); crunchy is texture (soft boost)\"\n"
            "}\n\n"
            "❌ INCORRECT (Attribute in Hard Filter):\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"must_keywords\": [\"crispy\", \"crunchy\"],\n"
            "  \"keywords\": []\n"
            "}\n"
            "Problem: Texture attributes too restrictive as must; should be soft keywords\n\n"
            "✓ CORRECT:\n"
            "{\n"
            "  \"anchor_product_noun\": \"chips\",\n"
            "  \"must_keywords\": [],\n"
            "  \"keywords\": [\"crispy\", \"crunchy\"],\n"
            "  \"reasoning\": \"Texture attributes as soft keywords for ranking boost\"\n"
            "}\n"
            "</contrastive_examples>\n"
            "</examples>\n\n"
            "<output_instructions>\n"

            "Generate the tool call with:\n"
            "1. anchor_product_noun: Clean, CONCRETE product phrase (2-6 words)\n"
            "   - If generic (snacks/treats/items) → derive from category_paths or history\n"
            "   - NEVER return generic anchor when category_paths is specific\n"
            "2. category_group: ONE of the enum values\n"
            "3. category_paths: Specific paths that match the anchor\n"
            "4. dietary_terms: THINK HEALTH-FIRST\n"
            "   - If user hasn't specified → suggest 1-2 category-appropriate health terms\n"
            "   - If user explicit (vegan, gluten free) → use ONLY user's terms\n"
            "   - If user said \"healthy\" → amplify with 2 suggestions\n"
            "   - If brand-specific query → NO health suggestions\n"
            "   - MAX 2 suggestions to avoid over-restriction\n"
            "5. must_keywords: EXTRACT FLAVOR/VARIANT TOKENS (MANDATORY STEP)\n"
            "   - From query: banana, tomato, peri peri, dark, orange, etc.\n"
            "   - From anchor if flavor embedded: \"banana chips\" → [\"banana\"]\n"
            "   - If NO flavors/variants present → leave empty (don't force)\n"
            "   - MAX 3 tokens\n"
            "6. keywords: INFER QUALITY ATTRIBUTES (NEVER LEAVE EMPTY)\n"
            "   - Explicit from query: crispy, baked, fresh, premium\n"
            "   - Inferred for category: chips→\"crunchy\", juice→\"fresh\", chocolates→\"smooth\"\n"
            "   - ALWAYS generate at least 1 keyword per query\n"
            "   - MAX 4 tokens\n"
            "7. Other filters: price_min/max, brands\n"
            "8. reasoning: One sentence explaining anchor + dietary + keyword decisions\n"
            "9. Validate against examples before finalizing\n\n"
            "Remember:\n"
            "- Anchor MUST be searchable, concrete noun (chips, not snacks)\n"
            "- HEALTH-FIRST: Suggest relevant dietary terms by default\n"
            "- KEYWORDS MANDATORY: Always generate at least 1 keyword (never leave empty)\n"
            "- CLASSIFY CORRECTLY: Flavors→must_keywords, Attributes→keywords\n"
            "- Extract constraints to separate fields\n"
            "- Use MOST_RECENT history for follow-ups\n"
            "- Refine generic anchors using category_paths\n"
            "- Carry-over concrete anchors when topic unchanged\n"
            "- Default to f_and_b when uncertain\n"
            "</output_instructions>\n"
        )

    # ============================================================================
    # PERSONAL CARE 2025 METHODS (Parallel to Food Path)
    # ============================================================================

    async def _generate_personal_care_es_params_2025(self, ctx: UserContext, current_text: str) -> Dict[str, Any]:
        """2025 best-practices for Personal Care: schema-first, optimized prompt, forced tool, minimal post-process."""
        session = ctx.session or {}
        
        # Determine follow-up
        is_follow_up = bool(session.get("assessment", {}))
        
        # Build recency-weighted history
        hist_limit = 10 if is_follow_up else 5
        history = self._build_last_interactions(ctx, limit=hist_limit)
        history_json = json.dumps(history, ensure_ascii=False)
        
        # Profile hints for personal care
        profile_hints = {
            "known_skin_type": session.get("user_skin_type"),
            "known_hair_type": session.get("user_hair_type"),
            "known_skin_concerns": session.get("user_skin_concerns", []),
            "known_hair_concerns": session.get("user_hair_concerns", []),
            "known_allergies": session.get("user_allergies", []),
            "preferences": session.get("preferences"),
        }
        
        # Build optimized prompt
        prompt = self._build_personal_care_optimized_prompt(
            current_text=current_text,
            is_follow_up=is_follow_up,
            history_json=history_json,
            profile_hints=profile_hints,
        )
        
        # Force tool call
        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[PERSONAL_CARE_ES_PARAMS_TOOL_2025],
            tool_choice={"type": "tool", "name": "generate_personal_care_es_params"},
            temperature=0,
            max_tokens=2000,
        )
        
        tool_use = pick_tool(resp, "generate_personal_care_es_params")
        params = _strip_keys(tool_use.input or {}) if tool_use else {}
        
        # Minimal normalization
        try:
            params["category_group"] = "personal_care"
            
            # Map anchor_product_noun to q
            anchor = str(params.get("anchor_product_noun") or "").strip()
            if anchor and not params.get("q"):
                params["q"] = anchor
            
            # Clamp size (max 15 for personal care)
            try:
                s = int(params.get("size", 10) or 10)
                params["size"] = max(1, min(15, s))
            except Exception:
                params["size"] = 10
            
            # Clean list fields
            for lf in [
                "brands", "skin_types", "hair_types", "efficacy_terms", "avoid_terms",
                "avoid_ingredients", "keywords", "must_keywords", "product_types",
                "skin_concerns", "hair_concerns", "category_paths"
            ]:
                if isinstance(params.get(lf), list):
                    params[lf] = [str(x).strip() for x in params[lf] if str(x).strip()]
            
            # Ensure mandatory arrays exist (per schema requirements)
            if not isinstance(params.get("efficacy_terms"), list) or not params.get("efficacy_terms"):
                params["efficacy_terms"] = ["hydration"]
            if not isinstance(params.get("keywords"), list) or not params.get("keywords"):
                params["keywords"] = ["gentle"]
            if not isinstance(params.get("must_keywords"), list):
                params["must_keywords"] = []
            if not isinstance(params.get("category_paths"), list) or not params.get("category_paths"):
                params["category_paths"] = ["personal_care/skin/moisturizers"]
        except Exception:
            pass
        
        # Persistence to session (inspired by food path)
        try:
            dbg = session.setdefault("debug", {})
            dbg["personal_care_es_params_raw"] = params
            dbg["last_skin_search_params"] = {
                k: params.get(k)
                for k in [
                    "q", "anchor_product_noun", "category_group", "category_paths",
                    "price_min", "price_max", "brands", "skin_types", "hair_types",
                    "efficacy_terms", "avoid_terms", "avoid_ingredients", "keywords",
                    "must_keywords", "product_types", "skin_concerns", "hair_concerns", "size"
                ]
            }
            import datetime
            dbg["last_skin_params_updated_at"] = datetime.datetime.utcnow().isoformat()
            
            # Promote to slots (merge logic for follow-ups handled by caller)
            session["category_group"] = "personal_care"
            if params.get("category_paths"):
                session["category_paths"] = params.get("category_paths")
                session["category_path"] = (params.get("category_paths") or [None])[0]
            session["brands"] = params.get("brands") or []
            session["price_min"] = params.get("price_min")
            session["price_max"] = params.get("price_max")
            
            # PC-specific slots
            session["skin_types_slot"] = params.get("skin_types") or []
            session["hair_types_slot"] = params.get("hair_types") or []
            session["efficacy_terms_slot"] = params.get("efficacy_terms") or []
            session["avoid_terms_slot"] = params.get("avoid_terms") or []
            session["pc_keywords_slot"] = params.get("keywords") or []
            session["pc_must_keywords_slot"] = params.get("must_keywords") or []
            
            ctx.session = session
        except Exception:
            pass
        
        # Essential logging (clean, structured)
        try:
            import os
            show_logs = os.getenv("ONLY_LLM2_OUTPUTS", "false").lower() not in {"1", "true", "yes", "on"}
            if show_logs or True:  # Always show PC LLM outputs
                print(
                    f"CORE:PC_LLM2_OUT | q='{params.get('q')}' | anchor='{params.get('anchor_product_noun')}' | "
                    f"paths={params.get('category_paths')} | brands={params.get('brands')} | "
                    f"price=({params.get('price_min')},{params.get('price_max')}) | types={params.get('product_types')} | "
                    f"skin_types={params.get('skin_types')} | hair_types={params.get('hair_types')} | "
                    f"efficacy={params.get('efficacy_terms')} | avoid={params.get('avoid_terms')} | "
                    f"keywords={params.get('keywords')} | must={params.get('must_keywords')} | "
                    f"concerns_skin={params.get('skin_concerns')} | concerns_hair={params.get('hair_concerns')} | "
                    f"size={params.get('size')}"
                )
        except Exception:
            pass
        
        return params

    def _build_personal_care_optimized_prompt(
        self,
        *,
        current_text: str,
        is_follow_up: bool,
        history_json: str,
        profile_hints: Dict[str, Any],
    ) -> str:
        """Optimized 2025 prompt for Personal Care (parallels food path structure)."""
        
        profile_str = json.dumps(profile_hints, ensure_ascii=False)
        
        return (
            # Task definition
            "<task>\n"
            "Extract personal care product search parameters via the generate_personal_care_es_params tool.\n"
            "Goal: Convert natural language queries into structured Elasticsearch parameters for skin/hair products.\n"
            "</task>\n\n"
            
            # Context
            f"<context>\n"
            f"FOLLOW_UP: {is_follow_up}\n"
            f"HISTORY: {history_json}\n"
            f"PROFILE_HINTS: {profile_str}\n"
            f"</context>\n\n"
            
            f"<current_query>{current_text}</current_query>\n\n"
            
            # Reasoning steps
            "<reasoning_steps>\n"
            "1. Identify the product type (shampoo, face wash, moisturizer, serum, etc.)\n"
            "2. Detect skin/hair type signals (oily, dry, sensitive, curly, frizzy, etc.)\n"
            "3. Extract efficacy needs (anti-dandruff, hydration, brightening, anti-aging, etc.)\n"
            "4. Identify concerns (acne, pigmentation, hair fall, split ends, etc.)\n"
            "5. Detect clean-ingredient preferences (sulfate-free, paraben-free, fragrance-free, etc.)\n"
            "6. Extract quality attributes (keywords) and variants (must_keywords)\n"
            "7. Map to category_paths in personal_care taxonomy\n"
            "8. Handle follow-ups by merging with history context\n"
            "</reasoning_steps>\n\n"
            
            # Critical rules (7 priority rules like food path)
            "<critical_rules>\n"
            "PRIORITY 1 - Category Group:\n"
            "- ALWAYS set category_group='personal_care'\n"
            "- Never use f_and_b for personal care queries\n\n"
            
            "PRIORITY 2 - Anchor Product Noun:\n"
            "- Use concrete, searchable product nouns (shampoo, face wash, moisturizer, serum, body lotion)\n"
            "- For generic queries ('something for my dry skin'), infer 2-3 relevant products from context\n"
            "- Join multiple products with commas in q field: 'moisturizer, face cream, lotion'\n"
            "- Carry over concrete anchors from history if topic unchanged\n"
            "- NEVER use vague terms like 'product', 'item', 'thing'\n\n"
            
            "PRIORITY 3 - Category Paths:\n"
            "- Choose 1-2 specific paths from personal_care taxonomy\n"
            "- Examples: 'personal_care/hair/shampoo', 'personal_care/skin/moisturizers'\n"
            "- Align with anchor noun and detected concerns\n"
            "- For generic queries, select paths matching inferred products\n\n"
            
            "PRIORITY 4 - Skin/Hair Type Detection:\n"
            "- Explicit signals: 'oily skin' → [\"oily\"], 'dry hair' → [\"dry\"]\n"
            "- Implicit signals: 'T-zone shine' → [\"combination\"], 'flaky scalp' → [\"dry\"]\n"
            "- Problem-based inference: 'acne' → [\"oily\"], 'tight feeling' → [\"dry\", \"sensitive\"]\n"
            "- Use profile_hints as fallback, override with current query signals\n"
            "- Enums: skin_types=[oily, dry, combination, sensitive, normal]\n"
            "- Enums: hair_types=[dry, oily, normal, curly, straight, wavy, frizzy, thin, thick]\n\n"
            
            "PRIORITY 5 - Efficacy Terms (MANDATORY - NEVER EMPTY):\n"
            "- Extract 2-5 desired benefits: anti-dandruff, hydration, brightening, nourishment, repair\n"
            "- Map problems to efficacy: 'acne' → [\"acne control\"], 'dull skin' → [\"brightening\"]\n"
            "- Default suggestions by category:\n"
            "  * Shampoo: [\"hair care\", \"scalp health\"]\n"
            "  * Face wash: [\"cleansing\", \"gentle\"]\n"
            "  * Moisturizer: [\"hydration\", \"nourishment\"]\n"
            "  * Serum: [\"targeted treatment\", \"absorption\"]\n"
            "- ALWAYS provide at least 1 efficacy term (required by schema)\n\n"
            
            "PRIORITY 6 - Clean-Ingredient Awareness (avoid_terms):\n"
            "- Populate when user signals sensitivity, clean beauty, or natural preference\n"
            "- Common avoids: sulfates, parabens, silicones, mineral oil, fragrance, alcohol\n"
            "- Triggers: 'sensitive skin', 'chemical-free', 'natural', 'gentle', 'harsh'\n"
            "- Be conservative - don't force avoids unless signaled\n"
            "- Use enum values from schema\n\n"
            
            "PRIORITY 7 - Keywords & Must_Keywords (Mandatory):\n"
            "- keywords (soft filters for reranking): Quality attributes\n"
            "  * Explicit: gentle, nourishing, lightweight, rich, refreshing\n"
            "  * Inferred by category: shampoo→\"nourishing\", face wash→\"gentle\", serum→\"lightweight\"\n"
            "  * ALWAYS generate at least 1 keyword (required by schema)\n"
            "  * MAX 4 keywords\n"
            "- must_keywords (hard filters): Product variants or active ingredients\n"
            "  * Examples: rose water, tea tree, niacinamide, vitamin C, aloe vera, charcoal\n"
            "  * Only extract when explicitly mentioned or strongly implied\n"
            "  * If not present → leave empty\n"
            "  * MAX 3 must_keywords\n"
            "</critical_rules>\n\n"
            
            # Examples (positive)
            "<examples>\n"
            "<example type=\"explicit_shampoo\">\n"
            "<input>\n"
            "current: \"anti-dandruff shampoo for oily scalp\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"shampoo\",\n"
            "  \"category_group\": \"personal_care\",\n"
            "  \"category_paths\": [\"personal_care/hair/shampoo\"],\n"
            "  \"hair_types\": [\"oily\"],\n"
            "  \"efficacy_terms\": [\"anti-dandruff\", \"scalp care\", \"oil control\"],\n"
            "  \"keywords\": [\"cleansing\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"size\": 10,\n"
            "  \"reasoning\": \"Explicit shampoo for oily hair with anti-dandruff efficacy\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            
            "<example type=\"generic_dry_skin\">\n"
            "<input>\n"
            "current: \"something for my dry skin\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"moisturizer\",\n"
            "  \"q\": \"moisturizer, face cream, lotion\",\n"
            "  \"category_group\": \"personal_care\",\n"
            "  \"category_paths\": [\"personal_care/skin/moisturizers\", \"personal_care/skin/face_creams\"],\n"
            "  \"skin_types\": [\"dry\"],\n"
            "  \"efficacy_terms\": [\"hydration\", \"nourishment\"],\n"
            "  \"keywords\": [\"rich\", \"nourishing\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"size\": 10,\n"
            "  \"reasoning\": \"Generic query for dry skin; inferred 3 concrete products in q for better coverage\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            
            "<example type=\"clean_ingredient\">\n"
            "<input>\n"
            "current: \"gentle face wash without harsh chemicals\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"face wash\",\n"
            "  \"category_group\": \"personal_care\",\n"
            "  \"category_paths\": [\"personal_care/skin/face_wash\"],\n"
            "  \"skin_types\": [\"sensitive\"],\n"
            "  \"efficacy_terms\": [\"cleansing\", \"gentle care\"],\n"
            "  \"avoid_terms\": [\"sulfates\", \"harsh chemicals\", \"fragrance\"],\n"
            "  \"keywords\": [\"gentle\", \"mild\"],\n"
            "  \"must_keywords\": [],\n"
            "  \"size\": 10,\n"
            "  \"reasoning\": \"Gentle face wash with clean-ingredient focus (sulfate-free, fragrance-free)\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            
            "<example type=\"tea_tree_variant\">\n"
            "<input>\n"
            "current: \"tea tree shampoo for dandruff\"\n"
            "history: []\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"shampoo\",\n"
            "  \"category_group\": \"personal_care\",\n"
            "  \"category_paths\": [\"personal_care/hair/shampoo\"],\n"
            "  \"efficacy_terms\": [\"anti-dandruff\", \"scalp care\"],\n"
            "  \"keywords\": [\"cleansing\"],\n"
            "  \"must_keywords\": [\"tea tree\"],\n"
            "  \"size\": 10,\n"
            "  \"reasoning\": \"Explicit tea tree variant in must_keywords for hard filtering\"\n"
            "}\n"
            "</output>\n"
            "</example>\n\n"
            
            "<example type=\"followup_price\">\n"
            "<input>\n"
            "current: \"under 500\"\n"
            "history: [{\"recency\":\"MOST_RECENT\", \"user\":\"vitamin C serum\", \"bot_summary\":\"Showed serum results\"}]\n"
            "</input>\n"
            "<output>\n"
            "{\n"
            "  \"anchor_product_noun\": \"serum\",\n"
            "  \"category_group\": \"personal_care\",\n"
            "  \"category_paths\": [\"personal_care/skin/serums\"],\n"
            "  \"efficacy_terms\": [\"brightening\", \"targeted treatment\"],\n"
            "  \"keywords\": [\"lightweight\"],\n"
            "  \"must_keywords\": [\"vitamin c\"],\n"
            "  \"price_max\": 500,\n"
            "  \"size\": 10,\n"
            "  \"reasoning\": \"Follow-up: carried over vitamin C serum anchor with price constraint\"\n"
            "}\n"
            "</output>\n"
            "</example>\n"
            "</examples>\n\n"
            
            # Contrastive examples (what NOT to do)
            "<contrastive_examples>\n"
            "<example type=\"vague_anchor_BAD\">\n"
            "<input>current: \"something for acne\"</input>\n"
            "<output_BAD>{\"anchor_product_noun\": \"product\", ...}</output_BAD>\n"
            "<output_GOOD>{\"anchor_product_noun\": \"face wash\", \"q\": \"face wash, acne cream, spot treatment\", ...}</output_GOOD>\n"
            "<reason>Never use generic 'product'; infer concrete products from concern</reason>\n"
            "</example>\n\n"
            
            "<example type=\"empty_efficacy_BAD\">\n"
            "<input>current: \"shampoo\"</input>\n"
            "<output_BAD>{\"efficacy_terms\": [], ...}</output_BAD>\n"
            "<output_GOOD>{\"efficacy_terms\": [\"hair care\", \"cleansing\"], \"keywords\": [\"nourishing\"], ...}</output_GOOD>\n"
            "<reason>efficacy_terms and keywords are MANDATORY (schema requirement)</reason>\n"
            "</example>\n\n"
            
            "<example type=\"wrong_category_group_BAD\">\n"
            "<input>current: \"moisturizer\"</input>\n"
            "<output_BAD>{\"category_group\": \"f_and_b\", ...}</output_BAD>\n"
            "<output_GOOD>{\"category_group\": \"personal_care\", ...}</output_GOOD>\n"
            "<reason>Personal care products ALWAYS use 'personal_care' category_group</reason>\n"
            "</example>\n"
            "</contrastive_examples>\n\n"
            
            # Output instructions
            "<output_instructions>\n"
            "Return ONLY a tool call to generate_personal_care_es_params.\n"
            "Required fields:\n"
            "1. anchor_product_noun: Concrete product (2-6 words)\n"
            "2. category_group: 'personal_care'\n"
            "3. category_paths: 1-2 paths (e.g., 'personal_care/skin/moisturizers')\n"
            "4. efficacy_terms: 2-5 benefits (MANDATORY, never empty)\n"
            "5. keywords: 1-4 quality attributes (MANDATORY, never empty)\n"
            "6. must_keywords: 0-3 variants/actives (only when explicit)\n"
            "7. skin_types / hair_types: Detected from query or profile\n"
            "8. avoid_terms: Only when clean-ingredient signals present\n"
            "9. price_min/max, brands: Extract if mentioned\n"
            "10. size: Max 15 for personal care\n"
            "11. reasoning: One sentence explaining anchor + efficacy + clean-ingredient decisions\n\n"
            
            "Decision flowchart:\n"
            "1. Extract anchor noun → if generic, infer 2-3 products → join in q\n"
            "2. Detect skin/hair types → map signals to enums\n"
            "3. Extract efficacy (ALWAYS ≥1) → map problems to benefits\n"
            "4. Generate keywords (ALWAYS ≥1) → infer quality attributes\n"
            "5. Extract must_keywords (if present) → variants/actives only\n"
            "6. Check for clean-ingredient signals → populate avoid_terms\n"
            "7. Map to category_paths → align with anchor\n"
            "8. Write reasoning → summarize key decisions\n"
            "9. Validate against examples → finalize output\n\n"
            
            "Remember:\n"
            "- Anchor MUST be searchable, concrete (shampoo, not 'haircare product')\n"
            "- EFFICACY & KEYWORDS MANDATORY: Always generate at least 1 of each\n"
            "- CLASSIFY CORRECTLY: Variants→must_keywords, Attributes→keywords\n"
            "- CLEAN-INGREDIENT AWARE: Populate avoid_terms when signaled\n"
            "- Use profile_hints as soft priors, override with current query\n"
            "- For follow-ups, merge with MOST_RECENT history context\n"
            "- Default size=10 for personal care (max 15)\n"
            "</output_instructions>\n"
        )

    async def _try_food_es_extraction(self, ctx: UserContext, current_text: str, convo_history: list[dict[str, str]], is_follow_up: bool) -> Dict[str, Any]:
        """Food-specific extraction using extract_search_parameters tool and adapter mapping.
        Backward-compatible: returns our standard params dict (q, category_paths, price_min/max, dietary_*, keywords, etc.)."""
        # Tool schema definition (minimal fields we need)
        FOOD_EXTRACT_TOOL = {
            "name": "extract_search_parameters",
            "description": "Extracts structured search parameters from user query for Elasticsearch",
            "input_schema": {
                "type": "object",
                "properties": {
                    "anchor_query": {"type": "string"},
                    "must_clauses": {
                        "type": "object",
                        "properties": {
                            "category_paths": {"type": "array", "items": {"type": "string"}},
                            "price_range": {
                                "type": "object",
                                "properties": {
                                    "min": {"type": "number"},
                                    "max": {"type": "number"}
                                }
                            },
                            "dietary_label": {"type": "string"},
                            "availability": {
                                "type": "object",
                                "properties": {
                                    "must_be_in_stock": {"type": "boolean"},
                                    "platform": {"type": "string"}
                                }
                            },
                            "excluded_ingredients": {"type": "array", "items": {"type": "string"}},
                            "health_positioning_tags": {"type": "array", "items": {"type": "string"}},
                            "marketing_tags": {"type": "array", "items": {"type": "string"}}
                        }
                    },
                    "rerank_attributes": {"type": "array"},
                    "confidence_score": {"type": "number"},
                    "delta_analysis": {"type": "object"}
                },
                "required": ["anchor_query", "must_clauses", "confidence_score"]
            }
        }

        # Food taxonomy JSON for L1/L2 classification
        food_taxonomy = {
            "frozen_treats": ["ice_cream_cakes_and_sandwiches", "ice_cream_sticks", "light_ice_cream", "ice_cream_tubs", "ice_cream_cups", "ice_cream_cones", "frozen_pop_cubes", "kulfi"],
            "light_bites": ["energy_bars", "nachos", "chips_and_crisps", "savory_namkeen", "dry_fruit_and_nut_snacks", "popcorn"],
            "refreshing_beverages": ["soda_and_mixers", "flavored_milk_drinks", "instant_beverage_mixes", "fruit_juices", "energy_and_non_alcoholic_drinks", "soft_drinks", "iced_coffee_and_tea", "bottled_water", "enhanced_hydration"],
            "breakfast_essentials": ["muesli_and_oats", "dates_and_seeds", "breakfast_cereals"],
            "spreads_and_condiments": ["ketchup_and_sauces", "honey_and_spreads", "peanut_butter", "jams_and_jellies"],
            "packaged_meals": ["papads_pickles_and_chutneys", "baby_food", "pasta_and_soups", "baking_mixes_and_ingredients", "ready_to_cook_meals", "ready_to_eat_meals"],
            "brew_and_brew_alternatives": ["iced_coffee_and_tea", "green_and_herbal_tea", "tea", "beverage_mix", "coffee"],
            "dairy_and_bakery": ["batter_and_mix", "butter", "paneer_and_cream", "cheese", "vegan_beverages", "yogurt_and_shrikhand", "curd_and_probiotic_drinks", "bread_and_buns", "eggs", "milk", "gourmet_specialties"],
            "sweet_treats": ["pastries_and_cakes", "candies_gums_and_mints", "chocolates", "premium_chocolates", "indian_mithai", "dessert_mixes"],
            "noodles_and_vermicelli": ["vermicelli_and_noodles"],
            "biscuits_and_crackers": ["glucose_and_marie_biscuits", "cream_filled_biscuits", "rusks_and_khari", "digestive_biscuits", "wafer_biscuits", "cookies", "crackers"],
            "frozen_foods": ["non_veg_frozen_snacks", "frozen_raw_meats", "frozen_vegetables_and_pulp", "frozen_vegetarian_snacks", "frozen_sausages_salami_and_ham", "momos_and_similar", "frozen_roti_and_paratha"],
            "dry_fruits_nuts_and_seeds": ["almonds", "cashews", "raisins", "pistachios", "walnuts", "dates", "seeds"]
        }

        # Build compact prompt with strict anchor-as-q rule and taxonomy classification
        turns_json = json.dumps(convo_history, ensure_ascii=False)
        taxonomy_json = json.dumps(food_taxonomy, ensure_ascii=False)
        prompt = (
            "You are a search query parser for Food & Beverage. Extract parameters in ONE tool call.\n\n"
            f"CURRENT_USER_TEXT: {current_text}\n"
            f"IS_FOLLOW_UP: {bool(is_follow_up)}\n\n"
            # === CRITICAL: Add explicit recency structure ===
            "<recent_turns weight='CRITICAL'>\n"
            f"{json.dumps(convo_history[-3:], ensure_ascii=False, indent=2) if len(convo_history) >= 3 else json.dumps(convo_history, ensure_ascii=False, indent=2)}\n"
            "</recent_turns>\n\n"
            "<older_turns weight='REFERENCE_ONLY'>\n"
            f"{json.dumps(convo_history[:-3], ensure_ascii=False, indent=2) if len(convo_history) > 3 else '[]'}\n"
            "</older_turns>\n\n"
            f"FOOD TAXONOMY: {taxonomy_json}\n\n"
            # === NEW: Explicit anchor identification rule ===
            "<anchor_identification_rule priority='MANDATORY'>\n"
            "STEP 1: Look at the LAST 3 turns in <recent_turns> ONLY.\n"
            "STEP 2: Find the most recent explicit product noun (e.g., 'ketchup', 'chips', 'noodles').\n"
            "STEP 3: If CURRENT_USER_TEXT is a modifier (dietary/price/attribute), combine it with that product noun.\n"
            "STEP 4: Only look at <older_turns> if NO product noun exists in recent turns.\n\n"
            "Examples:\n"
            "- Recent turns: 'ketchup', 'ketchup results', 'no onion garlic' → Current: 'low sodium' → anchor='ketchup' ✓\n"
            "- Recent turns: 'chips', 'chips results' → Current: 'banana' → anchor='banana chips' ✓\n"
            "- Recent turns: 'shampoo', 'shampoo results' → Current: 'dry scalp' → anchor='shampoo' ✓\n"
            "</anchor_identification_rule>\n\n"
            "<modifier_detection>\n"
            "CURRENT_USER_TEXT is a MODIFIER if it contains:\n"
            "- Dietary terms: 'gluten free', 'vegan', 'low sodium', 'no sugar', 'organic', 'no palm oil'\n"
            "- Price terms: 'under X', 'cheap', 'budget', 'premium'\n"
            "- Attributes: 'dry scalp', 'oily skin', 'baked', 'fried', 'spicy'\n"
            "- Ingredients/flavors: 'banana', 'tomato', 'garlic', 'onion'\n\n"
            "If MODIFIER detected:\n"
            "  → Find product noun from last 3 turns\n"
            "  → Set anchor_query = that product noun (NOT the modifier alone)\n"
            "  → Put modifier constraints in must_clauses\n"
            "</modifier_detection>\n\n"
            "MANDATORY RULES:\n"
            "- anchor_query MUST be a product noun (e.g., 'ketchup', 'chips', 'noodles')\n"
            "- NEVER set anchor_query to a modifier alone (e.g., ❌ 'low sodium', ❌ 'banana', ❌ 'dry scalp')\n"
            "- For follow-ups, anchor_query = product noun from last 3 turns unless explicit category switch\n"
            "- Use FOOD TAXONOMY to classify anchor_query → category_paths\n"
            "- Put all constraints (dietary/price/ingredients) in must_clauses, NOT in anchor_query\n\n"
            "Output: Return ONLY tool call to extract_search_parameters."
        )

        resp = await self.anthropic.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[FOOD_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "extract_search_parameters"},
            temperature=0,
            max_tokens=2000,
        )
        tool_use = pick_tool(resp, "extract_search_parameters")
        if not tool_use:
            return {}

        raw = _strip_keys(tool_use.input or {})
        try:
            print("CORE:LLM2_FOOD_OUT_FULL | " + json.dumps(raw, ensure_ascii=False))
        except Exception:
            pass

        # Adapt to our param dict
        params: Dict[str, Any] = {}
        anchor = str(raw.get("anchor_query") or "").strip()
        if not anchor:
            return {}
        params["q"] = anchor
        params["category_group"] = "f_and_b"

        mc = raw.get("must_clauses") or {}
        # category_paths - extract L1/L2 from f_and_b/food/L1/L2 format
        try:
            cps = mc.get("category_paths") or []
            if isinstance(cps, list):
                processed_paths = []
                for cp in cps:
                    path_str = str(cp).strip()
                    if path_str:
                        # If LLM returned full path like "f_and_b/food/light_bites/chips_and_crisps"
                        # Extract just "light_bites/chips_and_crisps" for ES builder
                        if path_str.startswith("f_and_b/food/"):
                            rel_path = path_str[len("f_and_b/food/"):]
                            if rel_path:
                                processed_paths.append(rel_path)
                        else:
                            # Keep as-is if not in expected format
                            processed_paths.append(path_str)
                params["category_paths"] = processed_paths
        except Exception:
            pass
        # price_range
        try:
            pr = mc.get("price_range") or {}
            if pr.get("min") is not None:
                params["price_min"] = float(pr.get("min"))
            if pr.get("max") is not None:
                params["price_max"] = float(pr.get("max"))
        except Exception:
            pass
        # dietary_label mapping
        try:
            dl = str(mc.get("dietary_label") or "").strip().lower()
            if dl:
                # Map to dietary_labels (keyword) when standardized; also pass through raw kind
                params["dietary_label_raw"] = dl
                mapped: list[str] = []
                if dl in {"vegan"}:
                    mapped = ["VEGAN"]
                elif dl in {"gluten-free", "gluten free", "glutenfree"}:
                    mapped = ["GLUTEN FREE"]
                # Also allow "veg"/"non-veg" to be handled separately in builder
                if mapped:
                    params["dietary_labels"] = mapped
        except Exception:
            pass
        # availability
        try:
            av = mc.get("availability") or {}
            if bool(av.get("must_be_in_stock")) and str(av.get("platform") or "").strip().lower() in {"zepto", "any", "*", "all"}:
                params["availability_zepto_in_stock"] = True
        except Exception:
            pass
        # excluded_ingredients
        try:
            ex = mc.get("excluded_ingredients") or []
            if isinstance(ex, list) and ex:
                params["excluded_ingredients"] = [str(x).strip().lower() for x in ex if str(x).strip()]
        except Exception:
            pass
        # tags
        try:
            hp = mc.get("health_positioning_tags") or []
            if isinstance(hp, list) and hp:
                params["health_positioning_tags"] = [str(x).strip() for x in hp if str(x).strip()]
        except Exception:
            pass
        try:
            mk = mc.get("marketing_tags") or []
            if isinstance(mk, list) and mk:
                params["marketing_tags"] = [str(x).strip() for x in mk if str(x).strip()]
        except Exception:
            pass

        # Derive at most two lightweight keywords from marketing or simple flavor-like rerank attributes
        try:
            keywords: list[str] = []
            for tag in (params.get("marketing_tags") or [])[:2]:
                # take single tokens; drop if contained in q
                tok = tag.lower()
                if " " not in tok and tok not in (params.get("q") or "").lower():
                    keywords.append(tok)
            if keywords:
                params["keywords"] = keywords[:2]
        except Exception:
            pass

        # Size clamp (keep default behavior)
        try:
            s = int(ctx.session.get("size_hint", 20) or 20)
            params["size"] = max(1, min(50, s))
        except Exception:
            params["size"] = 20

        return params

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

    def _get_fnb_taxonomy_hierarchical(self) -> Dict[str, Any]:
        """Load hierarchical F&B taxonomy and flatten for LLM prompt efficiency."""
        import os, json
        
        # Try to load user-provided hierarchical taxonomy
        hierarchical = {}
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(here, "taxonomies", "fnb_hierarchy.json")
            with open(path, "r", encoding="utf-8") as f:
                hierarchical = json.load(f)
        except Exception:
            # Fallback: use embedded taxonomy from user requirements
            hierarchical = {
                "f_and_b": {
                    "food": {
                        "frozen_treats": {
                            "ice_cream_cakes_and_sandwiches": {},
                            "ice_cream_sticks": {},
                            "light_ice_cream": {},
                            "ice_cream_tubs": {},
                            "ice_cream_cups": {},
                            "ice_cream_cones": {},
                            "frozen_pop_cubes": {},
                            "kulfi": {}
                        },
                        "light_bites": {
                            "energy_bars": {},
                            "nachos": {},
                            "chips_and_crisps": {},
                            "savory_namkeen": {},
                            "dry_fruit_and_nut_snacks": {},
                            "popcorn": {}
                        },
                        "breakfast_essentials": {
                            "muesli_and_oats": {},
                            "dates_and_seeds": {},
                            "breakfast_cereals": {}
                        },
                        "packaged_meals": {
                            "papads_and_pickles_and_chutneys": {},
                            "baby_food": {},
                            "pasta_and_soups": {},
                            "baking_mixes_and_ingredients": {},
                            "ready_to_cook_meals": {},
                            "ready_to_eat_meals": {}
                        },
                        "dairy_and_bakery": {
                            "batter_and_mix": {},
                            "butter": {},
                            "paneer_and_cream": {},
                            "cheese": {},
                            "vegan_beverages": {},
                            "yogurt_and_shrikhand": {},
                            "curd_and_probiotic_drinks": {},
                            "bread_and_buns": {},
                            "eggs": {},
                            "gourmet_specialties": {}
                        },
                        "sweet_treats": {
                            "pastries_and_cakes": {},
                            "candies_gums_and_mints": {},
                            "chocolates": {},
                            "premium_chocolates": {},
                            "indian_mithai": {},
                            "dessert_mixes": {}
                        },
                        "noodles_and_vermicelli": {
                            "vermicelli_and_noodles": {}
                        },
                        "biscuits_and_crackers": {
                            "glucose_and_marie_biscuits": {},
                            "cream_filled_biscuits": {},
                            "rusks_and_khari": {},
                            "digestive_biscuits": {},
                            "wafer_biscuits": {},
                            "cookies": {},
                            "crackers": {}
                        },
                        "frozen_foods": {
                            "non_veg_frozen_snacks": {},
                            "frozen_raw_meats": {},
                            "frozen_vegetables_and_pulp": {},
                            "frozen_vegetarian_snacks": {},
                            "frozen_sausages_salami_and_ham": {},
                            "momos_and_similar": {},
                            "frozen_roti_and_paratha": {}
                        },
                        "spreads_and_condiments": {
                            "ketchup_and_sauces": {},
                            "honey_and_spreads": {},
                            "peanut_butter": {}
                        }
                    },
                    "beverages": {
                        "sodas_juices_and_more": {
                            "soda_and_mixers": {},
                            "flavored_milk_drinks": {},
                            "instant_beverage_mixes": {},
                            "fruit_juices": {},
                            "energy_and_non_alcoholic_drinks": {},
                            "soft_drinks": {},
                            "iced_coffee_and_tea": {},
                            "bottled_water": {},
                            "enhanced_hydration": {}
                        },
                        "tea_coffee_and_more": {
                            "iced_coffee_and_tea": {},
                            "green_and_herbal_tea": {},
                            "tea": {},
                            "beverage_mix": {},
                            "coffee": {}
                        },
                        "dairy_and_bakery": {
                            "milk": {}
                        }
                    }
                }
            }
        
        # Flatten to {food: {l2: [l3s]}, beverages: {l2: [l3s]}} for token efficiency
        return self._flatten_fnb_taxonomy(hierarchical)
    
    def _flatten_fnb_taxonomy(self, hierarchical: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
        """Convert nested taxonomy to 2-level structure for prompt."""
        flattened = {}
        try:
            fnb = hierarchical.get("f_and_b", {})
            for domain in ["food", "beverages"]:
                if domain in fnb:
                    flattened[domain] = {}
                    for l2_key, l3_dict in fnb[domain].items():
                        if isinstance(l3_dict, dict):
                            flattened[domain][l2_key] = list(l3_dict.keys())
                        else:
                            flattened[domain][l2_key] = []
        except Exception as exc:
            log.warning(f"FNB_TAXONOMY_FLATTEN_ERROR | {exc}")
        return flattened

    def _get_personal_care_taxonomy(self) -> Dict[str, Any]:
        """Load Personal Care taxonomy JSON if present."""
        import os, json
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(here, "taxonomies", "personal_care.json")
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # Embedded minimal fallback so taxonomy-dependent prompts still work
            return {
                "personal_care": {
                    "skin": {
                        "3_purifying_cleansers": {},
                        "1_skin_hydrators": {},
                        "11_uv_defense": {},
                        "5_skin_toners": {},
                        "2_active_serums": {}
                    },
                    "hair": {
                        "17_hair_nurture": {
                            "conditioner": {},
                            "shampoo": {}
                        }
                    }
                }
            }

    def _resolve_pc_paths(self, subcategory: Optional[str], taxonomy: Dict[str, Any]) -> List[str]:
        """Resolve up to 2 full personal_care category_paths from a subcategory token."""
        results: List[str] = []
        try:
            if not (subcategory and taxonomy and isinstance(taxonomy, dict)):
                return results
            sub = str(subcategory).strip().lower()
            pc = taxonomy.get("personal_care") if isinstance(taxonomy.get("personal_care"), dict) else {}
            def walk(node: Dict[str, Any], prefix: List[str]):
                for k, v in node.items():
                    key = str(k).strip()
                    key_l = key.lower()
                    path = prefix + [key]
                    # Match if sub token is within key
                    if sub in key_l and len(results) < 2:
                        results.append("personal_care/" + "/".join(path))
                    if isinstance(v, dict) and v and len(results) < 2:
                        walk(v, path)
            if isinstance(pc, dict):
                for l2, l3s in pc.items():
                    if isinstance(l3s, dict):
                        walk(l3s, [l2])
            return results[:2]
        except Exception:
            return results

    async def generate_skin_es_params(self, ctx: UserContext) -> Dict[str, Any]:
        """Skin-specific ES param extraction using history and personal care taxonomy."""
        try:
            # Fast-path: Use 2025 unified personal care generator when there is current text
            session = ctx.session or {}
            current_text = str(
                getattr(ctx, "current_user_text", "")
                or session.get("current_user_text")
                or session.get("last_user_message")
                or ""
            ).strip()
            
            if current_text:
                try:
                    params_2025 = await self._generate_personal_care_es_params_2025(ctx, current_text)
                    if isinstance(params_2025, dict) and params_2025.get("q"):
                        return params_2025
                except Exception as e:
                    print(f"CORE:PC_2025_FALLBACK | {e}")
                    # Fall through to legacy path
            
            # Legacy path (backward compatibility)
            ask_only_mode = bool(getattr(Cfg, "USE_ASSESSMENT_FOR_ASK_ONLY", False))
            # Determine follow-up via LLM1 classifier (no deterministic rules)
            try:
                fu_res = await self.classify_follow_up(current_text, ctx)
                is_follow_up = bool(getattr(fu_res, "is_follow_up", False))
            except Exception:
                is_follow_up = False
            hist_limit = 10 if is_follow_up else 5
            convo_history = self._build_last_interactions(ctx, limit=hist_limit)
            interactions_json = json.dumps(convo_history, ensure_ascii=False)
            candidate_subcats = session.get("candidate_subcategories") or []
            domain_subcat = str(session.get("domain_subcategory") or "").strip()
            skin_taxonomy = self._get_personal_care_taxonomy()
            product_intent = str(session.get("product_intent") or "show_me_options")
            # Profile hints (best-effort)
            profile_hints = {
                "known_skin_type": session.get("user_skin_type"),
                "known_concerns": session.get("user_skin_concerns", []),
                "known_allergies": session.get("user_allergies", []),
                "preferences": session.get("preferences")
            }

            # Essential logging only
            try:
                print(f"CORE:SKIN_LEGACY | follow_up={is_follow_up} | current='{current_text[:60]}'")
            except Exception:
                pass

            prompt = (
                "You are a personal care search planner for skin and hair.\n\n"
                f"QUERY: \"{current_text}\"\n"
                f"FOLLOW_UP: {is_follow_up}\n"
                f"RECENT INTERACTIONS (last {hist_limit}): {interactions_json}\n"
                f"CANDIDATE_SUBCATEGORIES: {json.dumps(candidate_subcats, ensure_ascii=False)}\n"
                f"DOMAIN_SUBCATEGORY_HINT: {domain_subcat}\n"
                f"PROFILE_HINTS: {json.dumps(profile_hints, ensure_ascii=False)}\n"
                # PERSONAL_CARE TAXONOMY removed from prompt to keep it lean\n"
                f"PRODUCT_INTENT: {product_intent}\n\n"
                "Deliberate silently step-by-step to extract robust parameters. Do not output your reasoning; OUTPUT ONLY a tool call.\n"
                "Task: Emit normalized ES params for personal_care (skin or hair). Keep q as product noun (no price/concern words).\n"
                "Prefer specific noun-phrases; for modifier-only messages, anchor to most recent product noun.\n"
                "Return fields: q, category_group='personal_care', brands[], price_min, price_max, size (<=10), anchor_product_noun,\n"
                "               skin_types[], hair_types[], efficacy_terms[], avoid_terms[].\n"
                "Extraction guidance (use judgment, not rigid rules):\n"
                "- Skin types: map phrases → [oily, dry, combination, sensitive, normal]. Examples: 'oily skin', 'shine', 'greasy' → oily; 'dry', 'flaky', 'tight' → dry; 'T-zone oily' → combination; 'itchy, redness' → sensitive.\n"
                "- Hair types: [dry, oily, normal, curly, straight, wavy].\n"
                "- Efficacy terms (positive): 2–5 precise aspects (e.g., anti-dandruff, scalp care, hydration). Prefer taxonomy-aligned terms and common variants.\n"
                "- Avoid terms (negative): 1–4 negatives the user does NOT want (e.g., fragrance, harsh, sulfates). These will be used for both side_effects and cons_list.\n"
                "- Use PROFILE_HINTS as soft priors; override only if CURRENT query clearly contradicts.\n"
                "- If FOLLOW_UP: identify the delta (added or removed) and reflect it in efficacy_terms or avoid_terms accordingly. Always compute lists fresh from conversation context.\n"
                "- If DOMAIN_SUBCATEGORY_HINT present and consistent, set subcategory accordingly and build 1–2 category_paths from taxonomy.\n"
                "- Always include arrays even if empty; do not omit keys.\n"
            )

            # LLM call (no verbose logging)

            # Use dual tools: initial vs follow-up schemas per provided design
            tool_set = [FOLLOWUP_SKIN_PARAMS_TOOL] if is_follow_up else [INITIAL_SKIN_PARAMS_TOOL]
            tool_name = "extract_followup_skin_params" if is_follow_up else "extract_initial_skin_params"
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=tool_set,
                tool_choice={"type": "tool", "name": tool_name},
                temperature=0,
                max_tokens=2000,
            )
            tool_use = pick_tool(resp, tool_name)
            params = _strip_keys(tool_use.input or {}) if tool_use else {}

            # Essential output logging only

            # Normalize and clamp
            try:
                s = int(params.get("size", 10) or 10)
                params["size"] = max(1, min(10, s))
            except Exception:
                params["size"] = 10
            params["category_group"] = "personal_care"

            # Clean list fields
            for lf in ["brands", "keywords", "skin_types", "skin_concerns", "hair_types", "hair_concerns", "efficacy_terms", "avoid_terms", "avoid_ingredients", "product_types"]:
                if isinstance(params.get(lf), list):
                    params[lf] = [str(x).strip() for x in params[lf] if str(x).strip()]
            # Ensure deterministic presence of expected arrays
            for must_key in [
                "skin_types", "hair_types", "skin_concerns", "hair_concerns",
                "efficacy_terms", "avoid_terms", "avoid_ingredients"
            ]:
                if must_key not in params or params.get(must_key) is None:
                    params[must_key] = []

            # Normalize category_paths: convert dot → slash and ensure 'personal_care/' prefix
            try:
                norm_paths: List[str] = []
                for cp in (params.get("category_paths") or [])[:3]:
                    s = str(cp).strip()
                    if not s:
                        continue
                    s = s.replace(".", "/")
                    if not s.startswith("personal_care/"):
                        s = ("personal_care/" + s.lstrip("/")) if "personal_care" in s else ("personal_care/" + s)
                    if s not in norm_paths:
                        norm_paths.append(s)
                if norm_paths:
                    params["category_paths"] = norm_paths
                    # Path normalization (no verbose logging)
            except Exception:
                pass

            try:
                print(
                    "CORE:SKIN_LLM_OUT_VALS | "
                    f"q='{params.get('q')}' | subcat='{params.get('subcategory')}' | paths={len(params.get('category_paths') or [])} | "
                    f"brands={len(params.get('brands') or [])} | price=({params.get('price_min')},{params.get('price_max')}) | "
                    f"skin_types={params.get('skin_types')} | skin_concerns={params.get('skin_concerns')} | hair_types={params.get('hair_types')} | hair_concerns={params.get('hair_concerns')} | "
                    f"efficacy_terms={params.get('efficacy_terms')} | avoid_terms={params.get('avoid_terms')} | "
                    f"avoid={len(params.get('avoid_ingredients') or [])} | types={params.get('product_types')} | prioritize_concerns={params.get('prioritize_concerns')} | "
                    f"min_reviews={params.get('min_review_count')} | size={params.get('size')}"
                )
                # Explicit predictions block (deterministic visibility)
                print(
                    "CORE:PC_LLM_PRED | "
                    f"efficacy_terms={params.get('efficacy_terms')} | cons_list_from_avoid_terms={params.get('avoid_terms')} | "
                    f"side_effects_terms={params.get('avoid_terms')} | skin_types={params.get('skin_types')} | hair_types={params.get('hair_types')}"
                )
                # Planner stickiness report: what will be filled from profile if empty
                sticky = []
                profile_skin_type = ctx.session.get("user_skin_type") or ctx.session.get("user_hair_type")
                if not params.get('skin_types') and profile_skin_type:
                    sticky.append("skin_types←profile")
                if not params.get('skin_concerns') and ctx.session.get('user_skin_concerns'):
                    sticky.append("skin_concerns←profile")
                if not params.get('avoid_ingredients') and ctx.session.get('user_allergies'):
                    sticky.append("avoid_ingredients←profile")
                if sticky:
                    # Sticky profile logic (no verbose logging)
                    pass
            except Exception:
                pass

            print(f"CORE:SKIN_LLM_OUT | q='{params.get('q')}' | subcat='{params.get('subcategory')}' | price=({params.get('price_min')},{params.get('price_max')}) | types={params.get('product_types')} | skin_concerns={params.get('skin_concerns')} | hair_concerns={params.get('hair_concerns')} | efficacy_terms={params.get('efficacy_terms')} | avoid_terms={params.get('avoid_terms')}")
            return params
        except Exception as exc:
            log.error(f"SKIN_PARAMS_ERROR | {exc}")
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