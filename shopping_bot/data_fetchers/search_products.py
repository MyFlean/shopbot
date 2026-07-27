# shopping_bot/data_fetchers/search_products.py
"""
Chat/stream product search — Search V2 only.

Registers BackendFunction.SEARCH_PRODUCTS and related stub handlers.
Param planning (build_search_params) is shared with the conversational flow;
retrieval always goes through search_v2.extension.search.search().
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from ..enums import BackendFunction
import logging

from . import register_fetcher

log = logging.getLogger(__name__)


def _get_current_user_text(ctx) -> str:
    """Best-effort extraction of the CURRENT user utterance.

    Falls back through several likely attributes/locations and finally to any
    previously stored session text. This is critical to ensure each follow-up
    rebuilds ES params using the latest user intent delta.
    """
    # Direct attributes on context
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
                    print(f"DEBUG: CURRENT_TEXT_ATTR | attr={attr} | value='{value.strip()}'")
                except Exception:
                    pass
                return value.strip()
        except Exception:
            pass

    # Common session keys where pipelines may store the last turn text
    try:
        session = getattr(ctx, "session", {}) or {}
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
                    print(f"DEBUG: CURRENT_TEXT_SESSION | key={key} | value='{val.strip()}'")
                except Exception:
                    pass
                return val.strip()
    except Exception:
        pass

    # Assessment-level fallbacks
    try:
        assessment = (getattr(ctx, "session", {}) or {}).get("assessment", {}) or {}
        val = assessment.get("original_query")
        if isinstance(val, str) and val.strip():
            try:
                print(f"DEBUG: CURRENT_TEXT_ASSESSMENT | value='{val.strip()}'")
            except Exception:
                pass
            return val.strip()
    except Exception:
        pass

    try:
        print("DEBUG: CURRENT_TEXT_FALLBACK_EMPTY")
    except Exception:
        pass
    return ""

def _mentions_budget_or_price(text: str) -> bool:
    """Check if the current user text explicitly mentions budget or price.
    
    Returns True only if the text contains explicit budget/price keywords or numeric price indicators.
    This ensures we only apply budget filters when the user explicitly mentions budget/price.
    """
    if not text or not isinstance(text, str):
        return False
    
    text_lower = text.lower().strip()
    
    # Budget/price keywords
    budget_keywords = [
        "budget", "price", "priced", "pricing", "cost", "costs", "costing",
        "rupee", "rupees", "rs", "₹", "inr", "under", "below", "above", "over",
        "cheap", "cheaper", "affordable", "expensive", "premium", "value",
        "range", "ranges", "maximum", "minimum", "max", "min", "upto", "up to"
    ]
    
    # Check for budget keywords
    if any(keyword in text_lower for keyword in budget_keywords):
        return True
    
    # Check for numeric patterns that suggest price (e.g., "100", "50-200", "under 100")
    # Look for patterns like: number, number-number, number rupees, etc.
    price_patterns = [
        r'\d+\s*(rupee|rs|₹|inr)',
        r'\d+\s*-\s*\d+',  # e.g., "50-200"
        r'(under|below|above|over|upto|up to)\s*\d+',
        r'\d+\s*(or less|or more|and below|and above)',
    ]
    
    for pattern in price_patterns:
        if re.search(pattern, text_lower):
            return True
    
    return False
def _extract_defaults_from_context(ctx) -> Dict[str, Any]:
    """Extract search parameters from user context

    Critical: Always use CURRENT user text for follow-ups so we rebuild ES params
    from the latest delta rather than reusing stale query text from a prior turn.
    
    IMPORTANT: Budget is only extracted from session if the user explicitly mentions
    budget/price in the current query. This prevents old budget values from persisting
    when the user doesn't mention budget in their current query.
    """
    session = ctx.session or {}
    assessment = session.get("assessment", {})
    
    # Canonical base query comes from assessment or prior meaningful query
    base_query = (assessment or {}).get("original_query") or session.get("last_query", "")
    query = base_query or ""
    
    # Get current user text to check if budget is mentioned
    current_text = _get_current_user_text(ctx)
    
    # Extract budget ONLY if user explicitly mentions budget/price in current query
    # This prevents old budget values from persisting when user doesn't mention budget
    budget = session.get("budget", {})
    price_min = None
    price_max = None
    
    # Only extract budget from session if current query mentions budget/price
    if _mentions_budget_or_price(current_text) or _mentions_budget_or_price(query):
        if isinstance(budget, dict):
            price_min = budget.get("min")
            price_max = budget.get("max")
        elif isinstance(budget, str):
            # Try to parse budget string like "100-200" or "under 100"
            budget_lower = budget.lower()
            if "under" in budget_lower or "below" in budget_lower:
                try:
                    price_max = float(re.search(r'\d+', budget).group())
                except:
                    pass
            elif "-" in budget:
                try:
                    parts = budget.split("-")
                    price_min = float(parts[0].strip())
                    price_max = float(parts[1].strip())
                except:
                    pass
            else:
                # Map gen-z style labels to INR ranges
                label = budget_lower.strip()
                if label in {"budget-friendly", "budget friendly", "budget", "cheap", "affordable"}:
                    price_min = None
                    price_max = 100.0
                elif label in {"smart value", "value", "mid", "mid-range", "mid range"}:
                    price_min = 100.0
                    price_max = 200.0
                elif label in {"premium", "expensive", "high-end", "high end"}:
                    price_min = 200.0
                    price_max = None
    else:
        # User didn't mention budget/price, so don't apply budget filters
        # This ensures old budget values don't persist for new queries
        try:
            print(f"DEBUG: BUDGET_NOT_MENTIONED | current_text='{current_text[:50]}' | query='{query[:50]}' | skipping_budget_extraction")
        except Exception:
            pass
    
    # Determine product_intent from context
    product_intent = str(session.get("product_intent") or "show_me_options")
    # Size hint: 1 for is_this_good; else 10
    size_hint = 1 if product_intent == "is_this_good" else 10

    # Persist latest base query back into session for visibility/debug
    try:
        session["last_query"] = query
        ctx.session = session
    except Exception:
        pass

    # Seed params (no cross-assessment carry-over; treat prior slots as hints only via LLM)
    params = {
        "q": query,
        "size": size_hint,
        # Do NOT default category_group; let taxonomy/planner infer it
        "category_group": session.get("category_group") or None,
        # Do NOT carry prior brands/dietary directly; planner/normaliser will decide using current turn
        "brands": None,
        "dietary_terms": None,
        "price_min": price_min,
        "price_max": price_max,
        "protein_weight": 1.5,
        "product_intent": product_intent,
    }

    # Lift quality if preferences indicate health focus
    try:
        pref = str(session.get("preferences", "") or "").lower()
        if any(token in pref for token in ["healthy", "healthier", "cleaner", "low oil", "low sugar", "low sodium", "baked"]):
            prev = float(params.get("min_flean_percentile", 30) or 30)
            params["min_flean_percentile"] = max(prev, 50)
    except Exception:
        pass

    return params

def _normalize_params(base_params: Dict[str, Any], llm_params: Dict[str, Any]) -> Dict[str, Any]:
    """Merge and normalize parameters"""
    # Start with base params
    final_params = dict(base_params)
    
    # Overlay LLM-extracted params (allow LLM to override 'q' when provided)
    for key, value in (llm_params or {}).items():
        if value is not None:
            final_params[key] = value
    
    # Normalize lists
    for list_field in ["brands", "dietary_terms", "dietary_labels"]:
        if list_field in final_params and final_params[list_field]:
            value = final_params[list_field]
            if isinstance(value, str):
                # Split string into list
                if list_field in ["dietary_terms", "dietary_labels"]:
                    final_params[list_field] = [v.strip().upper() for v in value.replace(",", " ").split() if v.strip()]
                else:
                    final_params[list_field] = [v.strip() for v in value.replace(",", " ").split() if v.strip()]
            elif isinstance(value, list):
                # Clean existing list
                if list_field in ["dietary_terms", "dietary_labels"]:
                    final_params[list_field] = [str(v).strip().upper() for v in value if str(v).strip()]
                else:
                    final_params[list_field] = [str(v).strip() for v in value if str(v).strip()]
    
    # Only ensure category_group for F&B when text or taxonomy signals it; else leave None to let ES planner decide
    if not final_params.get("category_group"):
        try:
            q_low = str(final_params.get("q") or "").lower()
            if any(tok in q_low for tok in ["chips", "snack", "ketchup", "juice", "milk", "biscuit", "cookie", "chocolate", "bread"]):
                final_params["category_group"] = "f_and_b"
        except Exception:
            pass
    
    # Clean up None values
    return {k: v for k, v in final_params.items() if v is not None}

async def build_search_params(ctx) -> Dict[str, Any]:
    """Build final search parameters - unified LLM source of truth with minimal fallback"""
    
    # Branch for personal care/skin domain to use skin-specific planner
    try:
        domain = str((ctx.session or {}).get("domain") or "").strip()
        
        # ✅ SAFETY CHECK: Validate domain matches current query intent
        if domain == "personal_care":
            current_text = _get_current_user_text(ctx)
            
            # Heuristic: check if current query has food signals
            food_signals = [
                "chips", "snack", "ketchup", "juice", "milk", "biscuit", 
                "cookie", "chocolate", "bread", "preservative", "organic",
                "sugar", "salt", "sodium", "ingredient", "flavor", "sauce",
                "pickle", "jam", "butter", "cheese", "pasta", "noodle"
            ]
            
            query_lower = current_text.lower()
            is_likely_food = any(signal in query_lower for signal in food_signals)
            
            # If query seems food-related, reset stale personal_care domain
            if is_likely_food:
                
                log.info(
                    f"DOMAIN_MISMATCH_DETECTED | user={ctx.user_id} | "
                    f"stored_domain={domain} | query='{current_text}' | "
                    f"action=resetting_domain"
                )
                # Clear stale domain
                ctx.session.pop("domain", None)
                ctx.session.pop("domain_subcategory", None)
                domain = ""  # Force fallback to unified flow
            else:
                # Domain validation passed, proceed with personal care flow
                from ..llm_service import LLMService
                llm_service = LLMService()
                # Build unified params for personal care
                try:
                    unified_pc = await llm_service.generate_unified_es_params(ctx)
                except Exception:
                    unified_pc = {}
                final_params: Dict[str, Any] = dict(unified_pc or {})
                # Force personal care group and ignore any category paths
                final_params["category_group"] = "personal_care"
                final_params.pop("category_path", None)
                final_params.pop("category_paths", None)
                # Ensure product_intent present
                try:
                    final_params.setdefault("product_intent", str(ctx.session.get("product_intent") or "show_me_options"))
                except Exception:
                    final_params.setdefault("product_intent", "show_me_options")
                # Clamp size to [1,50]
                try:
                    s = int(final_params.get("size", 10) or 10)
                    final_params["size"] = max(1, min(50, s))
                except Exception:
                    final_params["size"] = 10
                try:
                    merged = []
                    for lst in [final_params.get('skin_concerns') or [], final_params.get('hair_concerns') or []]:
                        for it in lst:
                            if it and it not in merged:
                                merged.append(it)
                    print(f"DEBUG: USING_SKIN_PARAMS | q='{final_params.get('q')}' | types={final_params.get('product_types')} | skin_concerns={final_params.get('skin_concerns')} | hair_concerns={final_params.get('hair_concerns')} | merged_concerns={merged}")
                    ctx.session.setdefault("debug", {})["last_skin_search_params"] = final_params
                except Exception:
                    pass
                return final_params
    except Exception as exc:
        try:
            print(f"DEBUG: SKIN_BRANCH_FAILED | {exc}")
        except Exception:
            pass

    # 1) Try unified ES params directly (authoritative)
    try:
        from ..llm_service import LLMService  # type: ignore
        llm_service = LLMService()
        unified = await llm_service.generate_unified_es_params(ctx)
        if isinstance(unified, dict) and unified.get("q"):
            final_params: Dict[str, Any] = dict(unified)
            # Ensure product_intent present
            try:
                final_params.setdefault("product_intent", str(ctx.session.get("product_intent") or "show_me_options"))
            except Exception:
                final_params.setdefault("product_intent", "show_me_options")
            # Default protein weight for scoring
            final_params.setdefault("protein_weight", 1.5)
            # Clamp size to [1,50]
            try:
                s = int(final_params.get("size", 20) or 20)
                final_params["size"] = max(1, min(50, s))
            except Exception:
                final_params["size"] = 20
            # Persist for debugging
            try:
                print(f"DEBUG: USING_UNIFIED_PARAMS_DIRECT | q='{final_params.get('q')}' | dietary={final_params.get('dietary_terms')}")
                ctx.session.setdefault("debug", {})["last_search_params"] = final_params
            except Exception:
                pass
            return final_params
    except Exception as exc:
        try:
            print(f"DEBUG: UNIFIED_CALL_FAILED | {exc}")
        except Exception:
            pass
    
    # 2) Minimal safe fallback (no legacy merges/overwrites)
    try:
        current_text = _get_current_user_text(ctx)
        base = (_extract_defaults_from_context(ctx) or {})
        anchor_q = str(base.get("q") or "").strip()
        q = (current_text or anchor_q or "").strip()
        if not q:
            q = "snacks"  # ultimate minimal default
        fallback: Dict[str, Any] = {
            "q": q,
            "size": 20,
            "category_group": "f_and_b",
            "product_intent": str(ctx.session.get("product_intent") or "show_me_options"),
            "protein_weight": 1.5,
        }
        print(f"DEBUG: UNIFIED_MINIMAL_FALLBACK | q='{fallback['q']}'")
        ctx.session.setdefault("debug", {})["last_search_params"] = fallback
        return fallback
    except Exception:
        # Extreme fallback
        return {"q": "snacks", "size": 20, "category_group": "f_and_b", "product_intent": "show_me_options", "protein_weight": 1.5}


def _chat_params_to_v2_gw_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Map chat/LLM search params onto search_v2.extension.search.search() kwargs."""
    gw: Dict[str, Any] = {
        "q": params.get("q", "") or "",
        "size": params.get("size", 20),
    }
    if params.get("sort_by"):
        gw["sort_by"] = params["sort_by"]
    if params.get("category_group"):
        gw["category_group"] = params["category_group"]
    if params.get("category_paths"):
        gw["category_paths"] = params["category_paths"]
    if params.get("category_path"):
        gw.setdefault("category_path_prefix", params["category_path"])
    if params.get("price_min") is not None:
        gw["price_min"] = params["price_min"]
    if params.get("price_max") is not None:
        gw["price_max"] = params["price_max"]
    if params.get("dietary_terms"):
        gw["dietary_terms"] = params["dietary_terms"]
    if params.get("dietary_labels"):
        gw["dietary_terms"] = params.get("dietary_terms") or params["dietary_labels"]
    if params.get("avoid_ingredients"):
        gw["excluded_ingredients"] = params["avoid_ingredients"]
    if params.get("brands"):
        gw["brands"] = params["brands"]
    if params.get("min_flean_percentile") is not None:
        gw["min_flean_percentile"] = params["min_flean_percentile"]
    if params.get("food_type"):
        gw["food_type"] = params["food_type"]
    if params.get("page"):
        size = int(gw.get("size") or 20)
        gw["offset"] = int(params["page"]) * size
    return gw


