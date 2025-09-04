# shopping_bot/data_fetchers/es_products.py
"""
Elasticsearch Products Fetcher
──────────────────────────────
Enhanced with:
• Better brand handling using match queries
• Function score for percentile-based ranking
• Result quality checks with fallback strategies
• Minimum score thresholds
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

import requests

from ..enums import BackendFunction
from . import register_fetcher
from ..scoring_config import build_function_score_functions

# ES Configuration
ELASTIC_BASE = (
    os.getenv("ES_URL")
    or os.getenv("ELASTIC_BASE",
        "https://adb98ad92e064025a9b2893e0589a3b5.asia-south1.gcp.elastic-cloud.com:443"
    )
)
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "flean-v3")
ELASTIC_API_KEY = os.getenv("ES_API_KEY") or os.getenv("ELASTIC_API_KEY", "")
TIMEOUT = int(os.getenv("ELASTIC_TIMEOUT_SECONDS", "10"))

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

def _build_enhanced_es_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build ES query with improved brand handling and percentile-based ranking.
    Uses function_score for quality-based ranking.
    """
    p = params or {}

    # Determine size based on product intent
    desired_size = 1 if str(p.get("product_intent", "")).strip().lower() == "is_this_good" else 10

    # Base document structure with enhanced source fields
    body: Dict[str, Any] = {
        "size": desired_size,
        "track_total_hits": True,
        "_source": {
            "includes": [
                "id", "name", "brand", "price", "mrp", "hero_image.*",
                "package_claims.*", "category_group", "category_paths", 
                "description", "use", "flean_score.*",
                "stats.adjusted_score_percentiles.*",
                "stats.wholefood_percentiles.*",
                "stats.protein_percentiles.*",
                "stats.fiber_percentiles.*",
                "stats.fortification_percentiles.*",
                "stats.simplicity_percentiles.*",
                "stats.sugar_penalty_percentiles.*",
                "stats.sodium_penalty_percentiles.*",
                "stats.trans_fat_penalty_percentiles.*",
                "stats.saturated_fat_penalty_percentiles.*",
                "stats.oil_penalty_percentiles.*",
                "stats.sweetener_penalty_percentiles.*",
                "stats.calories_penalty_percentiles.*",
                "stats.empty_food_penalty_percentiles.*",
            ]
        },
        "query": {"bool": {"filter": [], "should": [], "minimum_should_match": 0}},
        "sort": [{"_score": "desc"}],
        "min_score": 0.5,  # Add minimum score threshold
    }

    bq = body["query"]["bool"]
    filters: List[Dict[str, Any]] = bq["filter"]
    shoulds: List[Dict[str, Any]] = bq["should"]

    # 1) Hard filters
    # Category group filter
    category_group = p.get("category_group")
    if isinstance(category_group, str) and category_group.strip():
        filters.append({"term": {"category_group": category_group.strip()}})

    # Category path filter with improved handling
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
                # More robust category matching
                filters.append({
                    "bool": {
                        "should": [
                            {"term": {"category_paths": full}},
                            {"wildcard": {"category_paths": {"value": f"*{full}*"}}}
                        ],
                        "minimum_should_match": 1
                    }
                })
            elif group == "personal_care":
                full = f"personal_care/{core_path}"
                filters.append({
                    "bool": {
                        "should": [
                            {"term": {"category_paths": full}},
                            {"wildcard": {"category_paths": {"value": f"*{full}*"}}}
                        ],
                        "minimum_should_match": 1
                    }
                })
            else:
                # Fallback: try both
                filters.append({
                    "bool": {
                        "should": [
                            {"wildcard": {"category_paths": {"value": f"*f_and_b/food/{core_path}*"}}},
                            {"wildcard": {"category_paths": {"value": f"*personal_care/{core_path}*"}}}
                        ],
                        "minimum_should_match": 1
                    }
                })

    # Price range filter
    price_min = p.get("price_min")
    price_max = p.get("price_max")
    if price_min is not None or price_max is not None:
        pr: Dict[str, Any] = {}
        if isinstance(price_min, (int, float)):
            pr["gte"] = float(price_min)
        if isinstance(price_max, (int, float)):
            pr["lte"] = float(price_max)
        if pr:
            filters.append({"range": {"price": pr}})

    # Minimum flean percentile (quality threshold)
    min_flean = p.get("min_flean_percentile")
    if isinstance(min_flean, (int, float)):
        try:
            filters.append({
                "range": {
                    "stats.adjusted_score_percentiles.subcategory_percentile": {"gte": float(min_flean)}
                }
            })
        except Exception:
            pass

    # 2) Soft re-ranking signals with boosts
    
    # NOTE: Brand-based boosting is intentionally disabled.
    # Direct brand filtering/boosting proved brittle due to inconsistent brand tokenization
    # (e.g., variants, localization, punctuation). It can eliminate good matches or overfit
    # to noisy brand mentions in text. If we need brand handling later, prefer exact keyword
    # fields (e.g., brand.keyword) and a carefully normalized brand map.

    # Dietary labels with boost
    dietary_labels = p.get("dietary_labels") or p.get("dietary_terms") or []
    if isinstance(dietary_labels, list) and dietary_labels:
        for label in dietary_labels:
            if label and str(label).strip():
                shoulds.append({
            "match": {
                "package_claims.dietary_labels": {
                            "query": str(label).strip(),
                            "boost": 3.0
                        }
                    }
                })

    # Health claims with boost
    health_claims = p.get("health_claims") or []
    if isinstance(health_claims, list) and health_claims:
        for claim in health_claims:
            if claim and str(claim).strip():
                shoulds.append({
            "match": {
                        "package_claims.health_claims": {
                            "query": str(claim).strip(),
                            "boost": 2.0
                        }
                    }
                })

    # 3) Keyword/multi_match component
    q_text = str(p.get("q", "")).strip()
    keywords = p.get("keywords") or []
    
    # Main query matching
    if isinstance(keywords, list) and keywords:
        for kw in keywords[:5]:
            kw_str = str(kw).strip()
            if kw_str:
                shoulds.append({
                    "multi_match": {
                        "query": kw_str,
                        "type": "best_fields",
                        "fields": ["name^4", "description^2", "use", "combined_text"],
                        "fuzziness": "AUTO"
                    }
                })
    elif q_text:
        shoulds.append({
            "multi_match": {
                "query": q_text,
                "type": "best_fields",
                "fields": ["name^4", "description^2", "use", "combined_text"],
                "fuzziness": "AUTO"
            }
        })

    # 4) Apply dynamic function_score based on subcategory
    # Derive subcategory from params/category_path
    subcategory = None
    try:
        if isinstance(p.get("fb_subcategory"), str) and p["fb_subcategory"].strip():
            subcategory = p["fb_subcategory"].strip()
        elif isinstance(category_path, str) and category_path.strip():
            # Use the last segment as subcategory when present
            path_parts = [seg for seg in category_path.split("/") if seg]
            if path_parts:
                # Expect f_and_b/food/l2/l3; pick l3 else l2
                subcategory = path_parts[-1]
                if subcategory in ("food", "f_and_b", "personal_care") and len(path_parts) >= 2:
                    subcategory = path_parts[-2]
        if not subcategory:
            subcategory = "_default"
    except Exception:
        subcategory = "_default"

    scoring_functions: List[Dict[str, Any]] = []
    if shoulds or filters:
        # Get category-specific scoring functions
        scoring_functions = build_function_score_functions(subcategory, include_flean=True)
        body["query"] = {
            "function_score": {
                "query": {"bool": bq},
                "functions": scoring_functions,
                "score_mode": "sum",
                "boost_mode": "multiply"
            }
        }

    # minimum_should_match stays 0 per spec
    bq["minimum_should_match"] = 0

    # Debug logs
    print(f"DEBUG: ES filters={len(filters)} filters")
    print(f"DEBUG: ES should={len(shoulds)} clauses")
    print(f"DEBUG: Using dynamic scoring for subcategory='{subcategory}'")
    print(f"DEBUG: Applied {len(scoring_functions)} scoring functions")
    
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
        
        # Extract percentile scores
        stats = src.get("stats", {})
        flean_percentile = None
        if stats.get("adjusted_score_percentiles"):
            flean_percentile = stats["adjusted_score_percentiles"].get("subcategory_percentile")
        # Prepare bonus/penalty percentile bundle for LLM persuasion
        bonus_percentiles = {
            "protein": (stats.get("protein_percentiles", {}) or {}).get("subcategory_percentile"),
            "fiber": (stats.get("fiber_percentiles", {}) or {}).get("subcategory_percentile"),
            "wholefood": (stats.get("wholefood_percentiles", {}) or {}).get("subcategory_percentile"),
            "fortification": (stats.get("fortification_percentiles", {}) or {}).get("subcategory_percentile"),
            "simplicity": (stats.get("simplicity_percentiles", {}) or {}).get("subcategory_percentile"),
        }
        penalty_percentiles = {
            "sugar": (stats.get("sugar_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "sodium": (stats.get("sodium_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "trans_fat": (stats.get("trans_fat_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "saturated_fat": (stats.get("saturated_fat_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "oil": (stats.get("oil_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "sweetener": (stats.get("sweetener_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "calories": (stats.get("calories_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "empty_food": (stats.get("empty_food_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
        }
        
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
            
            # Quality scores
            "flean_percentile": flean_percentile,
            "flean_score": src.get("flean_score", {}).get("adjusted_score"),
            "bonus_percentiles": {k: v for k, v in bonus_percentiles.items() if v is not None},
            "penalty_percentiles": {k: v for k, v in penalty_percentiles.items() if v is not None},
            
            # Image
            "image": _get_best_image(src.get("hero_image", {})),
            
            # Ingredients
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
    """Elasticsearch fetcher with enhanced query capabilities."""
    
    def __init__(self, base_url: str = None, index: str = None, api_key: str = None):
        self.base_url = base_url or ELASTIC_BASE
        self.index = index or ELASTIC_INDEX
        self.api_key = api_key or ELASTIC_API_KEY
        
        if not self.api_key:
            raise RuntimeError("ELASTIC_API_KEY (or ES_API_KEY) is required for Elasticsearch access")
            
        self.endpoint = f"{self.base_url}/{self.index}/_search"
        self.mget_endpoint = f"{self.base_url}/{self.index}/_mget"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"ApiKey {self.api_key}"
        } if self.api_key else {}
    
    def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute search against Elasticsearch with fallback strategies."""
        try:
            # Use the enhanced query builder
            query_body = _build_enhanced_es_query(params)
            
            # Debug logging
            print(f"DEBUG: Enhanced ES Query Structure:")
            print(f"  - Query: {params.get('q', '')}")
            print(f"  - Category: {params.get('category_group', 'all')}")
            print(f"  - Brands: {params.get('brands', [])}")
            print(f"  - Price range: {params.get('price_min', 'no min')}-{params.get('price_max', 'no max')}")
            print(f"  - Dietary: {params.get('dietary_labels', [])}")
            
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query_body,
                timeout=TIMEOUT
            )
            response.raise_for_status()
            
            raw_data = response.json()
            result = _transform_results(raw_data)
            
            print(f"DEBUG: ES query found {result['meta']['total_hits']} products")
            
            # No brand-specific fallback; brand handling is disabled (see note above)
            
            # Show top results for debugging
            if result['products']:
                print("DEBUG: Top ES results:")
                for i, product in enumerate(result['products'][:3], 1):
                    score_info = f"(score: {product['score']}"
                    if product.get('flean_percentile'):
                        score_info += f", flean: {product['flean_percentile']}%"
                    score_info += ")"
                    print(f"  {i}. {product['name']} - ₹{product['price']} {score_info}")
            
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

    def mget_products(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch full product documents via _mget for the given IDs.

        Returns a list of _source dicts (with selected fields prioritized) in the same order as requested IDs
        when possible.
        """
        if not ids:
            return []
        try:
            print(f"DEBUG: ES mget request | endpoint={self.mget_endpoint} | id_count={len(ids)} | sample_ids={ids[:3]}")
            body = {
                "ids": [str(x).strip() for x in ids if str(x).strip()]
            }
            response = requests.post(
                self.mget_endpoint,
                headers=self.headers,
                json=body,
                timeout=TIMEOUT
            )
            try:
                print(f"DEBUG: ES mget response | status={response.status_code}")
            except Exception:
                pass
            response.raise_for_status()
            data = response.json() or {}
            docs = data.get("docs", []) or []
            print(f"DEBUG: ES mget parsed | docs_count={len(docs)}")
            out: List[Dict[str, Any]] = []
            for d in docs:
                src = d.get("_source", {}) or {}
                if src:
                    out.append(src)
            print(f"DEBUG: ES mget out | sources_count={len(out)}")
            # If _mget by _id returned nothing, fallback to a terms search on field 'id'
            if not out:
                print("DEBUG: ES mget fallback → terms search on field 'id'")
                return self.search_by_ids(ids)
            return out
        except requests.exceptions.Timeout:
            print("DEBUG: ES mget timeout")
            return []
        except Exception as exc:
            print(f"DEBUG: ES mget failed: {exc}")
            return []

    def search_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch documents by matching the 'id' field using a terms query.

        Returns list of _source dicts ordered to match the requested ids.
        """
        if not ids:
            return []
        try:
            ordered_ids = [str(x).strip() for x in ids if str(x).strip()]
            body = {
                "size": len(ordered_ids),
                "_source": {
                    "includes": [
                        "id", "name", "brand", "price", "mrp", "description", "use",
                        "hero_image.*", "package_claims.*", "category_group", "category_paths",
                        "category_data.*", "ingredients.*", "tags_and_sentiments.*",
                        "flean_score.*", "stats.*"
                    ]
                },
                "query": {
                    "terms": {"id": ordered_ids}
                }
            }
            search_endpoint = f"{self.base_url}/{self.index}/_search"
            print(f"DEBUG: ES ids-search request | endpoint={search_endpoint} | id_count={len(ordered_ids)}")
            response = requests.post(
                search_endpoint,
                headers=self.headers,
                json=body,
                timeout=TIMEOUT
            )
            print(f"DEBUG: ES ids-search response | status={response.status_code}")
            response.raise_for_status()
            data = response.json() or {}
            hits = (data.get("hits", {}) or {}).get("hits", []) or []
            print(f"DEBUG: ES ids-search parsed | hits_count={len(hits)}")
            id_to_src: Dict[str, Dict[str, Any]] = {}
            for h in hits:
                src = h.get("_source", {}) or {}
                pid = str(src.get("id", "")).strip()
                if pid:
                    id_to_src[pid] = src
            # Reorder to match requested ids
            out: List[Dict[str, Any]] = [id_to_src.get(i) for i in ordered_ids if id_to_src.get(i)]
            print(f"DEBUG: ES ids-search out | sources_count={len(out)}")
            return out
        except requests.exceptions.Timeout:
            print("DEBUG: ES ids-search timeout")
            return []
        except Exception as exc:
            print(f"DEBUG: ES ids-search failed: {exc}")
            return []

# Parameter extraction and normalization
def _extract_defaults_from_context(ctx) -> Dict[str, Any]:
    """Extract search parameters from user context

    Critical: Always use CURRENT user text for follow-ups so we rebuild ES params
    from the latest delta rather than reusing stale query text from a prior turn.
    """
    session = ctx.session or {}
    assessment = session.get("assessment", {})
    
    # Prioritize the current user utterance
    query = _get_current_user_text(ctx) or ""
    if not query:
        # Fallbacks for safety
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
    
    # Determine product_intent from context
    product_intent = str(session.get("product_intent") or "show_me_options")
    # Size hint: 1 for is_this_good; else 10
    size_hint = 1 if product_intent == "is_this_good" else 10

    # Persist latest user query back into session for visibility/debug
    try:
        session["last_query"] = query
        ctx.session = session
    except Exception:
        pass

    return {
        "q": query,
        "size": size_hint,
        "category_group": session.get("category_group", "f_and_b"),  # default to f_and_b
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
    
    # Ensure category_group is set
    if not final_params.get("category_group"):
        final_params["category_group"] = "f_and_b"
    
    # Clean up None values
    return {k: v for k, v in final_params.items() if v is not None}

async def build_search_params(ctx) -> Dict[str, Any]:
    """Build final search parameters from context + LLM analysis"""
    
    # Resolve current user text once for downstream guards
    current_text = _get_current_user_text(ctx)
    try:
        print(f"DEBUG: BSP_START | current_text='{current_text}'")
    except Exception:
        pass
    # Get base parameters from context
    base_params = _extract_defaults_from_context(ctx)
    try:
        print(f"DEBUG: BSP_BASE | base_params={base_params}")
    except Exception:
        pass
    
    # Use LLM to extract/normalize additional parameters (always)
    llm_params = {}
    try:
        # Lazy import to avoid circular import with llm_service importing this module
        from ..llm_service import LLMService  # type: ignore
        llm_service = LLMService()
        # Inject current user text explicitly so LLM sees the latest delta
        try:
            ctx.session.setdefault("debug", {})["current_user_text"] = current_text
        except Exception:
            pass
        llm_params = await llm_service.extract_es_params(ctx)
        print(f"DEBUG: LLM extracted params = {llm_params}")
    except Exception as e:
        print(f"DEBUG: LLM param extraction failed: {e}")
        llm_params = {}
    
    # Merge and normalize
    final_params = _normalize_params(base_params, llm_params)
    try:
        print(f"DEBUG: BSP_MERGED | merged_params={final_params}")
    except Exception:
        pass
    
    # Heuristic upgrades based on current text
    text_lower = (current_text or "").lower()
    if any(token in text_lower for token in ["healthier", "healthy", "cleaner", "better for me", "low sugar", "less oil", "low sodium"]):
        # Tighten quality threshold if user is asking for healthier options
        try:
            prev = float(final_params.get("min_flean_percentile", 30))
        except Exception:
            prev = 30.0
        final_params["min_flean_percentile"] = max(prev, 50)

    # Apply minimum quality threshold if not searching for specific brand.
    # Don't clobber a higher threshold set above; enforce at least 30.
    if not final_params.get("brands"):
        try:
            prev = float(final_params.get("min_flean_percentile", 0))
        except Exception:
            prev = 0.0
        final_params["min_flean_percentile"] = max(prev, 30)
    
    # Deterministic keyword injection from CURRENT user text so refinements like
    # 'baked options' always influence ES ranking, even if LLM omits them.
    try:
        import re as _re
        stop = {
            "pls", "please", "options", "option", "want", "show", "some", "more",
            "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "me"
        }
        tokens = [_t for _t in (_re.findall(r"[A-Za-z]+", (current_text or "").lower()) or []) if _t and _t not in stop]
        before_kw = list(final_params.get("keywords") or [])
        if tokens:
            existing = []
            try:
                existing = [str(x).strip().lower() for x in (final_params.get("keywords") or []) if str(x).strip()]
            except Exception:
                existing = []
            merged = []
            seen = set()
            for x in existing + tokens:
                if x and x not in seen:
                    seen.add(x)
                    merged.append(x)
            final_params["keywords"] = merged[:8]
            print(f"DEBUG: BSP_INJECT | tokens={tokens} | before={before_kw} | after={final_params['keywords']}")
        else:
            print("DEBUG: BSP_INJECT | no_tokens_from_current_text")
    except Exception as _inj_exc:
        print(f"DEBUG: BSP_INJECT_ERROR | {str(_inj_exc)}")
    
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
    """Main product search handler with quality checks"""
    # Ensure follow-ups always use the latest user text by refreshing session state
    try:
        latest_text = _get_current_user_text(ctx)
        if isinstance(latest_text, str) and latest_text.strip():
            ctx.session = ctx.session or {}
            ctx.session["last_query"] = latest_text.strip()
    except Exception:
        pass

    params = await build_search_params(ctx)
    fetcher = get_es_fetcher()
    
    # Run in thread to avoid blocking
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, lambda: fetcher.search(params))
    
    # Additional quality check: if we got results but they're all low quality
    if results.get('products'):
        avg_flean = sum(p.get('flean_percentile', 50) for p in results['products']) / len(results['products'])
        if avg_flean < 30 and params.get('brands'):
            # Products are low quality, maybe try without brand constraint
            print(f"DEBUG: Average flean percentile {avg_flean}% is low, considering fallback...")
            results['meta']['quality_warning'] = f'average_flean_percentile_{avg_flean:.1f}'
    
    return results

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