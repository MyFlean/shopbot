# shopping_bot/ux_response_generator.py
"""
UX Response Generation Service
──────────────────────────────
Generates DPL, PSL, and Quick Replies for the 4 UX intent patterns.
Creates cohesive UX responses that follow the three-tap rule.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

import anthropic

from .config import get_config
from .models import UserContext, UXResponse, DPL, PSL, QuickReply, UXProduct
from .enums import UXIntentType, PSLType, EnhancedResponseType
from .ux_classifier import UXClassificationResult
from .bot_helpers import pick_tool

Cfg = get_config()
log = logging.getLogger(__name__)


# DPL Generation Tool
DPL_GENERATION_TOOL = {
    "name": "generate_dpl",
    "description": "Generate Dynamic Persuasion Layer text",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Persuasive, personalized message"
            },
            "context_hint": {
                "type": "string", 
                "description": "Why this message was chosen"
            },
            "personalization_factors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Factors that made it personal"
            }
        },
        "required": ["message"]
    }
}


# Quick Replies Generation Tool
QUICK_REPLIES_TOOL = {
    "name": "generate_quick_replies",
    "description": "Generate context-appropriate quick reply buttons",
    "input_schema": {
        "type": "object",
        "properties": {
            "quick_replies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                        "intent_type": {
                            "type": "string",
                            "enum": ["is_this_good", "which_is_better", "show_alternates", "show_options"]
                        }
                    },
                    "required": ["label", "value"]
                },
                "maxItems": 4,
                "description": "Up to 4 quick reply buttons"
            }
        },
        "required": ["quick_replies"]
    }
}


class UXResponseGenerator:
    """Service for generating complete UX responses with DPL, PSL, and QRs"""
    
    def __init__(self):
        self.anthropic = anthropic.AsyncAnthropic(api_key=Cfg.ANTHROPIC_API_KEY)
    
    async def generate_ux_response(
        self,
        classification: UXClassificationResult,
        query: str,
        ctx: UserContext,
        products_data: List[Dict[str, Any]]
    ) -> UXResponse:
        """
        Generate complete UX response for the classified intent.
        
        Args:
            classification: UX intent classification result
            query: User's query
            ctx: User context
            products_data: Product search results
        """
        
        # Convert products data to UX products
        ux_products = self._convert_to_ux_products(products_data, classification.ux_intent)
        
        # Generate DPL
        dpl = await self._generate_dpl(classification, query, ctx, ux_products)
        
        # Create PSL
        psl = self._create_psl(classification.recommended_psl, ux_products, classification.ux_intent)
        
        # Generate Quick Replies
        quick_replies = await self._generate_quick_replies(classification, ctx, len(ux_products))
        
        return UXResponse(
            ux_intent=classification.ux_intent,
            dpl=dpl,
            psl=psl,
            quick_replies=quick_replies,
            confidence_score=classification.confidence,
            personalization_applied=self._has_personalization(ctx)
        )
    
    async def _generate_dpl(
        self,
        classification: UXClassificationResult,
        query: str,
        ctx: UserContext,
        products: List[UXProduct]
    ) -> DPL:
        """Generate Dynamic Persuasion Layer text"""
        
        # Build context for DPL generation
        dpl_context = self._build_dpl_context(ctx, products, classification.ux_intent)
        
        prompt = self._build_dpl_prompt(query, classification, dpl_context)
        
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[DPL_GENERATION_TOOL],
                tool_choice={"type": "tool", "name": "generate_dpl"},
                temperature=0.7,
                max_tokens=200,
            )
            
            tool_use = pick_tool(resp, "generate_dpl")
            if tool_use and tool_use.input:
                dpl_data = tool_use.input
                return DPL(
                    message=dpl_data.get("message", "Here are your options!"),
                    context_hint=dpl_data.get("context_hint"),
                    personalization_factors=dpl_data.get("personalization_factors", [])
                )
        
        except Exception as exc:
            log.warning(f"DPL generation failed: {exc}")
        
        # Fallback DPL
        return self._get_fallback_dpl(classification.ux_intent, len(products))
    
    def _build_dpl_context(self, ctx: UserContext, products: List[UXProduct], intent: UXIntentType) -> Dict[str, Any]:
        """Build context for DPL generation"""
        session = ctx.session or {}
        
        return {
            "user_budget": session.get("budget"),
            "dietary_requirements": session.get("dietary_requirements"),
            "brand_preferences": session.get("brands"),
            "product_count": len(products),
            "price_range": self._get_price_range(products),
            "has_premium_options": self._has_premium_options(products),
            "conversation_context": self._get_recent_context(session)
        }
    
    def _build_dpl_prompt(self, query: str, classification: UXClassificationResult, context: Dict[str, Any]) -> str:
        """Build DPL generation prompt"""
        
        intent_guidance = {
            UXIntentType.IS_THIS_GOOD: "Validate the choice with confidence-building language. Address specific concerns.",
            UXIntentType.WHICH_IS_BETTER: "Help compare options with clear differentiators. Be decisive.",
            UXIntentType.SHOW_ALTERNATES: "Present alternatives as improvements or variations. Show why to switch.",
            UXIntentType.SHOW_OPTIONS: "Create excitement about variety and choice. Guide exploration."
        }
        
        return f"""
