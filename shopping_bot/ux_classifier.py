# shopping_bot/ux_classifier.py
"""
UX Intent Classification Service
────────────────────────────────
Classifies user queries into the 4 core UX intent patterns and
determines appropriate PSL templates and Quick Replies.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

import anthropic

from .config import get_config
from .models import UserContext
from .enums import UXIntentType, PSLType
from .bot_helpers import pick_tool

Cfg = get_config()
log = logging.getLogger(__name__)


@dataclass
class UXClassificationResult:
    """Result of UX intent classification"""
    ux_intent: UXIntentType
    confidence: float
    reasoning: str
    recommended_psl: PSLType
    context_factors: List[str]


# UX Classification Tool
UX_CLASSIFICATION_TOOL = {
    "name": "classify_ux_intent",
    "description": "Classify user query into one of 4 UX intent patterns",
    "input_schema": {
        "type": "object",
        "properties": {
            "ux_intent": {
                "type": "string",
                "enum": ["is_this_good", "which_is_better", "show_alternates", "show_options"],
                "description": "The primary UX intent pattern"
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in classification (0-1)"
            },
            "reasoning": {
                "type": "string",
                "description": "Why this classification was chosen"
            },
            "recommended_psl": {
                "type": "string",
                "enum": ["spm", "mpm"],
                "description": "Recommended Product Surface Layer template"
            },
            "context_factors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key context factors that influenced the decision"
            }
        },
        "required": ["ux_intent", "confidence", "reasoning", "recommended_psl", "context_factors"]
    }
}


class UXClassifierService:
    """Service for classifying user queries into UX intent patterns"""
    
    def __init__(self):
        self.anthropic = anthropic.AsyncAnthropic(api_key=Cfg.ANTHROPIC_API_KEY)
    
    async def classify_ux_intent(
        self, 
        query: str, 
        ctx: UserContext,
        product_count: int = 0
    ) -> UXClassificationResult:
        """
        Classify user query into UX intent pattern.
        
        Args:
            query: User's current query
            ctx: User context with history
            product_count: Number of products in current context
        """
        
        # Build context for classification
        classification_context = self._build_classification_context(ctx, product_count)
        
        prompt = self._build_classification_prompt(query, classification_context)
        
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[UX_CLASSIFICATION_TOOL],
                tool_choice={"type": "tool", "name": "classify_ux_intent"},
                temperature=0.2,
                max_tokens=300,
            )
            
            tool_use = pick_tool(resp, "classify_ux_intent")
            if not tool_use:
                return self._get_fallback_classification(query, product_count)
            
            result_data = tool_use.input or {}
            
            return UXClassificationResult(
                ux_intent=UXIntentType(result_data.get("ux_intent", "show_options")),
                confidence=float(result_data.get("confidence", 0.5)),
                reasoning=result_data.get("reasoning", "Default classification"),
                recommended_psl=PSLType(result_data.get("recommended_psl", "product_card_carousel")),
                context_factors=result_data.get("context_factors", [])
            )
            
        except Exception as exc:
            log.warning(f"UX classification failed: {exc}")
            return self._get_fallback_classification(query, product_count)
    
    def _build_classification_context(self, ctx: UserContext, product_count: int) -> Dict[str, Any]:
        """Build context for UX classification"""
        session = ctx.session or {}
        
        # Get recent conversation history
        history = session.get("history", [])
        recent_queries = []
        for h in history[-3:]:
            if isinstance(h, dict) and h.get("user_query"):
                recent_queries.append(h["user_query"])
        
        # Get last recommendation info
        last_recommendation = session.get("last_recommendation", {})
        last_products = last_recommendation.get("products", [])
        
        return {
            "product_count": product_count,
            "recent_queries": recent_queries,
            "last_products_count": len(last_products),
            "has_budget_constraint": bool(session.get("budget")),
            "has_dietary_requirements": bool(session.get("dietary_requirements")),
            "has_brand_preference": bool(session.get("brands")),
            "conversation_length": len(history)
        }
    
    def _build_classification_prompt(self, query: str, context: Dict[str, Any]) -> str:
        """Build the classification prompt"""
        return f"""
