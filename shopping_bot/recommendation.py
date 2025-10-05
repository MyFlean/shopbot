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

Updates (2025-01-XX):
• Added extraction caching for performance
• Improved dietary term normalization
• Added query explanation for debugging
• Enhanced brand handling
"""

from __future__ import annotations

import json
import logging
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from enum import Enum

import anthropic

from .config import get_config
import os
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
# Dietary Normalizations
# ─────────────────────────────────────────────────────────────

DIETARY_NORMALIZATIONS = {
    "palm oil free": ["PALM OIL FREE", "NO PALM OIL"],
    "gluten free": ["GLUTEN FREE", "NO GLUTEN"],
    "vegan": ["VEGAN", "100% VEG", "PLANT BASED"],
    "sugar free": ["SUGAR FREE", "NO ADDED SUGAR", "NO SUGAR"],
    "organic": ["ORGANIC", "100% ORGANIC"],
    "keto": ["KETO", "KETO FRIENDLY", "LOW CARB"],
    "dairy free": ["DAIRY FREE", "NO DAIRY", "LACTOSE FREE"],
}


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
        # Caching disabled for correctness-first behavior
        self._extraction_cache = None
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

        # Allow runtime override via env
        try:
            override = self._load_taxonomy_override()
            if override:
                self._fnb_taxonomy = override
                log.info("FNB_TAXONOMY | loaded override from environment")
        except Exception as exc:
            log.warning(f"FNB_TAXONOMY_OVERRIDE_FAILED | {exc}")
    
    def _normalize_dietary_term(self, term: str) -> List[str]:
        """Normalize a single dietary term to multiple possible variations"""
        term_lower = term.lower().strip()
        
        # Check against known normalizations
        for key, values in DIETARY_NORMALIZATIONS.items():
            if key in term_lower:
                return values
            # Check if any variation matches
            for value in values:
                if value.lower() in term_lower:
                    return values
        
        # Default: just uppercase the term
        return [term.upper()]
    
    def _create_cache_key(self, ctx: UserContext, current_user_text: str) -> str:
        """Create a cache key from context and current text"""
        session = ctx.session or {}
        # Pull the most recent user query from conversation history if available
        try:
            conv = (session.get("conversation_history", []) or [])
            last_turn = conv[-1] if conv else {}
            last_user_query = str((last_turn or {}).get("user_query", ""))
        except Exception:
            last_user_query = ""
        key_parts = [
            str(getattr(ctx, "session_id", "")),
            str(getattr(ctx, "user_id", "")),
            current_user_text or "",
            session.get("last_query", ""),
            last_user_query,
            str(session.get("budget")),
            str(session.get("dietary_requirements")),
            str(session.get("brands")),
        ]
        key_str = ":".join(key_parts)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    async def extract_search_params(self, ctx: UserContext) -> RecommendationResponse:
        """
        Enhanced parameter extraction with better query understanding.
        Focuses on food/product categorization and budget parsing.
        """
        try:
            session = ctx.session or {}
            assessment = session.get("assessment", {})
            
            # Resolve CURRENT user text first (delta-aware)
            current_user_text = self._get_current_user_text(ctx)
            if not current_user_text:
                current_user_text = assessment.get("original_query", "") or session.get("last_query", "") or ""

            # Compute base query from assessment for stability across slots
            base_query = assessment.get("original_query", "") or session.get("last_query", "") or ""

            # Build context for LLM (use BASE query as original_query; current text for constraints)
            context = {
                "original_query": base_query,
                "user_answers": {
                    "budget": session.get("budget"),
                    "preferences": session.get("preferences"),
                    "dietary_requirements": session.get("dietary_requirements"),
                },
                "session_data": session,
                "debug": session.get("debug", {}),
                "last_recommendation": session.get("last_recommendation", {}),
                "conversation_history": session.get("conversation_history", []),
                "current_constraints": {}
            }
            
            # Caching disabled
            
            # 0) Extract constraints from CURRENT user text
            try:
                current_constraints = await self._extract_constraints_from_text(current_user_text)
            except Exception as exc:
                log.warning(f"CURRENT_CONSTRAINTS_FAILED | error={exc}")
                current_constraints = {}
            context["current_constraints"] = current_constraints

            # 1) Construct a context-aware search query via LLM ALWAYS (no gating)
            #    Fallbacks: canonical_query → assessment.original_query → session.last_query → current_user_text
            try:
                constructed_q = await self._construct_search_query(context, current_user_text)
                if not constructed_q or constructed_q.upper().find("<UNKNOWN>") >= 0:
                    raise ValueError("constructed_q_invalid")
            except Exception:
                # Safe fallbacks in priority order
                constructed_q = (
                    str(session.get("canonical_query") or "").strip()
                    or str(assessment.get("original_query") or "").strip()
                    or str(session.get("last_query") or "").strip()
                    or str(current_user_text or "").strip()
                )
            log.info(f"ES_CONSTRUCTED_QUERY | q='{constructed_q}'")
            try:
                # Preview sanitization (no behavior change yet)
                before = constructed_q
                after = before
                for junk in ["products", "options", "show me", "alternatives"]:
                    after = after.replace(junk, "").strip()
                log.info(f"Q_SANITIZATION_PREVIEW | before='{before}' | after='{after}'")
            except Exception:
                pass

            # 2) Category/signals extraction from taxonomy (LLM tool)
            try:
                cat_signals = await self._extract_category_and_signals(constructed_q, self._fnb_taxonomy)
            except Exception as exc:
                log.warning(f"CAT_SIGNAL_EXTRACTION_FAILED | error={exc}")
                cat_signals = {}

            # 3) Ask LLM to emit initial ES params
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
                response = RecommendationResponse(
                    response_type=RecommendationResponseType.ES_PARAMS,
                    data=fallback,
                    metadata={
                        "extraction_method": "fallback_heuristic",
                        "context_keys": list(context.keys()),
                        "source_user_text": current_user_text,
                    },
                    error_message="LLM did not return a tool-call; used heuristics."
                )
                # Caching disabled
                return response
            
            # 4) Normalise params via dedicated LLM tool (no unconditional overlay from session slots)
            final_params = await self._normalise_es_params(params_from_llm, context, constructed_q, current_user_text)
            
            # Overlay only preferences as keywords; do not auto-merge dietary/brands unless current turn affirms
            try:
                if isinstance(session.get('preferences'), str) and session.get('preferences').strip():
                    # preferences become keywords/phrase_boosts
                    ptxt = session.get('preferences').strip()
                    kws = list(final_params.get('keywords') or [])
                    if ptxt.lower() not in [k.lower() for k in kws]:
                        kws.append(ptxt.lower())
                    final_params['keywords'] = kws[:8]
                    boosts = list(final_params.get('phrase_boosts') or [])
                    boosts.append({"title": ptxt, "boost": 1.5})
                    final_params['phrase_boosts'] = boosts[:5]
                # budget already parsed in defaults; ensure pass-through
                if session.get('budget'):
                    pass
            except Exception:
                pass
            try:
                slot = assessment.get("currently_asking")
                log.info(
                    f"NORMALISE_SUMMARY | slot='{slot}' | has_category={bool(final_params.get('category_path'))} | has_price={('price_min' in final_params) or ('price_max' in final_params)}"
                )
            except Exception:
                pass
            
            # 4b) Category carry-over for generic follow-ups
            try:
                is_generic = self._is_generic_followup(current_user_text)
                if is_generic and (not final_params.get("category_path") or "<UNKNOWN>" in str(final_params.get("category_path", ""))):
                    last_params = ((context.get("session_data") or {}).get("debug") or {}).get("last_search_params") or {}
                    last_cat_path = last_params.get("category_path")
                    last_cat_paths = last_params.get("category_paths") if isinstance(last_params.get("category_paths"), list) else []
                    last_fb_cat = last_params.get("fb_category")
                    last_fb_sub = last_params.get("fb_subcategory")
                    if (last_cat_path and "<UNKNOWN>" not in last_cat_path) or last_cat_paths:
                        if last_cat_path:
                            final_params["category_path"] = last_cat_path
                        if last_cat_paths:
                            final_params["category_paths"] = last_cat_paths
                        if last_fb_cat:
                            final_params["fb_category"] = last_fb_cat
                        if last_fb_sub:
                            final_params["fb_subcategory"] = last_fb_sub
                        log.info("CATEGORY_CARRY_OVER_APPLIED | carried multi-paths")
                    else:
                        log.info("CATEGORY_CARRY_OVER_SKIPPED | no_valid_last_path")
                else:
                    log.info(f"CATEGORY_CARRY_OVER_SKIPPED | is_generic={is_generic} | has_valid_path={bool(final_params.get('category_path') and '<UNKNOWN>' not in str(final_params.get('category_path', '')))}")
            except Exception as exc:
                log.warning(f"CATEGORY_CARRY_OVER_ERROR | error={exc}")
            
            # Guard against UNKNOWN/blank q after normalisation
            try:
                qval = str(final_params.get("q", "")).strip()
                if not qval or qval.upper().find("UNKNOWN") >= 0:
                    log.warning("ES_Q_FALLBACK | replacing invalid q with constructed query")
                    final_params["q"] = constructed_q
            except Exception:
                final_params["q"] = constructed_q

            # Deterministic budget inference fallback
            try:
                intent_text = (current_user_text or "").lower()
                has_price = ("price_min" in final_params) or ("price_max" in final_params)
                if not has_price:
                    if any(w in intent_text for w in ["cheap", "cheaper", "budget", "affordable", "lower price", "less expensive"]):
                        inferred = self._derive_price_from_history(context, mode="cheaper")
                        if inferred is not None:
                            final_params["price_max"] = inferred
                            log.info(f"ES_BUDGET_FALLBACK_FROM_HISTORY | price_max={inferred}")
                    elif any(w in intent_text for w in ["premium", "expensive", "higher price", "luxury"]):
                        inferred = self._derive_price_from_history(context, mode="premium")
                        if inferred is not None:
                            final_params["price_min"] = inferred
                            log.info(f"ES_BUDGET_FALLBACK_FROM_HISTORY | price_min={inferred}")
            except Exception as exc:
                log.warning(f"ES_BUDGET_FALLBACK_ERROR | error={exc}")

            # Merge category/signals into final params
            try:
                if isinstance(cat_signals, dict):
                    l1 = str(cat_signals.get("l1", "")).strip()
                    l2 = (cat_signals.get("l2") or cat_signals.get("cat") or "").strip()
                    l3 = (cat_signals.get("l3") or "").strip()

                    if l1 in ("f_and_b", "personal_care"):
                        final_params["category_group"] = l1

                    if not l2:
                        l2 = (final_params.get("fb_category") or "").strip()
                    if not l3:
                        l3 = (final_params.get("fb_subcategory") or "").strip()

                    cat_path = cat_signals.get("cat_path")
                    cat_paths_multi = cat_signals.get("cat_paths") or []
                    cat_path_candidates: List[str] = []
                    if l2:
                        first = f"{l2}/{l3}".rstrip("/") if l3 else l2
                        if first:
                            cat_path_candidates.append(first)
                    else:
                        if isinstance(cat_path, list):
                            joined = "/".join([str(x).strip() for x in cat_path if str(x).strip()])
                            if joined:
                                cat_path_candidates.append(joined)
                        else:
                            raw = str(cat_path or "").strip()
                            if raw:
                                cat_path_candidates.append(raw)
                    # Merge additional probable paths
                    if isinstance(cat_paths_multi, list):
                        for cp in cat_paths_multi:
                            s = str(cp).strip()
                            if s:
                                cat_path_candidates.append(s)
                    # Deduplicate and clamp to top-3
                    dedup: List[str] = []
                    for s in cat_path_candidates:
                        if s not in dedup:
                            dedup.append(s)
                    if dedup:
                        group = final_params.get("category_group") or "f_and_b"
                        final_params["category_group"] = group
                        # Build full paths per group
                        full_paths: List[str] = []
                        for rel in dedup[:3]:
                            if group == "f_and_b":
                                full_paths.append(f"f_and_b/food/{rel}")
                            elif group == "personal_care":
                                full_paths.append(f"personal_care/{rel}")
                            else:
                                full_paths.append(rel)
                        # Backward compatible single path + new multi-paths
                        final_params["category_path"] = full_paths[0]
                        final_params["category_paths"] = full_paths

                    if not final_params.get("category_group"):
                        final_params["category_group"] = "f_and_b"

                    # brands
                    brands = cat_signals.get("brands") or []
                    if isinstance(brands, list) and brands:
                        final_params["brands"] = list({str(b).strip() for b in brands if str(b).strip()})

                    # price range
                    if isinstance(cat_signals.get("price_min"), (int, float)):
                        final_params["price_min"] = float(cat_signals["price_min"])
                    if isinstance(cat_signals.get("price_max"), (int, float)):
                        final_params["price_max"] = float(cat_signals["price_max"])

                    # dietary/health signals with normalization
                    if isinstance(cat_signals.get("dietary_labels"), list) and cat_signals["dietary_labels"]:
                        normalized_dietary = []
                        for term in cat_signals["dietary_labels"]:
                            normalized_dietary.extend(self._normalize_dietary_term(str(term)))
                        final_params["dietary_labels"] = list(set(normalized_dietary))
                    
                    if final_params.get("dietary_terms") and not final_params.get("dietary_labels"):
                        try:
                            normalized_dietary = []
                            for term in (final_params.get("dietary_terms") or []):
                                normalized_dietary.extend(self._normalize_dietary_term(str(term)))
                            final_params["dietary_labels"] = list(set(normalized_dietary))
                        except Exception:
                            pass
                    
                    if isinstance(cat_signals.get("health_claims"), list) and cat_signals["health_claims"]:
                        final_params["health_claims"] = [str(x) for x in cat_signals["health_claims"] if str(x).strip()]

                    # keywords for re-ranking
                    if isinstance(cat_signals.get("keywords"), list) and cat_signals["keywords"]:
                        final_params["keywords"] = [str(x) for x in cat_signals["keywords"] if str(x).strip()]
            except Exception as exc:
                log.debug(f"MERGE_CAT_SIGNALS_FAILED | {exc}")

            # 5) Optional F&B classification
            fb_meta = await self._fb_category_classify(constructed_q)
            if fb_meta.get("is_fnb"):
                final_params["category_group"] = "f_and_b"
                final_params["fb_category"] = fb_meta.get("category")
                final_params["fb_subcategory"] = fb_meta.get("subcategory")
                # Build category_path if possible
                try:
                    cat = str(final_params.get("fb_category", "")).strip()
                    sub = str(final_params.get("fb_subcategory", "")).strip()
                    if cat and sub:
                        final_params["category_path"] = f"f_and_b/food/{cat}/{sub}"
                except Exception:
                    pass
                log.info(f"FB_CLASSIFIED | category={final_params.get('fb_category')} | subcategory={final_params.get('fb_subcategory')} | path={final_params.get('category_path')}")

            # Add query explanation for debugging
            explanation = {
                "extracted_from": "current_query" if current_user_text else "context",
                "filters_applied": len([k for k in final_params if k in ['price_min', 'price_max', 'brands', 'dietary_terms', 'dietary_labels']]),
                "category_inferred": bool(final_params.get('category_path')),
                "using_percentiles": True,
                "dietary_normalized": bool(final_params.get('dietary_labels'))
            }
            
            # Store in session for debugging
            ctx.session.setdefault("debug", {})["query_explanation"] = explanation

            log.info(f"ES_PARAMS_FINAL | keys={list(final_params.keys())}")
            try:
                log.info(f"ES_SEARCH_QUERY_USED | q='{final_params.get('q','')}'")
            except Exception:
                pass
            
            response = RecommendationResponse(
                response_type=RecommendationResponseType.ES_PARAMS,
                data=final_params,
                metadata={
                    "extraction_method": "llm_enhanced",
                    "context_keys": list(context.keys()),
                    "constructed_q": constructed_q,
                    "explanation": explanation,
                    "source_user_text": current_user_text,
                }
            )
            
            # Persist canonical query and last_query for downstream turns
            try:
                if isinstance(final_params.get("q"), str) and final_params["q"].strip():
                    session["canonical_query"] = final_params["q"].strip()
                    session["last_query"] = final_params["q"].strip()
            except Exception:
                pass

            # Caching disabled
            
            return response
            
        except Exception as exc:
            log.warning("Enhanced ES param extraction failed: %s", exc)
            return RecommendationResponse(
                response_type=RecommendationResponseType.ERROR,
                data={},
                error_message=str(exc)
            )

    async def _extract_constraints_from_text(self, text: str) -> Dict[str, Any]:
        """General constraint extractor from CURRENT user text using LLM tool-call."""
        EXTRACT_CONSTRAINTS_TOOL = {
            "name": "extract_current_constraints",
            "description": "From the given text, extract brands, dietary_terms (UPPERCASE), health_claims, keywords (lowercase), and optional price_min/price_max.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "brands": {"type": "array", "items": {"type": "string"}},
                    "dietary_terms": {"type": "array", "items": {"type": "string"}},
                    "dietary_labels": {"type": "array", "items": {"type": "string"}},
                    "health_claims": {"type": "array", "items": {"type": "string"}},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "price_min": {"type": "number"},
                    "price_max": {"type": "number"}
                }
            }
        }

        prompt = (
            "Extract shopping constraints from the CURRENT user text.\n\n"
            f"TEXT: {text}\n\n"
            "Rules:\n"
            "- brands: exact strings as mentioned; avoid guessing.\n"
            "- dietary_terms: map phrases like 'no/without palm oil' → 'PALM OIL FREE'; uppercase all; include VEGAN/GLUTEN FREE/ORGANIC if present.\n"
            "- dietary_labels: same as dietary_terms (duplicate acceptable).\n"
            "- health_claims: short phrases as-is (lowercase if natural).\n"
            "- keywords: 1-6 single words (lowercase), exclude stopwords and brand names.\n"
            "- price_min/price_max: parse ranges or under/over expressions when explicit.\n"
            "Return ONLY tool call to extract_current_constraints."
        )

        try:
            resp = await self._anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[EXTRACT_CONSTRAINTS_TOOL],
                tool_choice={"type": "tool", "name": "extract_current_constraints"},
                temperature=0,
                max_tokens=250,
            )
            tool_use = self._pick_tool(resp, "extract_current_constraints")
            if tool_use and getattr(tool_use, "input", None):
                data = self._strip_keys(getattr(tool_use, "input", {}) or {})
                
                # Apply dietary normalizations
                if isinstance(data.get("dietary_terms"), list):
                    normalized = []
                    for term in data["dietary_terms"]:
                        normalized.extend(self._normalize_dietary_term(str(term)))
                    data["dietary_terms"] = list(set(normalized))
                
                if isinstance(data.get("dietary_labels"), list):
                    normalized = []
                    for term in data["dietary_labels"]:
                        normalized.extend(self._normalize_dietary_term(str(term)))
                    data["dietary_labels"] = list(set(normalized))
                
                if isinstance(data.get("keywords"), list):
                    data["keywords"] = [str(x).strip().lower() for x in data["keywords"] if str(x).strip()]
                if isinstance(data.get("brands"), list):
                    data["brands"] = [str(x).strip() for x in data["brands"] if str(x).strip()]
                
                return data
        except Exception as exc:
            log.debug(f"CONSTRAINT_EXTRACT_FAIL | {exc}")
        return {}
    
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
    
    async def _normalise_es_params(self, params: Dict[str, Any], context: Dict[str, Any], constructed_q: str, current_user_text: str) -> Dict[str, Any]:
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

        # Extract last recommendation prices for LLM context
        try:
            lr_prices = []
            lr = context.get("last_recommendation", {}) or {}
            for p in (lr.get("products", []) or [])[:12]:
                val = p.get("price")
                if isinstance(val, (int, float)):
                    lr_prices.append(float(val))
                elif isinstance(val, str):
                    cleaned = ''.join(ch for ch in val if (ch.isdigit() or ch=='.'))
                    if cleaned:
                        lr_prices.append(float(cleaned))
        except Exception:
            lr_prices = []

        # Provide richer context to the LLM normaliser
        try:
            convo_hist = [
                {
                    "user_query": (h or {}).get("user_query"),
                    "bot_reply": ((h or {}).get("final_answer", {}) or {}).get("message_full")
                }
                for h in (context.get("conversation_history", []) or [])[-3:]
            ]
            last_params_hint = (context.get("debug", {}) or {}).get("last_search_params", {}) or {}
        except Exception:
            convo_hist = []
            last_params_hint = {}

        prompt = (
            "You are normalising ES parameters using prior conversation context.\n\n"
            f"CURRENT_USER_TEXT: {current_user_text}\n"
            f"Constructed query: {constructed_q}\n"
            f"Current params (pre-normalisation): {json.dumps(params, ensure_ascii=False)}\n"
            f"Recent turns (user↔bot): {json.dumps(convo_hist, ensure_ascii=False)}\n"
            f"Last search params hint: {json.dumps(last_params_hint, ensure_ascii=False)}\n"
            f"CURRENT_CONSTRAINTS: {json.dumps(context.get('current_constraints', {}), ensure_ascii=False)}\n"
            f"Recent prices from last_recommendation (if any): {lr_prices}\n\n"
            "General rules:\n"
            "- Clamp size to [1,50]; ensure price_min<=price_max; uppercase dietary terms.\n"
            "- If dietary_terms == ['ANY'] then DROP dietary_terms.\n"
            "- If q is empty or placeholder, set q to the constructed query.\n"
            "- Use conversation context to infer missing constraints when user implies a delta (e.g., brand/color/dietary/price).\n"
            "- If CURRENT_USER_TEXT includes phrases like 'no palm oil', 'without palm oil', set dietary_terms to include 'PALM OIL FREE'.\n"
            "- If CURRENT_USER_TEXT does NOT mention a brand and asks for 'options'/'alternatives', REMOVE any brands carried from previous turns.\n"
            "- For 'cheaper/budget' with no explicit price → infer price_max from historical prices (e.g., 0.8×median or 25th percentile, rounded to 10/50).\n"
            "- For 'premium/more expensive' with no explicit price → infer price_min similarly (e.g., 1.2×median or 75th percentile).\n"
            "- If user implies brand or color changes in CURRENT_USER_TEXT or last turn, reflect that in brands or append to q (phrase boosts allowed).\n"
            "- Optionally add phrase_boosts (e.g., title:\"blue chips\" boost 2.0) and field_boosts (brand^2, title^1.5) when signals exist.\n"
            "- Keep outputs as a clean JSON object for the normalise_es_params tool.\n\n"
            "Examples:\n"
            "- Text: 'cheaper options' + prices [120, 90, 60, 45] → set price_max ≈ 50.\n"
            "- Text: 'premium choices' + prices [120, 90, 60, 45] → set price_min ≈ 110.\n"
            "- Text: 'show me Lays only' → brands=['Lays'] and/or phrase_boosts on brand/title.\n"
            "- Text: 'make them VEGAN' → dietary_terms=['VEGAN'].\n"
            "- Text: 'spicy chips without palm oil' → dietary_terms=['PALM OIL FREE']; DO NOT set brands.\n"
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
        # Remove price/currency tokens from q if present
        def _strip_price_tokens(text: str) -> str:
            try:
                t = text
                # common currency/price words
                for tok in ["₹", "rs.", "rupees", "rs", "under", "over", "below", "above", "less than", "more than"]:
                    t = t.replace(tok, " ")
                # remove standalone numbers
                import re as _re
                t = _re.sub(r"\b\d+[\d,\.]*\b", " ", t)
                return " ".join(t.split()).strip()
            except Exception:
                return text
        raw_q = (norm.get("q") or constructed_q or "").strip()
        out["q"] = _strip_price_tokens(raw_q)
        try:
            out["size"] = max(1, min(10, int(norm.get("size", 10))))
        except Exception:
            out["size"] = 10
        if isinstance(norm.get("price_min"), (int, float)):
            out["price_min"] = float(norm["price_min"])
        if isinstance(norm.get("price_max"), (int, float)):
            out["price_max"] = float(norm["price_max"])
        if "price_min" in out and "price_max" in out and out["price_min"] > out["price_max"]:
            out["price_min"], out["price_max"] = out["price_max"], out["price_min"]
        
        # Lists with normalization
        for lf in ["brands", "dietary_terms"]:
            items = norm.get(lf)
            if isinstance(items, list):
                if lf == "dietary_terms":
                    normalized = []
                    for term in items:
                        if str(term).strip().upper() != "ANY":
                            normalized.extend(self._normalize_dietary_term(str(term)))
                    if normalized:
                        out[lf] = list(set(normalized))
                else:
                    cleaned = [str(x).strip() for x in items if str(x).strip()]
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
        # carry product_intent if present for downstream size logic
        if isinstance(norm.get("product_intent"), str):
            out["product_intent"] = norm["product_intent"].strip()

        # Merge CURRENT_CONSTRAINTS from this turn
        try:
            constraints = context.get("current_constraints", {}) or {}
            # Brands: if explicitly present for this turn, replace previous brands
            if isinstance(constraints.get("brands"), list) and constraints["brands"]:
                out["brands"] = [str(b).strip() for b in constraints["brands"] if str(b).strip()]
            # Dietary terms/labels and health claims: union with priority to current turn
            def _merge_list(key: str, transform=None):
                cur = constraints.get(key)
                if isinstance(cur, list) and cur:
                    vals = [str(x).strip() for x in cur if str(x).strip()]
                    if transform:
                        vals = [transform(x) for x in vals]
                    prev = out.get(key, []) if isinstance(out.get(key), list) else []
                    out[key] = list({*prev, *vals})
            
            # Apply normalization to dietary merges
            if isinstance(constraints.get("dietary_terms"), list) and constraints["dietary_terms"]:
                normalized = []
                for term in constraints["dietary_terms"]:
                    normalized.extend(self._normalize_dietary_term(str(term)))
                prev = out.get("dietary_terms", []) if isinstance(out.get("dietary_terms"), list) else []
                out["dietary_terms"] = list(set(prev + normalized))
            
            if isinstance(constraints.get("dietary_labels"), list) and constraints["dietary_labels"]:
                normalized = []
                for term in constraints["dietary_labels"]:
                    normalized.extend(self._normalize_dietary_term(str(term)))
                prev = out.get("dietary_labels", []) if isinstance(out.get("dietary_labels"), list) else []
                out["dietary_labels"] = list(set(prev + normalized))
            
            _merge_list("health_claims", lambda x: x)
            _merge_list("keywords", lambda x: x.lower())
            # Prices from this turn override
            if isinstance(constraints.get("price_min"), (int, float)):
                out["price_min"] = float(constraints["price_min"])
            if isinstance(constraints.get("price_max"), (int, float)):
                out["price_max"] = float(constraints["price_max"])
        except Exception:
            pass

        # Hard guards: derive dietary_terms from CURRENT_USER_TEXT if missing
        try:
            text_lower = (current_user_text or "").lower()
            for key_phrase, normalized_values in DIETARY_NORMALIZATIONS.items():
                if key_phrase in text_lower and not out.get("dietary_terms"):
                    out["dietary_terms"] = normalized_values
                    break
        except Exception:
            pass

        # Brand carry-over guard for generic option/alternate requests
        try:
            text_lower = (current_user_text or "").lower()
            generic_markers = ["options", "alternatives", "alternate", "show me options", "show me alternate"]
            mentions_brand = False
            existing_brands = [b for b in out.get("brands", [])] if isinstance(out.get("brands"), list) else []
            for b in existing_brands:
                if isinstance(b, str) and b.strip() and b.strip().lower() in text_lower:
                    mentions_brand = True
                    break
            if (not mentions_brand) and any(g in text_lower for g in generic_markers):
                out.pop("brands", None)
        except Exception:
            pass

        # Log fields inferred compared to input
        try:
            added = [k for k in out.keys() if k not in params]
            changed = [k for k in out.keys() if k in params and out[k] != params[k]]
            if added or changed:
                log.info(f"ES_INFERRED_FROM_CONTEXT | added={added} | changed={changed}")
        except Exception:
            pass
        return out

    def _derive_price_from_history(self, context: Dict[str, Any], mode: str) -> Optional[float]:
        """Compute a price threshold from last_recommendation: cheaper→price_max; premium→price_min."""
        try:
            lr = context.get("last_recommendation", {}) or {}
            vals: List[float] = []
            for p in (lr.get("products", []) or [])[:20]:
                raw = p.get("price")
                num = None
                if isinstance(raw, (int, float)):
                    num = float(raw)
                elif isinstance(raw, str):
                    cleaned = ''.join(ch for ch in raw if (ch.isdigit() or ch=='.'))
                    if cleaned:
                        num = float(cleaned)
                if num is not None and num > 0:
                    vals.append(num)
            if not vals:
                return None
            vals.sort()
            n = len(vals)
            median = vals[n//2] if n % 2 == 1 else (vals[n//2 - 1] + vals[n//2]) / 2
            p25 = vals[max(0, int(0.25 * (n-1)))]
            p75 = vals[max(0, int(0.75 * (n-1)))]
            if mode == "cheaper":
                target = min(p25, 0.8 * median)
                return self._round_price(target, down=True)
            if mode == "premium":
                target = max(p75, 1.2 * median)
                return self._round_price(target, down=False)
        except Exception:
            return None
        return None

    def _round_price(self, value: float, down: bool = True) -> float:
        """Round price to a sensible step: 10 for <500; 50 for 500-2000; 100 for >2000."""
        step = 10 if value < 500 else (50 if value < 2000 else 100)
        if down:
            return float(max(step, (int(value // step)) * step))
        return float(((int((value + step - 1) // step)) * step))

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
        # Extract last category context for delta-aware construction
        last_params = ((context.get("session_data") or {}).get("debug") or {}).get("last_search_params") or {}
        last_category = last_params.get("fb_category") or last_params.get("category_group") or ""
        last_subcategory = last_params.get("fb_subcategory") or ""
        
        # Extract last product names for category anchoring
        last_rec = context.get("last_recommendation") or {}
        last_product_names = []
        try:
            for p in (last_rec.get("products") or [])[:3]:
                name = str(p.get("name") or "").strip()
                if name:
                    # Extract product type nouns (e.g., "ketchup" from "Del Monte Classic Blend Tomato Ketchup")
                    words = name.lower().split()
                    for noun in ["ketchup", "chips", "juice", "candy", "chocolate", "soap", "shampoo", "cream", "oil", "powder"]:
                        if noun in words:
                            last_product_names.append(noun)
                            break
        except Exception:
            pass
        
        try:
            log.info(f"CONSTRUCT_QUERY_INPUT | current='{current_user_text}' | last_category='{last_category}' | last_subcategory='{last_subcategory}' | product_nouns={last_product_names}")
        except Exception:
            pass

        # Format conversation with recency weights
        formatted_history = []
        convo = context.get('conversation_history', [])
        total = len(convo)
        for idx, turn in enumerate(convo[-10:]):
            weight = "MOST_RECENT" if idx >= total - 2 else "RECENT" if idx >= total - 5 else "OLDER"
            formatted_history.append({
                "weight": weight,
                "user": turn.get("user_query", "")[:120],
                "bot": turn.get("bot_reply", "")[:100]
            })

        prompt = f"""<task_definition>