async def search_products_handler(ctx) -> Dict[str, Any]:
    """Main product search handler — Search V2 hybrid retrieval only."""
    try:
        latest_text = _get_current_user_text(ctx)
        if isinstance(latest_text, str) and latest_text.strip():
            pass
    except Exception:
        pass

    params = await build_search_params(ctx)
    loop = asyncio.get_running_loop()
    from search_v2.extension.search import search as v2_search

    gw_params = _chat_params_to_v2_gw_params(params)
    gw_result = await loop.run_in_executor(None, lambda: v2_search(gw_params))
    products = gw_result.get("products", []) or []
    gw_meta = gw_result.get("meta", {}) or {}
    results = {
        "products": products,
        "meta": {
            "total_hits": gw_meta.get("total_hits", len(products)),
            "returned": len(products),
            "took_ms": gw_meta.get("took_ms", 0),
            "query_successful": True,
            "engine": "v2",
            "page": params.get("page", 0),
            "size": gw_params.get("size", 20),
        },
    }
    if results.get("products"):
        numeric_fleans = [
            p.get("flean_percentile")
            for p in results["products"]
            if isinstance(p.get("flean_percentile"), (int, float))
        ]
        if numeric_fleans:
            avg_flean = sum(numeric_fleans) / len(numeric_fleans)
            if avg_flean < 30 and params.get("brands"):
                results["meta"]["quality_warning"] = f"average_flean_percentile_{avg_flean:.1f}"
    return results


async def fetch_user_profile_handler(ctx) -> Dict[str, Any]:
    return {
        "user_id": ctx.user_id,
        "preferences": ctx.permanent.get("preferences", {}),
        "dietary_restrictions": ctx.permanent.get("dietary_restrictions", []),
        "favorite_brands": ctx.permanent.get("favorite_brands", []),
    }


async def fetch_purchase_history_handler(ctx) -> Dict[str, Any]:
    return {
        "recent_purchases": [],
        "favorite_categories": ["f_and_b"],
        "average_order_value": 0,
        "last_purchase_date": None,
    }


async def fetch_order_status_handler(ctx) -> Dict[str, Any]:
    return {
        "orders": [],
        "status": "No recent orders found",
    }


register_fetcher(BackendFunction.SEARCH_PRODUCTS, search_products_handler)
register_fetcher(BackendFunction.FETCH_USER_PROFILE, fetch_user_profile_handler)
register_fetcher(BackendFunction.FETCH_PURCHASE_HISTORY, fetch_purchase_history_handler)
register_fetcher(BackendFunction.FETCH_ORDER_STATUS, fetch_order_status_handler)
