# shopping_bot/recommendation.py
"""
Recommendation Engine Module for ShoppingBotCore
───────────────────────────────────────────────
• Handles Elasticsearch parameter extraction and optimization
• Provides product recommendation logic
• Extensible architecture for future recommendation enhancements
• Clean separation from main LLM service

Created: 2025-08-20
Purpose: Modularize recommendation logic for better maintainability

Fix (2025-08-22):
• Switched to anthropic.AsyncAnthropic and awaited all .messages.create(...) calls
• Robust tool-pick for Anthropic response content blocks
• Defensive fallbacks when tool call is missing (JSON sniff + heuristic defaults)
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from enum import Enum

import anthropic

from .config import get_config
from .models import UserContext

Cfg = get_config()
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Response Types and Enums
# ─────────────────────────────────────────────────────────────

class RecommendationResponseType(Enum):
    """Types of responses from the recommendation engine"""
    ES_PARAMS = "es_params"
    PRODUCT_LIST = "product_list"
    ERROR = "error"
    ENHANCED_PARAMS = "enhanced_params"


@dataclass
class RecommendationResponse:
    """Standardized response from recommendation engine"""
    response_type: RecommendationResponseType
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "response_type": self.response_type.value,
            "data": self.data,
            "metadata": self.metadata or {},
            "error_message": self.error_message
        }


# ─────────────────────────────────────────────────────────────
# Base Recommendation Engine Interface
# ─────────────────────────────────────────────────────────────

class BaseRecommendationEngine(ABC):
    """Abstract base class for recommendation engines"""
    
    @abstractmethod
    async def extract_search_params(self, ctx: UserContext) -> RecommendationResponse:
        """Extract search parameters from user context"""
        raise NotImplementedError
    
    @abstractmethod
    def validate_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and clean extracted parameters"""
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────
# Elasticsearch Parameter Tool
# ─────────────────────────────────────────────────────────────

ES_PARAM_TOOL = {
    "name": "emit_es_params",
    "description": "Return normalized Elasticsearch params derived from ctx.session. Omit fields you cannot infer confidently.",
    "input_schema": {
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "Final search text."},
            "size": {"type": "integer", "minimum": 1, "maximum": 50},
            "category_group": {"type": "string"},
            "brands": {"type": "array", "items": {"type": "string"}},
            "dietary_terms": {"type": "array", "items": {"type": "string"}},
            "price_min": {"type": "number"},
            "price_max": {"type": "number"},
            "protein_weight": {"type": "number"},
            "phrase_boosts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "phrase": {"type": "string"},
                        "boost": {"type": "number"}
                    },
                    "required": ["field", "phrase"]
                }
            },
            "field_boosts": {"type": "array", "items": {"type": "string"}},
            "sort": {"type": "array", "items": {"type": "object"}},
            "highlight": {"type": "object"},
        },
    },
}


# ─────────────────────────────────────────────────────────────
# Main Recommendation Engine Implementation
# ─────────────────────────────────────────────────────────────