You are a search query constructor for a WhatsApp shopping bot. Build a coherent product search phrase that maintains continuity across conversations.
</task_definition>

<inputs>
<current_message>{current_user_text}</current_message>

<conversation_history>
{json.dumps(formatted_history, ensure_ascii=False, indent=2)}
</conversation_history>

<last_product_context>
Category: {last_category} | Subcategory: {last_subcategory} | Products: {last_product_names}
</last_product_context>

<current_constraints>
{json.dumps(context.get('current_constraints', {}), ensure_ascii=False, indent=2)}
</current_constraints>
</inputs>

<reasoning_process>
Think through these steps:
1. CONVERSATION FLOW: What was the product focus in last 2-3 turns?
2. MESSAGE TYPE: Is this REFINEMENT (adding constraints) or NEW SEARCH (different product)?
3. ANCHOR: What is the core product noun?
4. CONSTRAINTS: What new filters appear (brand, dietary, price, attribute)?
5. CONSTRUCT: Combine anchor + modifiers, keep 2-6 words, noun-led
</reasoning_process>

<rules>
<rule priority="critical">ANCHOR PERSISTENCE: Keep same product noun for follow-ups unless explicitly changed.
- "shampoo" → "dry scalp" → "dry scalp shampoo" ✓
- "chips" → "banana" → "banana chips" ✓
- "noodles" → "gluten free" → "gluten free noodles" ✓
</rule>

