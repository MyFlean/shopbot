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
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Union
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
        """Convert to dictionary for JSON serialization"""
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
        pass
    
    @abstractmethod
    def validate_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and clean extracted parameters"""
        pass


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
        self.anthropic = anthropic.Anthropic(api_key=Cfg.ANTHROPIC_API_KEY)
        self._valid_categories = [
            "f_and_b", "health_nutrition", "personal_care", 
            "home_kitchen", "electronics"
        ]
    
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
                "original_query": assessment.get("original_query", ""),
                "user_answers": {
                    "budget": session.get("budget"),
                    "dietary_requirements": session.get("dietary_requirements"),
                    "product_category": session.get("product_category"),
                    "brands": session.get("brands"),
                },
                "session_data": {
                    "category_group": session.get("category_group"),
                    "last_query": session.get("last_query"),
                }
            }
            
            # Enhanced prompt for better parameter extraction
            enhanced_prompt = self._build_extraction_prompt(context)
            
            resp = await self._call_anthropic_for_params(enhanced_prompt)
            
            if not resp:
                return RecommendationResponse(
                    response_type=RecommendationResponseType.ERROR,
                    data={},
                    error_message="Failed to get response from LLM"
                )
            
            # Post-process and validate
            final_params = self.validate_params(resp, context)
            
            return RecommendationResponse(
                response_type=RecommendationResponseType.ES_PARAMS,
                data=final_params,
                metadata={
                    "extraction_method": "llm_enhanced",
                    "context_keys": list(context.keys())
                }
            )
            
        except Exception as exc:
            log.warning("Enhanced ES param extraction failed: %s", exc)
            return RecommendationResponse(
                response_type=RecommendationResponseType.ERROR,
                data={},
                error_message=str(exc)
            )
    
    def _build_extraction_prompt(self, context: Dict[str, Any]) -> str:
        """Build the extraction prompt for LLM"""
        return f"""
You are a search parameter extractor for an e-commerce platform. 

USER CONTEXT:
{json.dumps(context, ensure_ascii=False, indent=2)}

TASK: Extract normalized Elasticsearch parameters for product search.

RULES:
1. q: Use the original_query or last_query as the main search text
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
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[ES_PARAM_TOOL],
                tool_choice={"type": "tool", "name": "emit_es_params"},
                temperature=0,
                max_tokens=400,
            )
            
            # Extract tool use (using the same helper function from original code)
            tool_use = self._pick_tool(resp, "emit_es_params")
            if not tool_use:
                return None
            
            raw_params = tool_use.input or {}
            cleaned_params = self._strip_keys(raw_params) if isinstance(raw_params, dict) else {}
            
            return cleaned_params
            
        except Exception as exc:
            log.error(f"Anthropic API call failed: {exc}")
            return None
    
    def validate_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and clean extracted parameters"""
        cleaned = {}
        
        # Query text - fallback chain
        q = params.get("q") or context.get("original_query") or context.get("session_data", {}).get("last_query") or ""
        cleaned["q"] = str(q).strip()
        
        # Size with bounds
        size = params.get("size", 20)
        try:
            size = int(size)
            cleaned["size"] = max(1, min(50, size))
        except:
            cleaned["size"] = 20
        
        # Category group validation
        category = params.get("category_group", "f_and_b")
        cleaned["category_group"] = category if category in self._valid_categories else "f_and_b"
        
        # Price validation
        for price_field in ["price_min", "price_max"]:
            if price_field in params:
                try:
                    price_val = float(params[price_field])
                    if price_val >= 0:  # No negative prices
                        cleaned[price_field] = price_val
                except:
                    pass
        
        # Ensure price_min <= price_max
        if "price_min" in cleaned and "price_max" in cleaned:
            if cleaned["price_min"] > cleaned["price_max"]:
                # Swap them
                cleaned["price_min"], cleaned["price_max"] = cleaned["price_max"], cleaned["price_min"]
        
        # List fields (brands, dietary_terms)
        for list_field in ["brands", "dietary_terms"]:
            if list_field in params:
                items = params[list_field]
                if isinstance(items, str):
                    # Split string
                    items = [item.strip() for item in items.replace(",", " ").split() if item.strip()]
                elif isinstance(items, list):
                    # Clean list
                    items = [str(item).strip() for item in items if str(item).strip()]
                else:
                    items = []
                
                if items:
                    # Special handling for dietary terms (uppercase)
                    if list_field == "dietary_terms":
                        items = [item.upper() for item in items]
                    cleaned[list_field] = items
        
        # Protein weight (optional scoring boost)
        if "protein_weight" in params:
            try:
                pw = float(params["protein_weight"])
                if 0.1 <= pw <= 10.0:  # Reasonable range
                    cleaned["protein_weight"] = pw
            except:
                pass
        
        return cleaned
    
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
        """Extract tool use from Anthropic response - placeholder for actual implementation"""
        # This should use the same logic as the original pick_tool function
        # from bot_helpers module
        for content in resp.content:
            if hasattr(content, 'name') and content.name == tool_name:
                return content
        return None


# ─────────────────────────────────────────────────────────────
# Factory and Service Manager
# ─────────────────────────────────────────────────────────────

class RecommendationEngineFactory:
    """Factory for creating recommendation engines"""
    
    _engines = {
        "elasticsearch": ElasticsearchRecommendationEngine,
        # Future engines can be added here:
        # "ml_based": MLRecommendationEngine,
        # "hybrid": HybridRecommendationEngine,
    }
    
    @classmethod
    def create_engine(self, engine_type: str = "elasticsearch") -> BaseRecommendationEngine:
        """Create a recommendation engine instance"""
        if engine_type not in self._engines:
            log.warning(f"Unknown engine type {engine_type}, defaulting to elasticsearch")
            engine_type = "elasticsearch"
        
        engine_class = self._engines[engine_type]
        return engine_class()
    
    @classmethod
    def register_engine(cls, name: str, engine_class: type):
        """Register a new engine type"""
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

# Global service instance for backward compatibility
_recommendation_service = None

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
# Future Extension Points
# ─────────────────────────────────────────────────────────────

class MLRecommendationEngine(BaseRecommendationEngine):
    """Future ML-based recommendation engine"""
    
    async def extract_search_params(self, ctx: UserContext) -> RecommendationResponse:
        # Placeholder for ML-based parameter extraction
        return RecommendationResponse(
            response_type=RecommendationResponseType.ERROR,
            data={},
            error_message="ML engine not implemented yet"
        )
    
    def validate_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        # Placeholder for ML-based validation
        return params


class HybridRecommendationEngine(BaseRecommendationEngine):
    """Future hybrid recommendation engine combining multiple approaches"""
    
    def __init__(self):
        self.elasticsearch_engine = ElasticsearchRecommendationEngine()
        # self.ml_engine = MLRecommendationEngine()
    
    async def extract_search_params(self, ctx: UserContext) -> RecommendationResponse:
        # Placeholder for hybrid approach
        # Could combine ES and ML results
        return await self.elasticsearch_engine.extract_search_params(ctx)
    
    def validate_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        return self.elasticsearch_engine.validate_params(params, context)