You are a UX intent classifier for an e-commerce shopping assistant.

CURRENT USER QUERY: "{query}"

CONTEXT:
- Products currently available: {context['product_count']}
- Recent queries: {context['recent_queries']}
- Last recommendation had: {context['last_products_count']} products
- Has budget constraint: {context['has_budget_constraint']}
- Has dietary requirements: {context['has_dietary_requirements']}
- Has brand preference: {context['has_brand_preference']}
- Conversation turns: {context['conversation_length']}

CLASSIFICATION RULES:

1. **IS_THIS_GOOD** - Single product validation/confirmation
   - Keywords: "good", "right", "okay", "suitable", "fine"
   - Context: User asking about specific product
   - PSL: SPM (single product focus)

2. **WHICH_IS_BETTER** - Direct comparison between options
   - Keywords: "better", "compare", "vs", "difference", "choose between"
   - Context: User comparing 2-3 specific items
   - PSL: PRODUCT_CARD_CAROUSEL (side-by-side view)

3. **SHOW_ALTERNATES** - Alternative to current selection
   - Keywords: "alternate", "different", "other", "instead", "substitute"
   - Context: User wants alternatives to current choice
   - PSL: PRODUCT_CARD_CAROUSEL (similar alternatives)

4. **SHOW_OPTIONS** - Broader category exploration
   - Keywords: "options", "what else", "show me", "available", "more"
   - Context: User exploring category broadly
   - PSL: MPM for collections

Consider context factors like:
- Conversation history (follow-up vs new request)
- Product availability (affects PSL choice)
- User constraints (budget, dietary, brand)

Return ONLY the classification tool call.
"""
    
    def _get_fallback_classification(self, query: str, product_count: int) -> UXClassificationResult:
        """Fallback classification based on simple heuristics"""
        query_lower = query.lower()
        
        # Simple keyword-based fallback
        if any(word in query_lower for word in ["good", "right", "okay", "suitable"]):
            return UXClassificationResult(
                ux_intent=UXIntentType.IS_THIS_GOOD,
                confidence=0.6,
                reasoning="Keyword-based fallback: validation question detected",
                recommended_psl=PSLType.SPM,
                context_factors=["fallback_classification", "keyword_match"]
            )
        
        if any(word in query_lower for word in ["better", "compare", "vs", "difference"]):
            return UXClassificationResult(
                ux_intent=UXIntentType.WHICH_IS_BETTER,
                confidence=0.6,
                reasoning="Keyword-based fallback: comparison detected",
                recommended_psl=PSLType.MPM,
                context_factors=["fallback_classification", "comparison_keywords"]
            )
        
        if any(word in query_lower for word in ["alternate", "different", "other", "instead"]):
            return UXClassificationResult(
                ux_intent=UXIntentType.SHOW_ALTERNATES,
                confidence=0.6,
                reasoning="Keyword-based fallback: alternatives requested",
                recommended_psl=PSLType.MPM,
                context_factors=["fallback_classification", "alternative_keywords"]
            )
        
        # Default to show_options
        return UXClassificationResult(
            ux_intent=UXIntentType.SHOW_OPTIONS,
            confidence=0.5,
            reasoning="Default fallback: show options pattern",
            recommended_psl=PSLType.MPM,
            context_factors=["fallback_classification", "default_pattern"]
        )


# Global service instance
_ux_classifier: Optional[UXClassifierService] = None


def get_ux_classifier() -> UXClassifierService:
    """Get the global UX classifier service instance"""
    global _ux_classifier
    if _ux_classifier is None:
        _ux_classifier = UXClassifierService()
    return _ux_classifier