Generate a Dynamic Persuasion Layer message for this shopping context.

USER QUERY: "{query}"
UX INTENT: {classification.ux_intent.value}
REASONING: {classification.reasoning}

CONTEXT:
- Budget: {context.get('user_budget', 'Not specified')}
- Dietary needs: {context.get('dietary_requirements', 'None')}
- Brand preferences: {context.get('brand_preferences', 'None')}
- Products available: {context['product_count']}
- Price range: {context.get('price_range', 'Varied')}
- Has premium options: {context.get('has_premium_options', False)}

GUIDANCE: {intent_guidance.get(classification.ux_intent, "Create engaging, helpful message.")}

RULES:
- Keep message 1-2 sentences, conversational and personal
- Reference user constraints when relevant (budget, dietary)
- Build confidence and urgency without being pushy
- Use "you" language, avoid generic phrases
- Include relevant context (price, features, benefits)

Return ONLY the tool call to generate_dpl.
"""
    
    async def _generate_quick_replies(
        self,
        classification: UXClassificationResult,
        ctx: UserContext,
        product_count: int
    ) -> List[QuickReply]:
        """Generate context-appropriate quick replies"""
        
        # Build context for QR generation
        qr_context = self._build_qr_context(ctx, classification.ux_intent, product_count)
        
        prompt = self._build_qr_prompt(classification, qr_context)
        
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[QUICK_REPLIES_TOOL],
                tool_choice={"type": "tool", "name": "generate_quick_replies"},
                temperature=0.3,
                max_tokens=300,
            )
            
            tool_use = pick_tool(resp, "generate_quick_replies")
            if tool_use and tool_use.input:
                qr_data = tool_use.input.get("quick_replies", [])
                return [
                    QuickReply(
                        label=qr.get("label", "More"),
                        value=qr.get("value", "show more"),
                        intent_type=UXIntentType(qr["intent_type"]) if qr.get("intent_type") else None
                    )
                    for qr in qr_data
                ]
        
        except Exception as exc:
            log.warning(f"Quick replies generation failed: {exc}")
        
        # Fallback QRs
        return self._get_fallback_quick_replies(classification.ux_intent)
    
    def _build_qr_context(self, ctx: UserContext, intent: UXIntentType, product_count: int) -> Dict[str, Any]:
        """Build context for quick replies generation"""
        session = ctx.session or {}
        
        return {
            "has_budget": bool(session.get("budget")),
            "has_dietary_prefs": bool(session.get("dietary_requirements")),
            "has_brand_prefs": bool(session.get("brands")),
            "product_count": product_count,
            "intent": intent.value
        }
    
    def _build_qr_prompt(self, classification: UXClassificationResult, context: Dict[str, Any]) -> str:
        """Build quick replies generation prompt"""
        
        # Intent-specific QR patterns
        qr_patterns = {
            UXIntentType.IS_THIS_GOOD: [
                "Why? (explain choice)",
                "Cleaner swap (better alternative)",
                "Cheaper (lower price)",
                "Add to cart"
            ],
            UXIntentType.WHICH_IS_BETTER: [
                "Explain pick (why this one)",
                "Show alternates (more options)",
                "Add A to cart",
                "Add B to cart"
            ],
            UXIntentType.SHOW_ALTERNATES: [
                "Only cleaner (health focus)",
                "Under ₹X (budget constraint)",
                "Higher protein (nutrition)",
                "Show 10 more"
            ],
            UXIntentType.SHOW_OPTIONS: [
                "Cheaper (budget options)",
                "Spicier (flavor preference)",
                "Higher protein (nutrition)",
                "Show 10 more"
            ]
        }
        
        pattern_suggestions = qr_patterns.get(classification.ux_intent, ["More", "Budget", "Premium", "Different"])
        
        return f"""
Generate quick reply buttons for this UX intent pattern.

UX INTENT: {classification.ux_intent.value}
CONTEXT: {json.dumps(context, ensure_ascii=False)}

PATTERN SUGGESTIONS: {pattern_suggestions}

