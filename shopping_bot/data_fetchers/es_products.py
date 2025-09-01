# shopping_bot/data_fetchers/es_products.py
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

import requests

from ..enums import BackendFunction
from ..llm_service import LLMService
from . import register_fetcher

# ES Configuration
# Prefer ES_URL/ES_API_KEY if provided; fallback to legacy ELASTIC_BASE/ELASTIC_API_KEY
ELASTIC_BASE = (
    os.getenv("ES_URL")
    or os.getenv("ELASTIC_BASE",
        "https://adb98ad92e064025a9b2893e0589a3b5.asia-south1.gcp.elastic-cloud.com:443"
    )
)
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "flean-v3")
ELASTIC_API_KEY = os.getenv("ES_API_KEY") or os.getenv("ELASTIC_API_KEY", "")
TIMEOUT = int(os.getenv("ELASTIC_TIMEOUT_SECONDS", "10"))

# Enhanced query template with better field coverage
BASE_BODY: Dict[str, Any] = {
    "track_total_hits": True,
    "size": 20,
    "_source": {
        "includes": [
            "id", "name", "brand", "price", "mrp", "category_paths",
            "ingredients.raw_text", "ingredients.raw_text_new",
            "category_data.nutritional.nutri_breakdown.*",
            "category_data.nutritional.raw_text",
            "package_claims.*", "tags_and_sentiments.tags.*", 
            "hero_image.*", "description", "use"
        ]
    },
    "query": {
        "function_score": {
            "query": {
                "bool": {
                    "filter": [],
                    "must": [],
                    "should": [],
                    "minimum_should_match": 0
                }
            },
            "functions": [],
            "score_mode": "sum",
            "boost_mode": "sum"
        }
    },
    "sort": [
        {"_score": "desc"},
        {"category_data.nutritional.nutri_breakdown.protein_g": {"order": "desc"}}
    ],
    "highlight": {
        "pre_tags": ["<em class=\"hl\">"],
        "post_tags": ["</em>"],
        "fields": {
            "name": {},
            "ingredients.raw_text": {},
            "ingredients.raw_text_new": {},
            "category_data.nutritional.raw_text": {}
        }
    },
    "aggs": {
        "by_brand": {"terms": {"field": "brand", "size": 10}},
        "dietary_labels": {"terms": {"field": "package_claims.dietary_labels", "size": 10}},
        "protein_ranges": {
            "range": {
                "field": "category_data.nutritional.nutri_breakdown.protein_g",
                "ranges": [
                    {"to": 5}, 
                    {"from": 5, "to": 10}, 
                    {"from": 10, "to": 20}, 
                    {"from": 20}
                ]
            }
        }
    }
}

# Text cleaning
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

def _clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    s = TAG_RE.sub("", s)
    s = WS_RE.sub(" ", s).strip()
    return s

def _extract_protein(src: Dict[str, Any]) -> Optional[float]:
    try:
        v = (
            src.get("category_data", {})
            .get("nutritional", {}) 
            .get("nutri_breakdown", {})
            .get("protein_g")
        )
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None

def _get_best_image(hero: Dict[str, Any]) -> Optional[str]:
    if not isinstance(hero, dict):
        return None
    # Try standard resolutions first
    for size in ["640", "750", "828", "1080", "256", "384"]:
        if hero.get(size):
            return hero[size]
    # Fallback to any available image
    for v in hero.values():
        if isinstance(v, str) and v.strip():
            return v
    return None

def _extract_highlight(hit: Dict[str, Any]) -> Optional[str]:
    hl = hit.get("highlight", {})
    for field in ["name", "package_claims.dietary_labels", "ingredients.raw_text"]:
        if field in hl and hl[field]:
            return _clean_text(hl[field][0])
    return None

def _extract_product_terms(query_text: str) -> str:
    """Extract meaningful product terms from natural language query"""
    # Words to remove that don't help product matching
    stop_words = {
        'recommend', 'suggest', 'show', 'find', 'get', 'buy', 'purchase', 'want', 'need',
        'me', 'some', 'any', 'good', 'nice', 'best', 'great', 'options', 'products', 'items',
        'under', 'above', 'below', 'within', 'around', 'about', 'less', 'more', 'than',
        'rupees', 'rs', 'inr', 'price', 'cost', 'budget', 'cheap', 'expensive',
        'can', 'you', 'please', 'help', 'looking', 'for'
    }
    
    # Extract words, remove numbers and stop words
    words = re.findall(r'\b[a-zA-Z]+\b', query_text.lower())
    product_words = [w for w in words if w not in stop_words and len(w) > 2]
    
    return ' '.join(product_words)