<rule priority="critical">FIELD SEPARATION: Never put budget/dietary/brand in query text.
- ❌ "gluten free chips under 100" 
- ✓ q: "gluten free chips", price_max: 100
</rule>

<rule priority="high">BRAND HANDLING: Drop brands for "options/alternatives" unless re-mentioned.
- "Lays chips" → "show me options" → "chips" (drop Lays) ✓
</rule>
</rules>

<examples>
<example>
Conversation: User: "shampoo" → Bot: [shows shampoos] → User: "dry scalp"
Output: {{"query": "dry scalp shampoo"}}
</example>

<example>
Conversation: User: "chips" → Bot: [shows chips] → User: "banana"
Output: {{"query": "banana chips"}}
</example>

<example>
Conversation: User: "noodles" → Bot: [shows noodles] → User: "gluten free under 100"
Output: {{"query": "gluten free noodles"}}
Note: price_max goes to separate field
</example>

<example>
Conversation: User: "Lays chips" → Bot: [shows Lays] → User: "show me other options"
Output: {{"query": "chips"}}
Note: Brand dropped per rule
</example>
</examples>

<output>Return ONLY tool call to construct_search_query. Query must be 2-6 words, noun-led, no prices/brands.</output>"""
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
                try:
                    log.info(f"CONSTRUCT_QUERY_OUTPUT | q='{q}'")
                except Exception:
                    pass
                return q
        except Exception as exc:
            log.warning(f"CONSTRUCT_QUERY_FAILED | error={exc}")
        # Fallback composition from available signals
        try:
            composed = self._compose_query_from_context(context, current_user_text)
            if composed:
                log.info(f"CONSTRUCT_QUERY_FALLBACK | q='{composed}'")
                return composed
            try:
                log.info(f"CONSTRUCT_QUERY_FALLBACK_EMPTY")
            except Exception:
                pass
        except Exception:
            pass
        q = context.get("original_query") or (context.get("session_data", {}) or {}).get("last_query") or current_user_text or ""
        result = str(q).strip()
        try:
            log.info(f"CONSTRUCT_QUERY_FINAL_FALLBACK | q='{result}'")
        except Exception:
            pass
        return result

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
                                normalized = self._normalize_dietary_term(str(diet[0]))
                                if normalized:
                                    parts.append(normalized[0])
                            elif isinstance(diet, str) and diet.strip():
                                normalized = self._normalize_dietary_term(diet.strip())
                                if normalized:
                                    parts.append(normalized[0])
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
            for k in [
                "category_group",
                "brands",
                "dietary_terms",
                "price_min",
                "price_max",
                "protein_weight",
                "phrase_boosts",
                "field_boosts",
                "sort",
                "dietary_labels",
                "health_claims",
                "keywords",
                "category_path",
                "product_intent",
            ]:
                if k in params and params[k] is not None:
                    out[k] = params[k]
            return out
        except Exception:
            return {"q": (context.get("original_query") or "").strip(), "size": 20}

    def _load_taxonomy_override(self) -> Optional[Dict[str, Any]]:
        """Load F&B taxonomy override from env JSON or file path."""
        path = os.getenv("FNB_TAXONOMY_PATH")
        raw = os.getenv("FNB_TAXONOMY_JSON")
        data: Optional[Dict[str, Any]] = None
        if path and isinstance(path, str) and path.strip():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                raise RuntimeError(f"Failed to load taxonomy file '{path}': {e}")
        elif raw and isinstance(raw, str) and raw.strip():
            try:
                data = json.loads(raw)
            except Exception as e:
                raise RuntimeError(f"Failed to parse FNB_TAXONOMY_JSON: {e}")

        if data is None:
            return None

        # Basic validation
        if not isinstance(data, dict):
            raise ValueError("Taxonomy override must be a JSON object mapping category → list[subcategories]")
        for k, v in data.items():
            if not isinstance(k, str) or not isinstance(v, list):
                raise ValueError("Invalid taxonomy format: keys must be strings and values must be lists")
        return data

    def _get_current_user_text(self, ctx: UserContext) -> str:
        """Best-effort extraction of the current turn's user text for delta-aware caching and prompts."""
        for attr in [
            "current_user_text",
            "current_text",
            "message_text",
            "user_message",
            "last_user_message",
            "text",
            "message",
        ]:
            try:
                value = getattr(ctx, attr, None)
                if isinstance(value, str) and value.strip():
                    try:
                        log.info(f"REC_TEXT_ATTR | attr={attr} | value='{value.strip()}'")
                    except Exception:
                        pass
                    return value.strip()
            except Exception:
                pass
        try:
            session = ctx.session or {}
            for key in [
                "current_user_text",
                "latest_user_text",
                "last_user_message",
                "last_user_text",
                "last_query",
            ]:
                val = session.get(key)
                if isinstance(val, str) and val.strip():
                    try:
                        log.info(f"REC_TEXT_SESSION | key={key} | value='{val.strip()}'")
                    except Exception:
                        pass
                    return val.strip()
            # Also check debug payload if present
            debug = session.get("debug", {}) or {}
            val = debug.get("current_user_text")
            if isinstance(val, str) and val.strip():
                try:
                    log.info(f"REC_TEXT_DEBUG | value='{val.strip()}'")
                except Exception:
                    pass
                return val.strip()
        except Exception:
            pass
        try:
            assessment = (getattr(ctx, "session", {}) or {}).get("assessment", {}) or {}
            val = assessment.get("original_query")
            if isinstance(val, str) and val.strip():
                try:
                    log.info(f"REC_TEXT_ASSESSMENT | value='{val.strip()}'")
                except Exception:
                    pass
                return val.strip()
        except Exception:
            pass
        try:
            log.info("REC_TEXT_FALLBACK_EMPTY")
        except Exception:
            pass
        return ""
    
    def _is_generic_followup(self, text: str) -> bool:
        """Check if the current text is a generic constraint/modifier without explicit product nouns."""
        if not text:
            return False
        text_lower = text.lower().strip()
        # Generic markers: constraints, modifiers, budget terms, but no explicit product categories
        generic_markers = {
            "options", "more", "cheaper", "budget", "affordable", "premium", "expensive",
            "sugar free", "no sugar", "no added sugar", "healthier", "healthy", "cleaner",
            "baked", "fried", "organic", "vegan", "gluten free", "no palm oil",
            "under", "over", "below", "above", "less than", "more than",
            "show me", "give me", "find me", "alternatives", "alternate"
        }
        # Product nouns that indicate a category shift
        product_nouns = {
            "ketchup", "chips", "juice", "candy", "chocolate", "soap", "shampoo", 
            "cream", "oil", "powder", "biscuit", "cookie", "cake", "bread", "milk"
        }
        
        has_generic = any(marker in text_lower for marker in generic_markers)
        has_product_noun = any(noun in text_lower for noun in product_nouns)
        
        # Generic if it has constraint markers but no new product nouns
        return has_generic and not has_product_noun
    
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
3. dietary_terms: Extract from the CURRENT user text and context. Map phrases like "no palm oil", "without palm oil", "palm oil free" → "PALM OIL FREE" (UPPERCASE). Include other terms like "GLUTEN FREE", "VEGAN", "ORGANIC" (UPPERCASE).
4. price_min/price_max: Parse budget expressions:
   - "100 rupees" → price_max: 100
   - "under 200" → price_max: 200  
   - "50-150" → price_min: 50, price_max: 150
   - "0-200 rupees" → price_min: 0, price_max: 200