class ElasticsearchRecommendationEngine(BaseRecommendationEngine):
    """Primary recommendation engine using Elasticsearch parameter extraction"""
    
    def __init__(self):
        # IMPORTANT: async client for awaitable calls
        self._anthropic = anthropic.AsyncAnthropic(api_key=Cfg.ANTHROPIC_API_KEY)
        self._valid_categories = [
            "f_and_b", "health_nutrition", "personal_care", 
            "home_kitchen", "electronics"
        ]
        # F&B taxonomy (provided)
        self._fnb_taxonomy = {
            "frozen_treats": [
                "ice_cream_cakes_and_sandwiches",
                "ice_cream_sticks",
                "light_ice_cream",
                "ice_cream_tubs",
                "ice_cream_cups",
                "ice_cream_cones",
                "frozen_pop_cubes",
                "kulfi"
            ],
            "light_bites": [
                "energy_bars",
                "nachos",
                "chips_and_crisps",
                "savory_namkeen",
                "dry_fruit_and_nut_snacks",
                "popcorn"
            ],
            "refreshing_beverages": [
                "soda_and_mixers",
                "flavored_milk_drinks",
                "instant_beverage_mixes",
                "fruit_juices",
                "energy_and_non_alcoholic_drinks",
                "soft_drinks",
                "iced_coffee_and_tea",
                "bottled_water",
                "enhanced_hydration"
            ],
            "breakfast_essentials": [
                "muesli_and_oats",
                "dates_and_seeds",
                "breakfast_cereals"
            ],
            "spreads_and_condiments": [
                "ketchup_and_sauces",
                "honey_and_spreads",
                "peanut_butter",
                "jams_and_jellies"
            ],
            "packaged_meals": [
                "papads_pickles_and_chutneys",
                "baby_food",
                "pasta_and_soups",
                "baking_mixes_and_ingredients",
                "ready_to_cook_meals",
                "ready_to_eat_meals"
            ],
            "brew_and_brew_alternatives": [
                "iced_coffee_and_tea",
                "green_and_herbal_tea",
                "tea",
                "beverage_mix",
                "coffee"
            ],
            "dairy_and_bakery": [
                "batter_and_mix",
                "butter",
                "paneer_and_cream",
                "cheese",
                "vegan_beverages",
                "yogurt_and_shrikhand",
                "curd_and_probiotic_drinks",
                "bread_and_buns",
                "eggs",
                "milk",
                "gourmet_specialties"
            ],
            "sweet_treats": [
                "pastries_and_cakes",
                "candies_gums_and_mints",
                "chocolates",
                "premium_chocolates",
                "indian_mithai",
                "dessert_mixes"
            ],
            "noodles_and_vermicelli": [
                "vermicelli_and_noodles"
            ],
            "biscuits_and_crackers": [
                "glucose_and_marie_biscuits",
                "cream_filled_biscuits",
                "rusks_and_khari",
                "digestive_biscuits",
                "wafer_biscuits",
                "cookies",
                "crackers"
            ],
            "frozen_foods": [
                "non_veg_frozen_snacks",
                "frozen_raw_meats",
                "frozen_vegetables_and_pulp",
                "frozen_vegetarian_snacks",
                "frozen_sausages_salami_and_ham",
                "momos_and_similar",
                "frozen_roti_and_paratha"
            ],
            "dry_fruits_nuts_and_seeds": [
                "almonds",
                "cashews",
                "raisins",
                "pistachios",
                "walnuts",
                "dates",
                "seeds"
            ]
        }
    
    async def extract_search_params(self, ctx: UserContext) -> RecommendationResponse:
        """
        Enhanced parameter extraction with better query understanding.
        Focuses on food/product categorization and budget parsing.
        """
        try:
            session = ctx.session or {}
            assessment = session.get("assessment", {})
            
            # Build context for LLM
            context = {
                "original_query": assessment.get("original_query", "") or session.get("last_query", "") or "",
                "user_answers": {
                    "budget": session.get("budget"),
                    "dietary_requirements": session.get("dietary_requirements"),
                    "product_category": session.get("product_category"),
                    "brands": session.get("brands"),
                },
                "session_data": {
                    "category_group": session.get("category_group"),
                    "last_query": session.get("last_query"),
                },
                "debug": session.get("debug", {}),
                "last_recommendation": session.get("last_recommendation", {}),
                "conversation_history": [
                    {
                        "user_query": (h or {}).get("user_query"),
                        "bot_reply": ((h or {}).get("final_answer", {}) or {}).get("message_full")
                    }
                    for h in (session.get("conversation_history", []) or [])[-3:]
                ],
            }
            
            current_user_text = context.get("original_query", "")
            # 1) Construct a context-aware search query
            constructed_q = await self._construct_search_query(context, current_user_text)
            log.info(f"ES_CONSTRUCTED_QUERY | q='{constructed_q}'")

            # 2) Ask LLM to emit initial ES params (seeded by constructed_q)
            prompt = self._build_extraction_prompt(context, constructed_q)
            params_from_llm = await self._call_anthropic_for_params(prompt)
            if isinstance(params_from_llm, dict):
                try:
                    log.info(f"ES_PARAMS_RAW | keys={list(params_from_llm.keys())}")
                except Exception:
                    pass

            if params_from_llm is None:
                # Defensive fallback
                fallback = self._heuristic_defaults(context, constructed_q)
                return RecommendationResponse(
                    response_type=RecommendationResponseType.ES_PARAMS,
                    data=fallback,
                    metadata={"extraction_method": "fallback_heuristic", "context_keys": list(context.keys())},
                    error_message="LLM did not return a tool-call; used heuristics."
                )
            
            # 3) Normalise params via dedicated LLM tool
            final_params = await self._normalise_es_params(params_from_llm, context, constructed_q)
            # Guard against UNKNOWN/blank q after normalisation
            try:
                qval = str(final_params.get("q", "")).strip()
                if not qval or qval.upper().find("UNKNOWN") >= 0:
                    log.warning("ES_Q_FALLBACK | replacing invalid q with constructed query")
                    final_params["q"] = constructed_q
            except Exception:
                final_params["q"] = constructed_q

            # 4) Optional F&B classification to attach category/subcategory
            fb_meta = await self._fb_category_classify(constructed_q)
            if fb_meta.get("is_fnb"):
                final_params["category_group"] = "f_and_b"
                # Attach category/subcategory as metadata for downstream query builder
                final_params["fb_category"] = fb_meta.get("category")
                final_params["fb_subcategory"] = fb_meta.get("subcategory")
                log.info(f"FB_CLASSIFIED | category={final_params.get('fb_category')} | subcategory={final_params.get('fb_subcategory')}")

            log.info(f"ES_PARAMS_FINAL | keys={list(final_params.keys())}")
            try:
                log.info(f"ES_SEARCH_QUERY_USED | q='{final_params.get('q','')}'")
            except Exception:
                pass
            return RecommendationResponse(
                response_type=RecommendationResponseType.ES_PARAMS,
                data=final_params,
                metadata={
                    "extraction_method": "llm_enhanced",
                    "context_keys": list(context.keys()),
                    "constructed_q": constructed_q
                }
            )
            
        except Exception as exc:
            log.warning("Enhanced ES param extraction failed: %s", exc)
            return RecommendationResponse(
                response_type=RecommendationResponseType.ERROR,
                data={},
                error_message=str(exc)
            )
    
    def _build_extraction_prompt(self, context: Dict[str, Any], constructed_q: str) -> str:
        """Build the extraction prompt for LLM"""
        return f"""
You are a search parameter extractor for an e-commerce platform. 

USER CONTEXT:
{json.dumps(context, ensure_ascii=False, indent=2)}

CONSTRUCTED QUERY (use this as the primary q):
{constructed_q}

TASK: Extract normalized Elasticsearch parameters for product search.

RULES:
1. q: Use the CONSTRUCTED QUERY above as the main search text
2. category_group: 
   - "f_and_b" for food, beverages, snacks, bread, etc.
   - "health_nutrition" for supplements, vitamins
   - "personal_care" for cosmetics, hygiene
   - Default to "f_and_b" if unclear
3. dietary_terms: Extract terms like "GLUTEN FREE", "VEGAN", "ORGANIC" (UPPERCASE)
4. price_min/price_max: Parse budget expressions:
   - "100 rupees" → price_max: 100
   - "under 200" → price_max: 200  
   - "50-150" → price_min: 50, price_max: 150
   - "0-200 rupees" → price_min: 0, price_max: 200
5. brands: Extract brand names if mentioned
6. size: Default 20, max 50

EXAMPLES:
- "gluten free bread under 100 rupees" → category_group: "f_and_b", dietary_terms: ["GLUTEN FREE"], price_max: 100
- "organic snacks 50-200" → category_group: "f_and_b", dietary_terms: ["ORGANIC"], price_min: 50, price_max: 200

Return ONLY the tool call to emit_es_params.
"""
    
    async def _call_anthropic_for_params(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Make the Anthropic API call for parameter extraction"""
        try:
            resp = await self._anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[ES_PARAM_TOOL],
                tool_choice={"type": "tool", "name": "emit_es_params"},
                temperature=0,
                max_tokens=400,
            )
            
            tool_use = self._pick_tool(resp, "emit_es_params")
            if not tool_use:
                # Attempt soft fallback: sometimes models emit raw JSON in text
                raw_text = (resp.content[0].text if resp.content and getattr(resp.content[0], "text", None) else "") or ""
                try:
                    parsed = json.loads(raw_text)
                    if isinstance(parsed, dict):
                        return self._strip_keys(parsed)
                except Exception:
                    pass
                return None
            
            raw_params = getattr(tool_use, "input", {}) or {}
            cleaned_params = self._strip_keys(raw_params) if isinstance(raw_params, dict) else {}
            return cleaned_params
            
        except Exception as exc:
            log.error(f"Anthropic API call failed: {exc}")
            return None
    
    async def _normalise_es_params(self, params: Dict[str, Any], context: Dict[str, Any], constructed_q: str) -> Dict[str, Any]:
        """LLM-driven normalisation of ES params (currencies, ranges, spelling, business rules)."""
        NORMALISE_PARAMS_TOOL = {
            "name": "normalise_es_params",
            "description": "Normalise and validate ES params. Parse currency/ranges; uppercase dietary terms; clamp size; correct obvious spellings.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "size": {"type": "integer"},
                    "category_group": {"type": "string"},
                    "brands": {"type": "array", "items": {"type": "string"}},
                    "dietary_terms": {"type": "array", "items": {"type": "string"}},
                    "price_min": {"type": "number"},
                    "price_max": {"type": "number"},
                    "protein_weight": {"type": "number"},
                    "phrase_boosts": {"type": "array", "items": {"type": "object"}},
                    "field_boosts": {"type": "array", "items": {"type": "string"}},
                    "sort": {"type": "array", "items": {"type": "object"}}
                },
                "required": ["q", "size"]
            }
        }

        prompt = (
            "You are normalising ES parameters.\n\n"
            f"Original context keys: {list(context.keys())}\n"
            f"Constructed query: {constructed_q}\n"
            f"Current params: {json.dumps(params, ensure_ascii=False)}\n\n"
            "Rules: clamp size to [1,50]; ensure price_min<=price_max; uppercase dietary terms;\n"
            "if dietary_terms == ['ANY'] then drop dietary_terms; set q=constructed query if current q is empty;"
        )

        try:
            resp = await self._anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[NORMALISE_PARAMS_TOOL],
                tool_choice={"type": "tool", "name": "normalise_es_params"},
                temperature=0,
                max_tokens=400,
            )
            tool_use = self._pick_tool(resp, "normalise_es_params")
            if tool_use and getattr(tool_use, "input", None):
                norm = self._strip_keys(getattr(tool_use, "input", {}) or {})
            else:
                norm = params
        except Exception as exc:
            log.warning(f"NORMALISE_PARAMS_FAILED | error={exc}")
            norm = params

        # Defensive post-normalisation (ensure constraints)
        out: Dict[str, Any] = {}
        out["q"] = (norm.get("q") or constructed_q or "").strip()
        try:
            out["size"] = max(1, min(50, int(norm.get("size", 20))))
        except Exception:
            out["size"] = 20
        if isinstance(norm.get("price_min"), (int, float)):
            out["price_min"] = float(norm["price_min"])
        if isinstance(norm.get("price_max"), (int, float)):
            out["price_max"] = float(norm["price_max"])
        if "price_min" in out and "price_max" in out and out["price_min"] > out["price_max"]:
            out["price_min"], out["price_max"] = out["price_max"], out["price_min"]
        # Lists
        for lf in ["brands", "dietary_terms"]:
            items = norm.get(lf)
            if isinstance(items, list):
                cleaned = [str(x).strip() for x in items if str(x).strip()]
                if lf == "dietary_terms":
                    cleaned = [x.upper() for x in cleaned if x.upper() != "ANY"]
                if cleaned:
                    out[lf] = cleaned
        # Optional fields
        if isinstance(norm.get("protein_weight"), (int, float)):
            pw = float(norm["protein_weight"])
            if 0.1 <= pw <= 10.0:
                out["protein_weight"] = pw
        if isinstance(norm.get("phrase_boosts"), list):
            out["phrase_boosts"] = norm["phrase_boosts"]
        if isinstance(norm.get("field_boosts"), list):
            out["field_boosts"] = norm["field_boosts"]
        if isinstance(norm.get("sort"), list):
            out["sort"] = norm["sort"]

        return out

    async def _construct_search_query(self, context: Dict[str, Any], current_user_text: str) -> str:
        """LLM tool to construct a context-aware search phrase from context (query + slots)."""
        CONSTRUCT_QUERY_TOOL = {
            "name": "construct_search_query",
            "description": "Construct a single, coherent search phrase including relevant constraints (brand, color, dietary, budget).",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
        prompt = (
            "Construct a concise product search phrase from context.\n\n"
            f"CURRENT_USER_TEXT: {current_user_text}\n\n"
            f"RECENT_TURNS (user↔bot): {json.dumps(context.get('conversation_history', []), ensure_ascii=False)}\n\n"
            f"SLOTS/STATE: {json.dumps({'user_answers': context.get('user_answers', {}), 'session_data': context.get('session_data', {}), 'debug': context.get('debug', {})}, ensure_ascii=False)}\n\n"
            f"LAST_RECOMMENDATION: {json.dumps(context.get('last_recommendation', {}), ensure_ascii=False)}\n\n"
            "Rules: Prefer debug.last_search_params.q if present; else derive from last_recommendation/products and slots;"
            " include constraints (brand, color, dietary in UPPERCASE, price range).\n"
            "Avoid placeholders like <UNKNOWN>.\n"
            "Return ONLY tool call to construct_search_query."
        )
        try:
            resp = await self._anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[CONSTRUCT_QUERY_TOOL],
                tool_choice={"type": "tool", "name": "construct_search_query"},
                temperature=0,
                max_tokens=200,
            )
            tool_use = self._pick_tool(resp, "construct_search_query")
            if tool_use and getattr(tool_use, "input", None):
                q = (getattr(tool_use, "input", {}) or {}).get("query") or ""
                q = str(q).strip()
                if not q or q.upper().find("UNKNOWN") >= 0:
                    raise ValueError("constructed query invalid")
                return q
        except Exception as exc:
            log.warning(f"CONSTRUCT_QUERY_FAILED | error={exc}")
        # Fallback composition from available signals
        try:
            composed = self._compose_query_from_context(context, current_user_text)
            if composed:
                log.info(f"CONSTRUCT_QUERY_FALLBACK | q='{composed}'")
                return composed
        except Exception:
            pass
        q = context.get("original_query") or (context.get("session_data", {}) or {}).get("last_query") or current_user_text or ""
        return str(q).strip()

    def _compose_query_from_context(self, context: Dict[str, Any], current_user_text: str) -> str:
        """Programmatic fallback: prefer last_search_params, else synthesize from last_recommendation and slots."""
        try:
            debug_block = (context.get("debug", {}) or {})
            last_params = debug_block.get("last_search_params", {}) or {}
            if isinstance(last_params, dict):
                lpq = str(last_params.get("q", "")).strip()
                if lpq and lpq.upper().find("UNKNOWN") < 0:
                    return lpq
        except Exception:
            pass
        # Derive from last_recommendation products
        try:
            lr = context.get("last_recommendation", {}) or {}
            products = lr.get("products", []) or []
            if isinstance(products, list) and products:
                titles = [str((p or {}).get("title", "")).strip() for p in products[:3]]
                titles = [t for t in titles if t]
                if titles:
                    base = titles[0]
                    # Append simple constraints from user_answers
                    ua = context.get("user_answers", {}) or {}
                    brand = ua.get("brands")
                    diet = ua.get("dietary_requirements")
                    parts = [base]
                    if brand:
                        try:
                            if isinstance(brand, list) and brand:
                                parts.append(str(brand[0]))
                            elif isinstance(brand, str) and brand.strip():
                                parts.append(brand.strip())
                        except Exception:
                            pass
                    if diet:
                        try:
                            if isinstance(diet, list) and diet:
                                parts.append(str(diet[0]).upper())
                            elif isinstance(diet, str) and diet.strip():
                                parts.append(diet.strip().upper())
                        except Exception:
                            pass
                    return " ".join(parts).strip()
        except Exception:
            pass
        # Fallback to user text
        return (current_user_text or context.get("original_query") or "").strip()

    async def _fb_category_classify(self, constructed_q: str) -> Dict[str, Any]:
        """LLM-guided F&B classification using provided taxonomy. Returns {is_fnb, category, subcategory}."""
        FB_CLASSIFY_TOOL = {
            "name": "fb_category_classify",
            "description": "Classify query into food & beverage taxonomy if applicable.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "is_fnb": {"type": "boolean"},
                    "category": {"type": "string"},
                    "subcategory": {"type": "string"}
                },
                "required": ["is_fnb"]
            }
        }

        prompt = (
            "Decide if the query is Food & Beverage, and map to category/subcategory from the taxonomy.\n\n"
            f"Query: {constructed_q}\n\n"
            f"TAXONOMY: {json.dumps(self._fnb_taxonomy, ensure_ascii=False)}\n\n"
            "Return ONLY tool call to fb_category_classify. If not F&B, set is_fnb=false."
        )
        try:
            resp = await self._anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[FB_CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "fb_category_classify"},
                temperature=0,
                max_tokens=200,
            )
            tool_use = self._pick_tool(resp, "fb_category_classify")
            if tool_use and getattr(tool_use, "input", None):
                data = self._strip_keys(getattr(tool_use, "input", {}) or {})
                is_fnb = bool(data.get("is_fnb", False))
                category = str(data.get("category", "")).strip()
                subcat = str(data.get("subcategory", "")).strip()
                return {"is_fnb": is_fnb, "category": category, "subcategory": subcat}
        except Exception as exc:
            log.warning(f"FB_CLASSIFY_FAILED | error={exc}")
        return {"is_fnb": False}
    
    def _heuristic_defaults(self, context: Dict[str, Any], constructed_q: str) -> Dict[str, Any]:
        """Minimal fallback: only query and size to avoid wrong category assumptions."""
        q = (constructed_q or context.get("original_query") or (context.get("session_data", {}) or {}).get("last_query") or "").strip()
        return {"q": q, "size": 20}

    # ------------------------------------------------------------------
    # Backward-compatibility: abstract method implemented (not used now)
    # ------------------------------------------------------------------
    def validate_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deprecated path kept to satisfy the abstract interface.
        The new workflow uses _normalise_es_params (LLM-driven). This method
        applies only minimal guards so instantiation works and callers that
        still reference it won't break.
        """
        out: Dict[str, Any] = {}
        try:
            q = (params.get("q") or context.get("original_query") or "").strip()
            out["q"] = q
            try:
                size = int(params.get("size", 20))
            except Exception:
                size = 20
            out["size"] = max(1, min(50, size))
            # Copy through commonly used fields without heavy parsing
            for k in ["category_group", "brands", "dietary_terms", "price_min", "price_max", "protein_weight", "phrase_boosts", "field_boosts", "sort"]:
                if k in params:
                    out[k] = params[k]
            return out
        except Exception:
            return {"q": (context.get("original_query") or "").strip(), "size": 20}
    
    def _strip_keys(self, obj: Any) -> Any:
        """Recursively trim whitespace around dict keys"""
        if isinstance(obj, dict):
            new: Dict[str, Any] = {}
            for k, v in obj.items():
                key = k.strip() if isinstance(k, str) else k
                new[key] = self._strip_keys(v)
            return new
        if isinstance(obj, list):
            return [self._strip_keys(x) for x in obj]
        return obj
    
    def _pick_tool(self, resp, tool_name: str):
        """
        Extract tool use from Anthropic response.
        Works with SDK content blocks (type == 'tool_use') and is defensive.
        """
        try:
            for block in (resp.content or []):
                # New SDK objects: .type == "tool_use", .name, .input
                btype = getattr(block, "type", None)
                bname = getattr(block, "name", None)
                if btype == "tool_use" and bname == tool_name:
                    return block
                # Extremely defensive: dict-like fallback
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == tool_name:
                    return block
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────
# Factory and Service Manager
# ─────────────────────────────────────────────────────────────

class RecommendationEngineFactory:
    """Factory for creating recommendation engines"""
    
    _engines = {
        "elasticsearch": ElasticsearchRecommendationEngine,
        # "ml_based": MLRecommendationEngine,  # future
        # "hybrid": HybridRecommendationEngine,  # future
    }
    
    @classmethod
    def create_engine(cls, engine_type: str = "elasticsearch") -> BaseRecommendationEngine:
        if engine_type not in cls._engines:
            log.warning(f"Unknown engine type {engine_type}, defaulting to elasticsearch")
            engine_type = "elasticsearch"
        engine_class = cls._engines[engine_type]
        return engine_class()
    
    @classmethod
    def register_engine(cls, name: str, engine_class: type):
        cls._engines[name] = engine_class


class RecommendationService:
    """Main service for handling recommendations"""
    
    def __init__(self, engine_type: str = "elasticsearch"):
        self.engine = RecommendationEngineFactory.create_engine(engine_type)
        self.engine_type = engine_type
    
    async def extract_es_params(self, ctx: UserContext) -> Dict[str, Any]:
        """
        Extract ES parameters - maintains original interface for backward compatibility
        """
        response = await self.engine.extract_search_params(ctx)
        if response.response_type == RecommendationResponseType.ERROR:
            log.error(f"Recommendation engine error: {response.error_message}")
            return {}
        return response.data
    
    async def get_recommendations(self, ctx: UserContext) -> RecommendationResponse:
        """
        Get full recommendation response with metadata
        """
        return await self.engine.extract_search_params(ctx)
    
    def switch_engine(self, engine_type: str):
        """Switch to a different recommendation engine"""
        self.engine = RecommendationEngineFactory.create_engine(engine_type)
        self.engine_type = engine_type
        log.info(f"Switched to recommendation engine: {engine_type}")


# ─────────────────────────────────────────────────────────────
# Compatibility Layer
# ─────────────────────────────────────────────────────────────

_recommendation_service: Optional[RecommendationService] = None

def get_recommendation_service() -> RecommendationService:
    """Get the global recommendation service instance"""
    global _recommendation_service
    if _recommendation_service is None:
        _recommendation_service = RecommendationService()
    return _recommendation_service

def set_recommendation_engine(engine_type: str):
    """Set the global recommendation engine type"""
    global _recommendation_service
    if _recommendation_service is None:
        _recommendation_service = RecommendationService(engine_type)
    else:
        _recommendation_service.switch_engine(engine_type)


# ─────────────────────────────────────────────────────────────
# Future Extension Points (stubs kept for API compatibility)
# ─────────────────────────────────────────────────────────────

class MLRecommendationEngine(BaseRecommendationEngine):
    async def extract_search_params(self, ctx: UserContext) -> RecommendationResponse:
        return RecommendationResponse(
            response_type=RecommendationResponseType.ERROR,
            data={},
            error_message="ML engine not implemented yet"
        )
    
    def validate_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        return params


class HybridRecommendationEngine(BaseRecommendationEngine):
    def __init__(self):
        self.elasticsearch_engine = ElasticsearchRecommendationEngine()
        # self.ml_engine = MLRecommendationEngine()
    
    async def extract_search_params(self, ctx: UserContext) -> RecommendationResponse:
        return await self.elasticsearch_engine.extract_search_params(ctx)
    
    def validate_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        return self.elasticsearch_engine.validate_params(params, context)