RULES:
- Generate 3-4 buttons max
- Keep labels short (1-2 words ideal, max 3)
- Make values actionable for the bot to understand
- Consider user constraints (budget, dietary, brands)
- Follow three-tap rule - each button should lead closer to purchase
- Map each button to appropriate intent_type when possible

EXAMPLES:
- "Why?" → value: "explain why this product", intent_type: "is_this_good"
- "Cheaper" → value: "show cheaper alternatives", intent_type: "show_alternates"  
- "Add to cart" → value: "add product to cart"

Return ONLY the tool call to generate_quick_replies.
"""
    
    def _create_psl(self, psl_type: PSLType, products: List[UXProduct], intent: UXIntentType) -> PSL:
        """Create Product Surface Layer with appropriate template"""
        
        if psl_type == PSLType.SPM:
            # Single Product Module - take first product
            products_for_template = products[:1]
            max_visible = 1
            
        else:  # MPM
            # Multi-Product Module - curated collections
            products_for_template = products[:20]  # More products for collections
            max_visible = 10  # Show 10, rest behind "View items"
        
        # Set collection title for MPM
        collection_title = None
        view_more_action = None
        
        if psl_type == PSLType.MPM:
            collection_title = self._get_collection_title(intent, len(products))
            if len(products) > max_visible:
                view_more_action = f"view_more_{intent.value}"
        
        return PSL(
            template_type=psl_type,
            products=products_for_template,
            max_visible=max_visible,
            collection_title=collection_title,
            view_more_action=view_more_action
        )
    
    def _convert_to_ux_products(self, products_data: List[Dict[str, Any]], intent: UXIntentType) -> List[UXProduct]:
        """Convert search results to UX products with persuasion hooks"""
        
        ux_products = []
        for i, product in enumerate(products_data):
            # Extract basic product info
            product_id = str(product.get("id", f"prod_{i}"))
            name = product.get("name", "Product")
            price = f"₹{product.get('price', 'N/A')}"
            
            # Generate persuasion hook based on intent
            persuasion_hook = self._generate_persuasion_hook(product, intent)
            
            # Extract key differentiator
            key_differentiator = self._extract_key_differentiator(product)
            
            ux_products.append(UXProduct(
                id=product_id,
                name=name,
                price=price,
                image_url=product.get("image"),
                brand=product.get("brand"),
                rating=product.get("rating"),
                persuasion_hook=persuasion_hook,
                key_differentiator=key_differentiator,
                cart_action=f"add_to_cart_{product_id}",
                features=self._extract_features(product)
            ))
        
        return ux_products
    
    def _generate_persuasion_hook(self, product: Dict[str, Any], intent: UXIntentType) -> str:
        """Generate persuasive one-liner for the product"""
        
        # Extract key attributes
        price = product.get("price")
        brand = product.get("brand", "")
        health_claims = product.get("health_claims", [])
        protein = product.get("protein_g")
        
        if intent == UXIntentType.IS_THIS_GOOD:
            if health_claims:
                return f"Great choice with {health_claims[0].lower()}"
            elif protein and protein > 10:
                return f"Excellent protein source at {protein}g per serving"
            else:
                return f"Popular {brand} choice" if brand else "Solid everyday option"
        
        elif intent == UXIntentType.WHICH_IS_BETTER:
            if protein:
                return f"{protein}g protein - ideal for fitness goals"
            elif price and price < 100:
                return "Budget-friendly without compromising quality"
            else:
                return "Premium quality meets great value"
        
        elif intent == UXIntentType.SHOW_ALTERNATES:
            if health_claims:
                return f"Cleaner choice: {health_claims[0].lower()}"
            else:
                return "Better alternative for your needs"
        
        else:  # SHOW_OPTIONS
            return "Worth exploring for your requirements"
    
    def _extract_key_differentiator(self, product: Dict[str, Any]) -> str:
        """Extract what makes this product special"""
        
        # Priority order for differentiators
        if product.get("health_claims"):
            return product["health_claims"][0]
        elif product.get("dietary_labels"):
            return product["dietary_labels"][0]
        elif product.get("protein_g") and product["protein_g"] > 15:
            return f"High protein ({product['protein_g']}g)"
        elif product.get("brand"):
            return f"Trusted {product['brand']} brand"
        else:
            return "Quality assured"
    
    def _extract_features(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Extract relevant features for comparison"""
        features = {}
        
        if product.get("protein_g"):
            features["protein"] = f"{product['protein_g']}g"
        if product.get("calories"):
            features["calories"] = product["calories"]
        if product.get("dietary_labels"):
            features["dietary"] = product["dietary_labels"]
        if product.get("health_claims"):
            features["claims"] = product["health_claims"]
        
        return features
    
    def _get_price_range(self, products: List[UXProduct]) -> str:
        """Get price range from products"""
        if not products:
            return "Varied"
        
        prices = []
        for product in products:
            try:
                # Extract numeric price
                price_str = product.price.replace("₹", "").replace(",", "")
                prices.append(float(price_str))
            except:
                continue
        
        if not prices:
            return "Varied"
        
        min_price = min(prices)
        max_price = max(prices)
        
        if min_price == max_price:
            return f"₹{int(min_price)}"
        else:
            return f"₹{int(min_price)}-{int(max_price)}"
    
    def _has_premium_options(self, products: List[UXProduct]) -> bool:
        """Check if there are premium-priced options"""
        for product in products:
            try:
                price_str = product.price.replace("₹", "").replace(",", "")
                if float(price_str) > 200:  # Consider 200+ as premium
                    return True
            except:
                continue
        return False
    
    def _get_recent_context(self, session: Dict[str, Any]) -> str:
        """Get recent conversation context"""
        history = session.get("history", [])
        if not history:
            return "New conversation"
        
        recent = history[-1]
        if isinstance(recent, dict):
            return recent.get("intent", "Continuing conversation")
        
        return "Ongoing conversation"
    
    def _has_personalization(self, ctx: UserContext) -> bool:
        """Check if response can be personalized"""
        session = ctx.session or {}
        return bool(
            session.get("budget") or 
            session.get("dietary_requirements") or 
            session.get("brands")
        )
    
    def _get_collection_title(self, intent: UXIntentType, product_count: int) -> str:
        """Generate collection title for MPM"""
        titles = {
            UXIntentType.IS_THIS_GOOD: "Recommended for You",
            UXIntentType.WHICH_IS_BETTER: "Top Comparisons",
            UXIntentType.SHOW_ALTERNATES: "Better Alternatives",
            UXIntentType.SHOW_OPTIONS: f"All {product_count} Options"
        }
        return titles.get(intent, "Product Collection")
    
    def _get_fallback_dpl(self, intent: UXIntentType, product_count: int) -> DPL:
        """Fallback DPL when generation fails"""
        messages = {
            UXIntentType.IS_THIS_GOOD: "This looks like a solid choice for your needs!",
            UXIntentType.WHICH_IS_BETTER: "Here's how these options compare for you.",
            UXIntentType.SHOW_ALTERNATES: f"Found {product_count} great alternatives to consider.",
            UXIntentType.SHOW_OPTIONS: f"Exploring all {product_count} options for you!"
        }
        
        return DPL(
            message=messages.get(intent, "Here are your options!"),
            context_hint="Fallback message due to generation failure",
            personalization_factors=["fallback"]
        )
    
    def _get_fallback_quick_replies(self, intent: UXIntentType) -> List[QuickReply]:
        """Fallback quick replies when generation fails"""
        fallback_qrs = {
            UXIntentType.IS_THIS_GOOD: [
                QuickReply("Why?", "explain choice", UXIntentType.IS_THIS_GOOD),
                QuickReply("Cheaper", "cheaper options", UXIntentType.SHOW_ALTERNATES),
                QuickReply("Add to cart", "add to cart")
            ],
            UXIntentType.WHICH_IS_BETTER: [
                QuickReply("Explain", "explain difference", UXIntentType.IS_THIS_GOOD),
                QuickReply("More options", "show more", UXIntentType.SHOW_OPTIONS)
            ],
            UXIntentType.SHOW_ALTERNATES: [
                QuickReply("Cheaper", "cheaper options", UXIntentType.SHOW_ALTERNATES),
                QuickReply("Premium", "premium options", UXIntentType.SHOW_ALTERNATES),
                QuickReply("More", "show more", UXIntentType.SHOW_OPTIONS)
            ],
            UXIntentType.SHOW_OPTIONS: [
                QuickReply("Budget", "budget options", UXIntentType.SHOW_ALTERNATES),
                QuickReply("Premium", "premium options", UXIntentType.SHOW_ALTERNATES),
                QuickReply("More", "show more", UXIntentType.SHOW_OPTIONS)
            ]
        }
        
        return fallback_qrs.get(intent, [
            QuickReply("More", "show more options", UXIntentType.SHOW_OPTIONS)
        ])


# Global service instance
_ux_response_generator: Optional[UXResponseGenerator] = None


def get_ux_response_generator() -> UXResponseGenerator:
    """Get the global UX response generator instance"""
    global _ux_response_generator
    if _ux_response_generator is None:
        _ux_response_generator = UXResponseGenerator()
    return _ux_response_generator