5. brands: Extract brand names ONLY if explicitly mentioned in the CURRENT user text. Do NOT carry over brands from previous turns when the user asks for generic "options/alternatives".
6. size: Default 20, max 50

EXAMPLES:
- "gluten free bread under 100 rupees" → category_group: "f_and_b", dietary_terms: ["GLUTEN FREE"], price_max: 100
- "organic snacks 50-200" → category_group: "f_and_b", dietary_terms: ["ORGANIC"], price_min: 50, price_max: 200

FOLLOW-UP HANDLING:
- If the last turn was about a specific brand but the CURRENT user text asks for "options/alternatives" without re-mentioning that brand, DROP brands.
- Always include dietary constraints present in the CURRENT user text.

Return ONLY the tool call to emit_es_params.
"""

    async def _extract_category_and_signals(self, user_query: str, taxonomy: Dict[str, Any]) -> Dict[str, Any]:
        """LLM tool-call function for category extraction."""
        EXTRACT_TOOL = {
            "name": "extract_category_signals",
            "description": "Given user text and taxonomy, emit hierarchical category (l1/l2/l3), cat_path, brand filters, price range, dietary/health signals, and keywords.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "l1": {"type": "string", "enum": ["f_and_b", "personal_care"]},
                    "l2": {"type": "string"},
                    "l3": {"type": "string"},
                    "cat": {"type": "string"},
                    "cat_path": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "cat_paths": {"type": "array", "items": {"type": "string"}},
                    "brands": {"type": "array", "items": {"type": "string"}},
                    "price_min": {"type": "number"},
                    "price_max": {"type": "number"},
                    "dietary_labels": {"type": "array", "items": {"type": "string"}},
                    "health_claims": {"type": "array", "items": {"type": "string"}},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["l1"],
            },
        }

        prompt = (
            "Extract hierarchical category and ES signals.\n\n"
            f"QUERY: {user_query}\n\n"
            f"F_AND_B TAXONOMY: {json.dumps(taxonomy, ensure_ascii=False)}\n\n"
            "Hierarchy and mapping rules:\n"
            "1) l1 (category_group) MUST be exactly one of: f_and_b, personal_care.\n"
            "   - If the query is about foods, snacks, beverages, groceries → l1=f_and_b.\n"
            "   - If the query is about skincare, facewash, creams, shampoos, personal products → l1=personal_care.\n"
            "2) For l1=f_and_b, pick l2 from the top-level keys in the taxonomy (e.g., light_bites, refreshing_beverages, etc.).\n"
            "3) For l1=f_and_b, pick l3 from the corresponding subcategory list (e.g., chips_and_crisps under light_bites).\n"
            "4) If the query suggests snacks like chips, nachos, namkeen, popcorn → l2=light_bites; then choose l3 when appropriate.\n"
            "5) Build cat_path as [l2, l3] when l3 exists; else just [l2].\n"
            "6) If the query is GENERIC (e.g., 'healthy snacks', 'evening snacks', 'breakfast options', 'snack ideas'), return 2-3 PROBABLE cat_path values (array).\n"
            "   - Examples:\n"
            "     • 'healthy snacks' → cat_path: ['light_bites/chips_and_crisps', 'light_bites/popcorn', 'dry_fruits_nuts_and_seeds']\n"
            "     • 'evening snacks' → cat_path: ['light_bites/chips_and_crisps', 'light_bites/savory_namkeen', 'light_bites/popcorn']\n"
            "     • 'breakfast options' → cat_path: ['breakfast_essentials/breakfast_cereals', 'breakfast_essentials/muesli_and_oats', 'dairy_and_bakery/bread_and_buns']\n"
            "6) brands: include any explicit brand mentions; keep original casing.\n"
            "7) price_min/price_max: parse ranges like 'under 60', '50-100'.\n"
            "8) dietary_labels: emit UPPERCASE terms (e.g., PALM OIL FREE, GLUTEN FREE, VEGAN) if present.\n"
            "9) health_claims: short phrases as mentioned.\n"
            "10) keywords: 1-5 single words (lowercase) useful for re-ranking (exclude stop-words).\n"
            "Return ONLY the tool call to extract_category_signals."
        )

        try:
            resp = await self._anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[EXTRACT_TOOL],
                tool_choice={"type": "tool", "name": "extract_category_signals"},
                temperature=0,
                max_tokens=300,
            )
            tool_use = self._pick_tool(resp, "extract_category_signals")
            if tool_use and getattr(tool_use, "input", None):
                data = self._strip_keys(getattr(tool_use, "input", {}) or {})
                # Normalise outputs minimally
                if isinstance(data.get("dietary_labels"), list):
                    data["dietary_labels"] = [str(x).upper() for x in data["dietary_labels"] if str(x).strip()]
                if isinstance(data.get("keywords"), list):
                    data["keywords"] = [str(x).lower() for x in data["keywords"] if str(x).strip()]
                # Build cat_path from l2/l3 when available
                l2 = (data.get("l2") or data.get("cat") or "").strip()
                l3 = (data.get("l3") or "").strip()
                if l2 and not data.get("cat_path"):
                    data["cat_path"] = [l2] + ([l3] if l3 else [])
                # Ensure cat_paths is an array of strings if provided
                if isinstance(data.get("cat_paths"), list):
                    data["cat_paths"] = [str(x).strip() for x in data["cat_paths"] if str(x).strip()]
                return data
        except Exception as exc:
            log.warning(f"CAT_SIGNAL_TOOL_FAILED | error={exc}")
        return {}

    def _strip_keys(self, obj: Any) -> Any:
        """Recursively trim whitespace around dict keys and values."""
        if isinstance(obj, dict):
            new: Dict[str, Any] = {}
            for k, v in obj.items():
                key = k.strip() if isinstance(k, str) else k
                new[key] = self._strip_keys(v)
            return new
        if isinstance(obj, list):
            return [self._strip_keys(x) for x in obj]
        if isinstance(obj, str):
            return obj.strip()
        return obj

    def _pick_tool(self, resp, tool_name: str):
        """Extract tool use from Anthropic response (robust to SDK formats)."""
        try:
            for block in (resp.content or []):
                btype = getattr(block, "type", None)
                bname = getattr(block, "name", None)
                if btype == "tool_use" and bname == tool_name:
                    return block
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == tool_name:
                    return block
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────
# Service Factory (restored for llm_service import)
# ─────────────────────────────────────────────────────────────

class RecommendationService:
    """Facade over the recommendation engine used by LLMService."""
    def __init__(self, engine_type: str = "elasticsearch") -> None:
        # Currently only one engine; hook for future extensibility
        self.engine = ElasticsearchRecommendationEngine()
        self.engine_type = engine_type

    async def extract_es_params(self, ctx: UserContext) -> Dict[str, Any]:
        from .config import get_config as _cfg
        cfg = _cfg()
        if getattr(cfg, "USE_TWO_CALL_ES_PIPELINE", False):
            # Two-call pipeline: delegate ES planning to LLMService once
            try:
                from .llm_service import LLMService  # lazy import
                llm = LLMService()
                # Read current text and product_intent from session
                product_intent = str(ctx.session.get("product_intent") or "show_me_options")
                plan = await llm.plan_es_search(ctx.session.get("current_user_text") or ctx.session.get("last_user_message") or "", ctx, product_intent=product_intent)
                es_params = (plan or {}).get("es_params") or {}
                # Preserve composed dietary_terms if present (do not override downstream)
                try:
                    composed_dt = ((ctx.session.get("debug", {}) or {}).get("composed_dietary_terms") or []) if isinstance(ctx.session.get("debug"), dict) else []
                    if (isinstance(es_params.get("dietary_terms"), list) and es_params.get("dietary_terms")):
                        pass
                    elif composed_dt:
                        es_params["dietary_terms"] = composed_dt
                except Exception:
                    pass
                # Guard/normalize minimal fields (server-side safety)
                out: Dict[str, Any] = {}
                # Defensive: during an active assessment, prefer assessment.original_query as anchor for generic modifier turns
                try:
                    assessment = ctx.session.get("assessment", {}) or {}
                    current_text = (ctx.session.get("current_user_text") or ctx.session.get("last_user_message") or "").strip().lower()
                    generic_markers = ["under", "over", "below", "above", "cheaper", "premium", "gluten", "vegan", "no palm", "baked", "fried", "options", "alternatives", "alternate", "show me"]
                    has_product_noun = any(n in current_text for n in ["ketchup", "chips", "juice", "soap", "shampoo", "bread", "milk", "biscuit", "cookie", "chocolate", "noodles", "vermicelli"])
                    is_generic = any(m in current_text for m in generic_markers) and not has_product_noun
                except Exception:
                    assessment = {}
                    is_generic = False
                preferred_anchor = str(assessment.get("original_query") or "").strip()
                q = str(es_params.get("q") or (preferred_anchor if (preferred_anchor and is_generic) else "") or ctx.session.get("canonical_query") or ctx.session.get("last_query") or "").strip()
                if q:
                    out["q"] = q
                # Copy-through allowed keys
                for k in [
                    "size", "category_group", "category_path", "category_paths",
                    "brands", "dietary_terms", "price_min", "price_max",
                    "keywords", "phrase_boosts", "field_boosts"
                ]:
                    if es_params.get(k) is not None:
                        out[k] = es_params[k]
                # Clamp size
                try:
                    size = int(out.get("size", 10))
                except Exception:
                    size = 10
                out["size"] = max(1, min(50, size))
                # Uppercase dietary terms
                if isinstance(out.get("dietary_terms"), list):
                    out["dietary_terms"] = [str(x).upper() for x in out["dietary_terms"] if str(x).strip()]
                # Sanitize category_group
                try:
                    valid_groups = {"f_and_b", "personal_care", "health_nutrition", "home_kitchen", "electronics"}
                    cg_raw = str(out.get("category_group", "")).strip().lower()
                    if cg_raw not in valid_groups:
                        # Infer from category_path(s) if available
                        inferred = None
                        cp = str(out.get("category_path", "")).strip().lower()
                        if cp.startswith("f_and_b/"):
                            inferred = "f_and_b"
                        elif cp.startswith("personal_care/"):
                            inferred = "personal_care"
                        if not inferred and isinstance(out.get("category_paths"), list):
                            for pth in out["category_paths"]:
                                s = str(pth).strip().lower()
                                if s.startswith("f_and_b/"):
                                    inferred = "f_and_b"; break
                                if s.startswith("personal_care/"):
                                    inferred = "personal_care"; break
                        # Heuristic from q
                        if not inferred:
                            ql = (out.get("q") or "").lower()
                            if any(tok in ql for tok in ["chips", "snack", "juice", "bread", "milk", "chocolate", "ketchup", "biscuit", "cookie"]):
                                inferred = "f_and_b"
                        out["category_group"] = inferred or ctx.session.get("category_group") or "f_and_b"
                except Exception:
                    out["category_group"] = ctx.session.get("category_group") or "f_and_b"
                log.info(f"ES_PARAMS_PLANNED | keys={list(out.keys())}")
                # Persist a hint for debugging
                ctx.session.setdefault("debug", {})["last_search_params"] = out
                return out
            except Exception as exc:
                log.warning(f"Two-call ES planning failed, falling back: {exc}")
                # Fall back to legacy extraction below
        # Legacy multi-call pipeline
        resp = await self.engine.extract_search_params(ctx)
        if resp.response_type == RecommendationResponseType.ERROR:
            log.error(f"Recommendation engine error: {resp.error_message}")
            return {}
        return resp.data


_recommendation_service: Optional[RecommendationService] = None

def get_recommendation_service() -> RecommendationService:
    """Return a singleton RecommendationService instance."""
    global _recommendation_service
    if _recommendation_service is None:
        _recommendation_service = RecommendationService()
    return _recommendation_service

def set_recommendation_engine(engine_type: str) -> None:
    """Swap engine type (placeholder for future multiple engines)."""
    global _recommendation_service
    _recommendation_service = RecommendationService(engine_type)