def _build_must_query(product_terms: str) -> List[Dict[str, Any]]:
    """Build the main search query with enhanced field coverage"""
    if not product_terms.strip():
        return [{"match_all": {}}]
    
    return [{
        "multi_match": {
            "query": product_terms,
            "type": "most_fields",
            "operator": "or",
            "fuzziness": "AUTO",
            "lenient": True,
            "fields": [
                "name^6",
                "ingredients.raw_text^8",
                "ingredients.raw_text_new^8",
                "category_data.nutritional.raw_text^3",
                "description^2",
                "use",
                "package_claims.health_claims^2",
                "package_claims.dietary_labels^2",
                "tags_and_sentiments.seo_keywords.*^2",
                "tags_and_sentiments.tags.*^1.5"
            ]
        }
    }]

def _build_should_boosts(product_terms: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build should clauses for better relevance based on query content"""
    should_clauses = []
    terms_lower = product_terms.lower()
    
    # Boost for exact phrase matches in name
    if len(product_terms.split()) > 1:
        should_clauses.append({
            "match_phrase": {
                "name": {
                    "query": product_terms,
                    "boost": 10
                }
            }
        })
    
    # Category-specific boosts (examples)
    if any(term in terms_lower for term in ['bread', 'roti', 'pav', 'bun']):
        should_clauses.extend([
            {"wildcard": {"category_paths": {"value": "*bread*", "boost": 5.0}}},
            {"match": {"name": {"query": "whole wheat", "boost": 3}}},
            {"match": {"package_claims.dietary_labels": {"query": "whole grain", "boost": 2}}}
        ])
    
    if any(term in terms_lower for term in ['chips', 'snacks', 'namkeen']):
        should_clauses.extend([
            {"wildcard": {"category_paths": {"value": "*snacks*", "boost": 5.0}}},
            {"match": {"package_claims.health_claims": {"query": "baked not fried", "boost": 2}}}
        ])
    
    if any(term in terms_lower for term in ['protein', 'fitness', 'workout']):
        should_clauses.extend([
            {"match": {"package_claims.health_claims": {"query": "high protein", "boost": 5}}},
            {"match": {"package_claims.health_claims": {"query": "source of protein", "boost": 3}}},
            {"range": {"category_data.nutritional.nutri_breakdown.protein_g": {"gte": 10, "boost": 3}}}
        ])
    
    # Dietary preference boosts
    dietary_terms = params.get("dietary_terms", [])
    for term in dietary_terms:
        should_clauses.append({
            "match": {
                "package_claims.dietary_labels": {
                    "query": term,
                    "boost": 8
                }
            }
        })
    
    # Brand preference boosts
    brands = params.get("brands", [])
    for brand in brands:
        should_clauses.append({
            "match": {
                "brand": {
                    "query": brand,
                    "boost": 6
                }
            }
        })
    
    return should_clauses

def _build_function_score_functions(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build function score functions for better ranking"""
    functions = []
    
    # Protein content boost
    protein_weight = params.get("protein_weight", 1.5)
    if protein_weight > 0:
        functions.append({
            "field_value_factor": {
                "field": "category_data.nutritional.nutri_breakdown.protein_g",
                "modifier": "ln1p",
                "missing": 0
            },
            "weight": float(protein_weight)
        })
    
    # Health claims boost
    functions.append({
        "filter": {
            "exists": {"field": "package_claims.health_claims"}
        },
        "weight": 1.2
    })
    
    # Popular brands boost
    popular_brands = ["Nestle", "Britannia", "Parle", "ITC", "Amul"]
    functions.append({
        "filter": {
            "terms": {"brand": popular_brands}
        },
        "weight": 1.1
    })
    
    return functions

def _build_enhanced_es_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """Build ES query using explicit hard filters and soft re-ranking per latest spec."""
    p = params or {}

    # Base doc with fields per spec
    body: Dict[str, Any] = {
        "size": max(1, min(50, int(p.get("size", 20)))),
        "track_total_hits": True,
        "_source": {
            "includes": [
                "id", "name", "brand", "price", "mrp", "hero_image.*",
                "package_claims.*", "category_group", "category_paths", "description", "use"
            ]
        },
        "query": {"bool": {"filter": [], "should": [], "minimum_should_match": 0}},
        "sort": [{"_score": "desc"}],
    }

    bq = body["query"]["bool"]
    filters: List[Dict[str, Any]] = bq["filter"]
    shoulds: List[Dict[str, Any]] = bq["should"]

    # 1) Hard filters
    # category_group
    category_group = p.get("category_group")
    if isinstance(category_group, str) and category_group.strip():
        filters.append({"term": {"category_group": category_group.strip()}})

    # category_paths via explicit l2/l3 or full path wildcard respecting ES layout
    category_path = p.get("category_path") or p.get("cat_path")
    if isinstance(category_path, str) and category_path.strip():
        raw_path = category_path.strip()
        parts = [x for x in raw_path.split("/") if x]
        # Strip leading group and optional 'food' to get core l2/l3
        core_parts = parts[:]
        if core_parts and core_parts[0] in ("f_and_b", "personal_care"):
            core_parts = core_parts[1:]
        if core_parts and core_parts[0] == "food":
            core_parts = core_parts[1:]
        core_path = "/".join(core_parts)
        if core_path:
            group = (category_group or "").strip()
            if group == "f_and_b":
                full = f"f_and_b/food/{core_path}"
                filters.append({"wildcard": {"category_paths": {"value": f"*{full}*"}}})
            elif group == "personal_care":
                full = f"personal_care/{core_path}"
                filters.append({"wildcard": {"category_paths": {"value": f"*{full}*"}}})
            else:
                # Fallback: try both
                filters.append({"wildcard": {"category_paths": {"value": f"*f_and_b/food/{core_path}*"}}})
                filters.append({"wildcard": {"category_paths": {"value": f"*personal_care/{core_path}*"}}})

    # brands
    brands = p.get("brands") or []
    if isinstance(brands, list) and brands:
        filters.append({"terms": {"brand": brands}})

    # price range
    price_min = p.get("price_min")
    price_max = p.get("price_max")
    if price_min is not None or price_max is not None:
        pr: Dict[str, float] = {}
        if isinstance(price_min, (int, float)):
            pr["gte"] = float(price_min)
        if isinstance(price_max, (int, float)):
            pr["lte"] = float(price_max)
        if pr:
            filters.append({"range": {"price": pr}})

    # 2) Soft re-ranking signals
    dietary_labels = p.get("dietary_labels") or p.get("dietary_terms") or []
    if isinstance(dietary_labels, list) and dietary_labels:
        shoulds.append({"terms": {"package_claims.dietary_labels": dietary_labels}})

    health_claims = p.get("health_claims") or []
    if isinstance(health_claims, list) and health_claims:
        shoulds.append({"terms": {"package_claims.health_claims": health_claims}})

    # 3) Keyword/multi_match component
    q_text = str(p.get("q", "")).strip()
    keywords = p.get("keywords") or []
    # Prefer explicit keywords; else fall back to extracted product terms from q
    if isinstance(keywords, list) and keywords:
        for kw in keywords[:5]:
            kw_str = str(kw).strip()
            if kw_str:
                shoulds.append({
                    "multi_match": {
                        "query": kw_str,
                        "type": "most_fields",
                        "fields": ["name^4", "description^2", "use", "combined_text"],
                    }
                })
    elif q_text:
        shoulds.append({
            "multi_match": {
                "query": q_text,
                "type": "most_fields",
                "fields": ["name^4", "description^2", "use", "combined_text"],
            }
        })

    # minimum_should_match stays 0 per spec
    bq["minimum_should_match"] = 0

    # Debug logs
    print(f"DEBUG: ES filters={filters}")
    print(f"DEBUG: ES should_count={len(shoulds)}")

    return body

def _transform_results(raw_response: Dict[str, Any]) -> Dict[str, Any]:
    """Transform ES response with enhanced field coverage"""
    hits = raw_response.get("hits", {}).get("hits", [])
    total = raw_response.get("hits", {}).get("total", {}).get("value", len(hits))
    took = raw_response.get("took", 0)
    
    products = []
    for rank, hit in enumerate(hits, 1):
        src = hit.get("_source", {})
        score = hit.get("_score", 0)
        
        # Extract nutritional info
        nutrition = src.get("category_data", {}).get("nutritional", {}).get("nutri_breakdown", {})
        
        # Extract package claims
        package_claims = src.get("package_claims", {})
        health_claims = package_claims.get("health_claims", [])
        dietary_labels = package_claims.get("dietary_labels", [])
        
        product = {
            "rank": rank,
            "score": round(score, 3) if isinstance(score, (int, float)) else score,
            "id": src.get("id", f"prod_{rank}"),
            "name": _clean_text(src.get("name", "")),
            "brand": src.get("brand", ""),
            "price": src.get("price"),
            "mrp": src.get("mrp"),
            "category": src.get("category_group", ""),
            "category_paths": src.get("category_paths", []),
            "description": _clean_text(src.get("description", "")),
            
            # Nutritional information
            "protein_g": nutrition.get("protein_g"),
            "carbs_g": nutrition.get("carbs_g"),
            "fat_g": nutrition.get("fat_g"),
            "calories": nutrition.get("energy_kcal"),
            
            # Claims and labels
            "health_claims": health_claims if isinstance(health_claims, list) else [],
            "dietary_labels": dietary_labels if isinstance(dietary_labels, list) else [],
            
            # Image
            "image": _get_best_image(src.get("hero_image", {})),
            
            # Ingredients (useful for detailed info)
            "ingredients": _clean_text(src.get("ingredients", {}).get("raw_text", "")),
        }
        
        # Add highlight if available
        highlight = _extract_highlight(hit)
        if highlight:
            product["highlight"] = highlight
            
        products.append(product)
    
    return {
        "meta": {
            "total_hits": total,
            "returned": len(products),
            "took_ms": took,
            "query_successful": True
        },
        "products": products
    }

class ElasticsearchProductsFetcher:
    """Elasticsearch fetcher (real ES only, no mock data)."""
    
    def __init__(self, base_url: str = None, index: str = None, api_key: str = None):
        self.base_url = base_url or ELASTIC_BASE
        self.index = index or ELASTIC_INDEX
        self.api_key = api_key or ELASTIC_API_KEY
        
        if not self.api_key:
            raise RuntimeError("ELASTIC_API_KEY (or ES_API_KEY) is required for Elasticsearch access")
            
        self.endpoint = f"{self.base_url}/{self.index}/_search"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"ApiKey {self.api_key}"
        } if self.api_key else {}
    
    def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute search against Elasticsearch. No mock fallbacks."""
        try:
            # Use the enhanced query builder
            query_body = _build_enhanced_es_query(params)
            
            # Debug logging
            print(f"DEBUG: Enhanced ES Query Structure:")
            print(f"  - Original query: {params.get('q', '')}")
            print(f"  - Category: {params.get('category_group', 'all')}")
            print(f"  - Price max: {params.get('price_max', 'no limit')}")
            print(f"  - Dietary labels: {params.get('dietary_labels', 'none')}")
            
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query_body,
                timeout=TIMEOUT
            )
            response.raise_for_status()
            
            raw_data = response.json()
            result = _transform_results(raw_data)
            
            print(f"DEBUG: Enhanced ES query found {result['meta']['total_hits']} products")
            
            # Show top results for debugging
            if result['products']:
                print("DEBUG: Top ES results:")
                for i, product in enumerate(result['products'][:3], 1):
                    print(f"  {i}. {product['name']} - ₹{product['price']} (score: {product['score']})")
            
            return result
            
        except requests.exceptions.Timeout:
            print(f"DEBUG: ES timeout")
            return {"meta": {"total_hits": 0, "returned": 0, "took_ms": 0, "query_successful": False, "error": "timeout"}, "products": []}
        except requests.exceptions.RequestException as e:
            print(f"DEBUG: ES request failed: {e}")
            return {"meta": {"total_hits": 0, "returned": 0, "took_ms": 0, "query_successful": False, "error": str(e)}, "products": []}
        except Exception as e:
            print(f"DEBUG: Unexpected ES error: {e}")
            return {"meta": {"total_hits": 0, "returned": 0, "took_ms": 0, "query_successful": False, "error": str(e)}, "products": []}

# Parameter extraction and normalization
def _extract_defaults_from_context(ctx) -> Dict[str, Any]:
    """Extract search parameters from user context"""
    session = ctx.session or {}
    assessment = session.get("assessment", {})
    
    # Get the original query
    query = assessment.get("original_query") or session.get("last_query", "")
    
    # Extract budget
    budget = session.get("budget", {})
    price_min = None
    price_max = None
    
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
    
    # Determine product_intent from context (default to show_me_options)
    product_intent = str(session.get("product_intent") or "show_me_options")
    # Size hint: 1 for is_this_good; else 4
    size_hint = 1 if product_intent == "is_this_good" else 4

    return {
        "q": query,
        "size": size_hint,
        "category_group": session.get("category_group", "personal_care"),  # default if unknown
        "brands": session.get("brands"),
        "dietary_terms": session.get("dietary_requirements"),
        "price_min": price_min,
        "price_max": price_max,
        "protein_weight": 1.5,
        "product_intent": product_intent,
    }

def _normalize_params(base_params: Dict[str, Any], llm_params: Dict[str, Any]) -> Dict[str, Any]:
    """Merge and normalize parameters"""
    # Start with base params
    final_params = dict(base_params)
    
    # Overlay LLM-extracted params
    for key, value in (llm_params or {}).items():
        if value is not None:
            final_params[key] = value
    
    # Normalize lists
    for list_field in ["brands", "dietary_terms"]:
        if list_field in final_params and final_params[list_field]:
            value = final_params[list_field]
            if isinstance(value, str):
                # Split string into list
                final_params[list_field] = [v.strip().upper() for v in value.replace(",", " ").split() if v.strip()]
            elif isinstance(value, list):
                # Clean existing list
                final_params[list_field] = [str(v).strip().upper() for v in value if str(v).strip()]
    
    # Ensure category_group is set
    if not final_params.get("category_group"):
        final_params["category_group"] = "f_and_b"
    
    # Clean up None values
    return {k: v for k, v in final_params.items() if v is not None}

async def build_search_params(ctx) -> Dict[str, Any]:
    """Build final search parameters from context + LLM analysis"""
    
    # Get base parameters from context
    base_params = _extract_defaults_from_context(ctx)
    
    # Use LLM to extract/normalize additional parameters (always)
    llm_params = {}
    try:
        llm_service = LLMService()
        llm_params = await llm_service.extract_es_params(ctx)
        print(f"DEBUG: LLM extracted params = {llm_params}")
    except Exception as e:
        print(f"DEBUG: LLM param extraction failed: {e}")
        llm_params = {}
    
    # Merge and normalize
    final_params = _normalize_params(base_params, llm_params)
    
    print(f"DEBUG: Final search params = {final_params}")
    
    # Store for debugging
    try:
        ctx.session.setdefault("debug", {})["last_search_params"] = final_params
    except:
        pass
    
    return final_params

# Global fetcher instance
_es_fetcher: Optional[ElasticsearchProductsFetcher] = None

def get_es_fetcher() -> ElasticsearchProductsFetcher:
    """Get singleton ES fetcher instance"""
    global _es_fetcher
    if _es_fetcher is None:
        _es_fetcher = ElasticsearchProductsFetcher()
    return _es_fetcher

# Async handlers for different functions
async def search_products_handler(ctx) -> Dict[str, Any]:
    """Main product search handler (real ES only)"""
    params = await build_search_params(ctx)
    fetcher = get_es_fetcher()
    
    # Run in thread to avoid blocking
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fetcher.search(params))

async def fetch_user_profile_handler(ctx) -> Dict[str, Any]:
    """User profile - return minimal data for now"""
    return {
        "user_id": ctx.user_id,
        "preferences": ctx.permanent.get("preferences", {}),
        "dietary_restrictions": ctx.permanent.get("dietary_restrictions", []),
        "favorite_brands": ctx.permanent.get("favorite_brands", []),
    }

async def fetch_purchase_history_handler(ctx) -> Dict[str, Any]:
    """Purchase history - return minimal data for now"""
    return {
        "recent_purchases": [],
        "favorite_categories": ["f_and_b"],
        "average_order_value": 0,
        "last_purchase_date": None,
    }

async def fetch_order_status_handler(ctx) -> Dict[str, Any]:
    """Order status - return minimal data for now"""
    return {
        "orders": [],
        "status": "No recent orders found",
    }

# Register all handlers
register_fetcher(BackendFunction.SEARCH_PRODUCTS, search_products_handler)
register_fetcher(BackendFunction.FETCH_USER_PROFILE, fetch_user_profile_handler)  
register_fetcher(BackendFunction.FETCH_PURCHASE_HISTORY, fetch_purchase_history_handler)
register_fetcher(BackendFunction.FETCH_ORDER_STATUS, fetch_order_status_handler)