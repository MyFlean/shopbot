# shopping_bot/enhanced_llm_service.py
"""
Enhanced LLM Service with UX Response Generation
────────────────────────────────────────────────
Extends the existing LLM service to support the new UX intent patterns
while maintaining backward compatibility with existing functionality.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

from .llm_service import LLMService  # Import existing service
from .models import UserContext, UXResponse, EnhancedBotResponse
from .enums import UXIntentType, EnhancedResponseType, QueryIntent
from .ux_classifier import get_ux_classifier, UXClassificationResult
from .ux_response_generator import get_ux_response_generator
from .utils.helpers import safe_get

log = logging.getLogger(__name__)


class EnhancedLLMService(LLMService):
    """
    Enhanced LLM Service that adds UX-driven response generation
    while maintaining all existing functionality.
    """
    
    def __init__(self):
        super().__init__()
        self.ux_classifier = get_ux_classifier()
        self.ux_response_generator = get_ux_response_generator()
        
        # UX-enabled intent mappings
        self.ux_enabled_intents = {
            "Product_Discovery", "Recommendation", 
            "Specific_Product_Search", "Product_Comparison"
        }
    
    async def generate_enhanced_response(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
        intent_l3: str,
        query_intent: QueryIntent
    ) -> EnhancedBotResponse:
        """
        Generate enhanced response with UX components when appropriate.
        Falls back to standard response for non-UX intents.
        """
        
        # Check if this intent should use UX patterns
        should_use_ux = self._should_use_ux_patterns(intent_l3, fetched)
        
        if should_use_ux:
            return await self._generate_ux_enhanced_response(
                query, ctx, fetched, intent_l3, query_intent
            )
        else:
            # Fall back to standard response generation
            standard_response = await self.generate_response(
                query, ctx, fetched, intent_l3, query_intent
            )
            return self._wrap_as_enhanced_response(standard_response)
    
    def _should_use_ux_patterns(self, intent_l3: str, fetched: Dict[str, Any]) -> bool:
        """
        Determine if we should use UX patterns for this intent and data.
        """
        # Must be a UX-enabled intent
        if intent_l3 not in self.ux_enabled_intents:
            return False
        
        # Must have product data
        has_products = self._has_product_results(fetched)
        if not has_products:
            return False
        
        # Must have reasonable number of products (1-50)
        product_count = self._get_product_count(fetched)
        if product_count < 1 or product_count > 50:
            return False
        
        return True
    
    async def _generate_ux_enhanced_response(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
        intent_l3: str,
        query_intent: QueryIntent
    ) -> EnhancedBotResponse:
        """Generate UX-enhanced response with DPL, PSL, and QRs"""
        
        try:
            # Extract products data
            products_data = self._extract_products_data(fetched)
            
            # Classify UX intent
            classification = await self.ux_classifier.classify_ux_intent(
                query, ctx, len(products_data)
            )
            
            log.info(f"UX Classification: {classification.ux_intent.value} (confidence: {classification.confidence})")
            
            # Generate UX response
            ux_response = await self.ux_response_generator.generate_ux_response(
                classification, query, ctx, products_data
            )
            
            # Create enhanced response content
            content = self._create_ux_content(ux_response, classification)
            
            # Determine enhanced response type
            response_type = self._map_to_enhanced_response_type(classification.recommended_psl)
            
            return EnhancedBotResponse(
                response_type=response_type,
                content=content,
                functions_executed=list(fetched.keys()),
                ux_response=ux_response
            )
            
        except Exception as exc:
            log.error(f"UX-enhanced response generation failed: {exc}")
            
            # Fallback to standard response
            standard_response = await self.generate_response(
                query, ctx, fetched, intent_l3, query_intent
            )
            return self._wrap_as_enhanced_response(standard_response)
    
    def _extract_products_data(self, fetched: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract products data from fetched results"""
        if 'search_products' in fetched:
            search_data = fetched['search_products']
            if isinstance(search_data, dict):
                data = search_data.get('data', search_data)
                return data.get('products', [])
        return []
    
    def _get_product_count(self, fetched: Dict[str, Any]) -> int:
        """Get number of products in fetched data"""
        products = self._extract_products_data(fetched)
        return len(products)
    
    def _create_ux_content(self, ux_response: UXResponse, classification: UXClassificationResult) -> Dict[str, Any]:
        """Create content structure for UX response"""
        
        return {
            "ux_intent": ux_response.ux_intent.value,
            "dpl": ux_response.dpl.to_dict(),
            "psl": ux_response.psl.to_dict(), 
            "quick_replies": [qr.to_dict() for qr in ux_response.quick_replies],
            "confidence_score": ux_response.confidence_score,
            
            # Backward compatibility fields
            "message": ux_response.dpl.message,  # For legacy clients
            "summary_message": ux_response.dpl.message,
            "products": [p.to_dict() for p in ux_response.psl.products]
        }
    
    def _map_to_enhanced_response_type(self, psl_type) -> EnhancedResponseType:
        """Map PSL type to enhanced response type"""
        mapping = {
            "spm": EnhancedResponseType.UX_SPM,
            "mpm": EnhancedResponseType.UX_MPM
        }
        return mapping.get(psl_type.value if hasattr(psl_type, 'value') else str(psl_type), 
                          EnhancedResponseType.UX_MPM)
    
    def _wrap_as_enhanced_response(self, standard_response: Dict[str, Any]) -> EnhancedBotResponse:
        """Wrap standard response as enhanced response"""
        
        # Map response type
        response_type_mapping = {
            "final_answer": EnhancedResponseType.CASUAL,
            "error": EnhancedResponseType.ERROR,
            "question": EnhancedResponseType.QUESTION,
            "processing": EnhancedResponseType.PROCESSING_STUB
        }
        
        response_type_str = standard_response.get("response_type", "final_answer")
        enhanced_type = response_type_mapping.get(response_type_str, EnhancedResponseType.CASUAL)
        
        return EnhancedBotResponse(
            response_type=enhanced_type,
            content=standard_response,
            functions_executed=[]  # Will be set by caller
        )
    
    # Override parent method to provide backward compatibility
    async def generate_response(
        self,
        query: str,
        ctx: UserContext,
        fetched: Dict[str, Any],
        intent_l3: str,
        query_intent: QueryIntent
    ) -> Dict[str, Any]:
        """
        Override parent method to optionally use UX patterns.
        Maintains backward compatibility by returning standard dict format.
        """
        
        # Check if caller specifically wants enhanced response
        use_enhanced = getattr(ctx, '_use_enhanced_response', False)
        
        if use_enhanced:
            enhanced_response = await self.generate_enhanced_response(
                query, ctx, fetched, intent_l3, query_intent
            )
            
            # Convert enhanced response back to standard dict format
            return enhanced_response.content
        else:
            # Use original parent implementation
            return await super().generate_response(query, ctx, fetched, intent_l3, query_intent)


class UXResponseDecisionEngine:
    """
    Decision engine for determining when to use UX patterns vs standard responses.
    This can be configured and tuned independently.
    """
    
    def __init__(self):
        self.ux_intent_threshold = 0.7  # Minimum confidence for UX classification
        self.product_count_range = (1, 50)  # Valid product count range
        self.enabled_intents = {
            "Product_Discovery", "Recommendation", 
            "Specific_Product_Search", "Product_Comparison"
        }
    
    def should_use_ux_pattern(
        self, 
        intent_l3: str, 
        classification: UXClassificationResult,
        product_count: int,
        context: Dict[str, Any]
    ) -> bool:
        """
        Comprehensive decision logic for UX pattern usage.
        Can be made more sophisticated with A/B testing, user preferences, etc.
        """
        
        # Intent must be UX-enabled
        if intent_l3 not in self.enabled_intents:
            return False
        
        # Must have sufficient confidence in UX classification
        if classification.confidence < self.ux_intent_threshold:
            return False
        
        # Product count must be reasonable
        if not (self.product_count_range[0] <= product_count <= self.product_count_range[1]):
            return False
        
        # Additional context-based decisions can be added here
        # - User preferences
        # - A/B testing groups  
        # - Time-based rollouts
        # - Error rate monitoring
        
        return True
    
    def get_fallback_strategy(self, reason: str) -> str:
        """
        Determine fallback strategy when UX patterns can't be used.
        """
        fallback_strategies = {
            "low_confidence": "standard_with_products",
            "wrong_intent": "standard_simple",
            "no_products": "standard_simple", 
            "too_many_products": "standard_with_pagination",
            "error": "standard_simple"
        }
        
        return fallback_strategies.get(reason, "standard_simple")


# Singleton instances
_enhanced_llm_service: Optional[EnhancedLLMService] = None
_decision_engine: Optional[UXResponseDecisionEngine] = None


def get_enhanced_llm_service() -> EnhancedLLMService:
    """Get the global enhanced LLM service instance"""
    global _enhanced_llm_service
    if _enhanced_llm_service is None:
        _enhanced_llm_service = EnhancedLLMService()
    return _enhanced_llm_service


def get_ux_decision_engine() -> UXResponseDecisionEngine:
    """Get the global UX decision engine instance"""
    global _decision_engine
    if _decision_engine is None:
        _decision_engine = UXResponseDecisionEngine()
    return _decision_engine