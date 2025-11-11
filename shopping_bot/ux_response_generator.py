# shopping_bot/ux_response_generator.py
"""
UX Response Generator - NEW MODULE
==================================

Modular service that takes classified 4-intent + answer + product IDs 
and generates UX-ready responses with DPL, PSL, and QRs.

Designed to be service-ready for future separation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import anthropic

from .config import get_config
from .models import UserContext

Cfg = get_config()
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# UX Response Data Models
# ─────────────────────────────────────────────────────────────

@dataclass
class UXResponse:
    """Complete UX response with all components."""
    dpl_runtime_text: str
    ux_surface: str  # "SPM" or "MPM"
    quick_replies: List[str]
    product_ids: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "dpl_runtime_text": self.dpl_runtime_text,
            "ux_surface": self.ux_surface,
            "quick_replies": self.quick_replies,
            "product_ids": self.product_ids
        }


# ─────────────────────────────────────────────────────────────
# LLM Tool for UX Generation
# ─────────────────────────────────────────────────────────────

UX_GENERATION_TOOL = {
    "name": "generate_ux_response",
    "description": "Generate UX-ready response with DPL, surface type, and quick replies",
    "input_schema": {
        "type": "object",
        "properties": {
            "dpl_runtime_text": {
                "type": "string",
                "description": "Dynamic Persuasion Layer text explaining why the suggested products are good"
            },
            "ux_surface": {
                "type": "string",
                "enum": ["SPM", "MPM"],
                "description": "SPM for single item, MPM for multiple items/collections"
            },
            "quick_replies": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 4,
                "description": "3-4 quick reply button labels for intent pivots"
            }
        },
        "required": ["dpl_runtime_text", "ux_surface", "quick_replies"]
    }
}


# ─────────────────────────────────────────────────────────────
# Intent Configuration
# ─────────────────────────────────────────────────────────────

INTENT_UX_CONFIG = {
    "is_this_good": {
        "default_surface": "SPM",
        "default_quick_replies": ["Why?", "Cleaner swap", "Cheaper"],
        "dpl_focus": "verdict on why the product is good"
    },
    "which_is_better": {
        "default_surface": "MPM", 
        "default_quick_replies": ["Explain pick", "Show alternates", "Add to cart"],
        "dpl_focus": "verdict on why the recommended choice is best"
    },
    "show_me_alternate": {
        "default_surface": "MPM",
        "default_quick_replies": ["Only cleaner", "Under ₹{x}", "Higher protein"],
        "dpl_focus": "verdict on why these alternatives are good options"
    },
    "show_me_options": {
        "default_surface": "MPM", 
        "default_quick_replies": ["Cheaper", "Spicier", "Higher protein", "Show 10 more"],
        "dpl_focus": "verdict on why these options suit your needs"
    }
}


# ─────────────────────────────────────────────────────────────
# UX Response Generator Service
# ─────────────────────────────────────────────────────────────

class UXResponseGenerator:
    """
    Modular service for generating UX-ready responses.
    Designed to be separable as future microservice.
    """
    
    def __init__(self):
        self.anthropic = anthropic.AsyncAnthropic(api_key=Cfg.ANTHROPIC_API_KEY)
    
    async def generate_ux_response(
        self,
        intent: str,
        previous_answer: Dict[str, Any],
        product_ids: List[str],
        ctx: UserContext,
        user_query: str
    ) -> UXResponse:
        """
        Main method to generate UX-ready response.
        
        Args:
            intent: One of the 4 intents (is_this_good, which_is_better, etc.)
            previous_answer: The answer dict from existing LLM service
            product_ids: List of product IDs to surface
            ctx: User context
            user_query: Original user query
            
        Returns:
            UXResponse with all components
        """
        
        if intent not in INTENT_UX_CONFIG:
            log.warning(f"Unknown intent: {intent}, defaulting to show_me_options")
            intent = "show_me_options"
        
        try:
            # Generate UX response using LLM
            ux_response = await self._call_llm_for_ux(
                intent, previous_answer, product_ids, ctx, user_query
            )
            
            if ux_response:
                log.info(f"UX_RESPONSE_GENERATED | intent={intent} | surface={ux_response.ux_surface} | qr_count={len(ux_response.quick_replies)}")
                return ux_response
            else:
                # Fallback to template-based generation
                return self._generate_fallback_response(intent, previous_answer, product_ids, ctx)
                
        except Exception as e:
            log.error(f"UX_GENERATION_ERROR | intent={intent} | error={e}", exc_info=True)
            return self._generate_fallback_response(intent, previous_answer, product_ids, ctx)
    
    async def _call_llm_for_ux(
        self,
        intent: str,
        previous_answer: Dict[str, Any],
        product_ids: List[str],
        ctx: UserContext,
        user_query: str
    ) -> Optional[UXResponse]:
        """Call LLM to generate UX components."""
        
        intent_config = INTENT_UX_CONFIG[intent]
        
        # Build context for LLM (pass enriched briefs if available)
        context_data = {
            "user_query": user_query,
            "intent": intent,
            "previous_answer": previous_answer,
            "product_count": len(product_ids),
            "user_session": {
                k: v for k, v in ctx.session.items() 
                if k in ['budget', 'preferences', 'dietary_requirements', 'product_category']
            },
            "enriched_top": previous_answer.get("top_products_brief", [])
        }
        
        # Extract budget for dynamic QR generation
        budget_info = self._extract_budget_info(ctx)
        
        try:
            log.info(
                f"UX_PROMPT_INPUT | intent={intent} | product_count={len(product_ids)} | enriched_top_len={len(context_data.get('enriched_top', []))}"
            )
        except Exception:
            pass

        prompt = self._build_ux_prompt(intent, context_data, intent_config, budget_info)
        
        try:
            resp = await self.anthropic.messages.create(
                model=Cfg.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                tools=[UX_GENERATION_TOOL],
                tool_choice={"type": "tool", "name": "generate_ux_response"},
                temperature=0.7,
                max_tokens=500,
            )
            
            tool_use = self._pick_tool(resp, "generate_ux_response")
            if not tool_use:
                return None
            
            result = tool_use.input or {}
            
            # Enforce surface rule by intent only:
            # - is_this_good → SPM
            # - which_is_better/show_me_alternate/show_me_options → MPM
            enforced_surface = "SPM" if intent == "is_this_good" else "MPM"

            return UXResponse(
                dpl_runtime_text=result.get("dpl_runtime_text", ""),
                ux_surface=enforced_surface,
                quick_replies=result.get("quick_replies", intent_config["default_quick_replies"]),
                product_ids=product_ids
            )
            
        except Exception as e:
            log.error(f"LLM_UX_CALL_ERROR | intent={intent} | error={e}")
            return None
    
    def _build_ux_prompt(
        self, 
        intent: str, 
        context_data: Dict[str, Any],
        intent_config: Dict[str, Any],
        budget_info: Dict[str, Any]
    ) -> str:
        """Build the prompt for UX generation."""
        
        return f"""
You are generating UX components for a WhatsApp shopping bot response.

### INTENT: {intent}
### CONTEXT: {json.dumps(context_data, ensure_ascii=False, indent=2)}
### BUDGET INFO: {json.dumps(budget_info, ensure_ascii=False)}

### YOUR TASK:
Generate 3 components for this {intent} response:

1. **DPL (Dynamic Persuasion Layer)**:
   - Focus: {intent_config['dpl_focus']}
   - Write as HIGH-CONVERSION marketing copy that drives immediate action (1-2 sentences)
   - Transform technical data into irresistible benefits that make shoppers want this NOW
   - Use psychological triggers: social proof, urgency, exclusivity, emotional connection
   - Leverage product quality signals to create desire:
     • flean_percentile becomes "health score that stands out above the rest"
     • bonus_percentiles become "what smart shoppers are choosing for [benefit]"
     • penalty_percentiles become "finally free from [pain point] that ruins other options"
   - Also use emotional triggers:
     • health_claims and dietary_labels become "the clean choice your body deserves"
     • nutritional data becomes "gives you the [benefit] you've been missing"
     • processing_type and tags_and_sentiments become "the premium feel you crave"
     • flean_score.bonuses become "delivers the quality difference you can actually taste"
   - Make it feel like a personal recommendation from a trusted expert who gets their needs.

2. **UX Surface Type**:
   - SPM: Single Product Module (use for 1 item or clear single recommendation)
   - MPM: Multiple Product Module (use for 2+ items, comparisons, or collections)
   - Consider: {context_data.get('product_count', 0)} products available

3. **Quick Replies** (3-4 buttons):
   - Intent-specific action buttons for user to pivot/refine
   - Examples for {intent}: {intent_config['default_quick_replies']}
   - Make them contextual to the user's situation
   - If budget info available, include price-based options like "Under ₹{budget_info.get('upper_limit', 500)}"
   - Keep labels short (1-3 words)

### DPL HIGH-CONVERSION RULES (MANDATORY)
- Structure: 1) Hook with emotional benefit; 2) Social proof + specificity; 3) Call to action urgency
- REQUIRED: Transform metrics into benefits (e.g., "top 22%" becomes "stands out above 78% of options")
- BANNED PHRASES: great, awesome, amazing, healthy, good (use desire-creating language instead)
- Max 3 sentences; each ≤20 words; conversational yet sophisticated tone that builds trust and desire

### PERSONAL CARE CONTEXT (MANDATORY WHEN PRESENT)
- You MUST incorporate the personal care planning outputs if available:
  • efficacy_terms (positive aspects to emphasize)
  • avoid_terms (negatives to avoid; reflect why excluded)
  • skin_types / hair_types (suitability fit)
- Make the DPL explicitly reference how recommendations match efficacy_terms and avoid avoid_terms (e.g., "anti-dandruff focus, fragrance-free fit for oily scalp").

### DPL HERO FOCUS
- For MPM responses: First sentence features the TOP PICK with a specific metric; second sentence states a quantified benefit; third sentence mentions alternatives or a filter.
- For SPM responses: Focus entirely on the single product with a clear BUY/CONSIDER/SKIP verdict; do not discuss other options.

### COMMANDMENTS FOR QUICK REPLIES
- Max 3 words per button; action verbs preferred; always include one price filter and one quality filter.
- For show_me_options: include "Top rated only" or "Score 70+", "Under ₹{max(50, (budget_info.get('upper_limit') or 100)//2)}", "Less sodium" or "More protein", and "Different brands" or "Baked only".
- For is_this_good: include "Compare similar", "Healthier options", "Cheaper options", "Why this rating?".
- For which_is_better: include "See more options", "Compare nutrition", "Find middle ground", "Different category".

### HIGH-CONVERSION EXAMPLES:
- "is_this_good" → DPL: "Finally, the protein powder that actually delivers what your body craves. Stands out above 95% of options with 25g protein. What serious fitness enthusiasts choose for real results."
- "which_is_better" → DPL: "The smart choice that premium shoppers keep coming back to. Exceptional 78/100 health score and saves you from 25% more sodium than typical options. Your shortcut to better nutrition."
- "show_me_options" → DPL: "Discovered these premium picks that deliver real quality without the premium price. Our ₹72 standout beats 70% of 'healthy' alternatives. What conscious snackers are stocking up on right now." 

Return ONLY a tool call to generate_ux_response.
"""
    
    def _extract_budget_info(self, ctx: UserContext) -> Dict[str, Any]:
        """Extract budget information for dynamic QR generation."""
        budget_info = {}
        
        try:
            # Try to get budget from session
            budget = ctx.session.get('budget')
            if budget:
                # Parse budget string like "₹100-500", "under ₹200", etc.
                budget_str = str(budget).lower().replace('₹', '').replace('rs', '').replace('rupees', '')
                
                if '-' in budget_str:
                    parts = budget_str.split('-')
                    if len(parts) == 2:
                        try:
                            budget_info['lower_limit'] = int(parts[0].strip())
                            budget_info['upper_limit'] = int(parts[1].strip())
                        except ValueError:
                            pass
                elif 'under' in budget_str:
                    try:
                        amount = int(''.join(filter(str.isdigit, budget_str)))
                        budget_info['upper_limit'] = amount
                    except ValueError:
                        pass
                elif 'above' in budget_str or 'over' in budget_str:
                    try:
                        amount = int(''.join(filter(str.isdigit, budget_str)))
                        budget_info['lower_limit'] = amount
                    except ValueError:
                        pass
                else:
                    try:
                        amount = int(''.join(filter(str.isdigit, budget_str)))
                        budget_info['target'] = amount
                    except ValueError:
                        pass
        except Exception as e:
            log.debug(f"BUDGET_EXTRACTION_ERROR | error={e}")
        
        return budget_info
    
    def _generate_fallback_response(
        self,
        intent: str,
        previous_answer: Dict[str, Any],
        product_ids: List[str],
        ctx: UserContext
    ) -> UXResponse:
        """Generate fallback response using templates."""
        
        intent_config = INTENT_UX_CONFIG.get(intent, INTENT_UX_CONFIG["show_me_options"])
        
        # Template-based DPL generation
        dpl_templates = {
            "is_this_good": "This is a solid choice based on your preferences.",
            "which_is_better": "I'd recommend the first option for the best value.",
            "show_me_alternate": "These alternatives should work well for your needs.",
            "show_me_options": "Here are some great options to consider."
        }
        
        dpl_text = dpl_templates.get(intent, dpl_templates["show_me_options"])
        
        # Enhance DPL with context if available
        if previous_answer.get("summary_message"):
            summary = previous_answer["summary_message"]
            if len(summary) > 20:  # Use summary if substantial
                dpl_text = summary[:150] + "..." if len(summary) > 150 else summary
        
        # Enforce surface type strictly by intent
        surface_type = "SPM" if intent == "is_this_good" else "MPM"
        
        # Generate contextual quick replies
        qr_options = self._generate_contextual_quick_replies(intent, ctx)
        
        log.info(f"UX_FALLBACK_GENERATED | intent={intent} | surface={surface_type}")
        
        return UXResponse(
            dpl_runtime_text=dpl_text,
            ux_surface=surface_type,
            quick_replies=qr_options,
            product_ids=product_ids
        )
    
    def _generate_contextual_quick_replies(self, intent: str, ctx: UserContext) -> List[str]:
        """Generate contextual quick replies based on intent and context."""
        
        base_options = INTENT_UX_CONFIG[intent]["default_quick_replies"]
        
        # Try to make them more contextual
        contextual_options = []
        
        for option in base_options:
            if "₹{x}" in option:
                # Replace with actual budget if available
                budget_info = self._extract_budget_info(ctx)
                if budget_info.get('upper_limit'):
                    option = option.replace("₹{x}", f"₹{budget_info['upper_limit']}")
                else:
                    option = "Under ₹500"  # Default
            
            contextual_options.append(option)
        
        # Add dietary-specific options if relevant
        dietary = ctx.session.get('dietary_requirements')
        if dietary and isinstance(dietary, str):
            if 'vegan' in dietary.lower():
                contextual_options = [opt.replace("Cleaner", "Vegan") for opt in contextual_options]
            elif 'gluten' in dietary.lower():
                contextual_options = [opt.replace("Cleaner", "Gluten-free") for opt in contextual_options]
        
        return contextual_options[:4]  # Max 4 options
    
    def _pick_tool(self, resp, tool_name: str):
        """Extract tool use from Anthropic response."""
        try:
            for block in (resp.content or []):
                if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                    return block
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────
# Service Factory
# ─────────────────────────────────────────────────────────────

_ux_generator_instance: Optional[UXResponseGenerator] = None

def get_ux_response_generator() -> UXResponseGenerator:
    """Get global UX response generator instance."""
    global _ux_generator_instance
    if _ux_generator_instance is None:
        _ux_generator_instance = UXResponseGenerator()
    return _ux_generator_instance


# ─────────────────────────────────────────────────────────────
# Integration Helper Functions
# ─────────────────────────────────────────────────────────────

async def generate_ux_response_for_intent(
    intent: str,
    previous_answer: Dict[str, Any],
    ctx: UserContext,
    user_query: str
) -> Dict[str, Any]:
    """
    High-level integration function.
    
    Takes intent classification result and previous answer,
    extracts product IDs, and returns UX-ready response.
    """
    
    # Short-circuit: if previous_answer already contains a UX block from unified call, return as-is
    try:
        if isinstance(previous_answer.get("ux_response"), dict):
            # Ensure product_intent present
            if not previous_answer.get("product_intent") and isinstance(intent, str):
                previous_answer["product_intent"] = intent
            # Ensure product_ids live inside ux_response
            if isinstance(previous_answer.get("product_ids"), list) and previous_answer["product_ids"]:
                try:
                    ux = previous_answer.get("ux_response") or {}
                    if isinstance(ux, dict) and not ux.get("product_ids"):
                        ux["product_ids"] = [str(x) for x in previous_answer["product_ids"] if str(x).strip()]
                        previous_answer["ux_response"] = ux
                except Exception:
                    pass
                previous_answer.pop("product_ids", None)
            log.info("UX_EARLY_RETURN | using unified ux_response from previous_answer")
            return previous_answer
    except Exception:
        pass

    # Extract product IDs for UX usage
    product_ids: List[str] = []
    # 1) Prefer explicit product_ids if provided by upstream (e.g., MPM flow)
    if isinstance(previous_answer.get("product_ids"), list) and previous_answer["product_ids"]:
        product_ids = [str(pid) for pid in previous_answer["product_ids"] if str(pid).strip()]
    else:
        # 2) Fallback: extract from products list
        if previous_answer.get("products"):
            products = previous_answer["products"]
            if isinstance(products, list):
                for product in products:
                    if isinstance(product, dict) and product.get("id"):
                        product_ids.append(str(product["id"]))
        # 3) If still empty, synthesize from product names
        if not product_ids and previous_answer.get("products"):
            products = previous_answer["products"]
            if isinstance(products, list):
                for i, product in enumerate(products):
                    if isinstance(product, dict):
                        name = product.get("text", product.get("name", f"product_{i}"))
                        product_ids.append(f"prod_{hash(name)%1000000}")

    # 4) Belt-and-suspenders: if still empty, backfill from fetched_data (ES results)
    if not product_ids:
        try:
            fetched_block = (ctx.fetched_data or {}).get("search_products") or {}
            payload = fetched_block.get("data", fetched_block)
            products = payload.get("products", []) if isinstance(payload, dict) else []
            for p in products[:10]:
                pid = p.get("id") or f"prod_{hash(p.get('name','') or p.get('title',''))%1000000}"
                sid = str(pid)
                if sid and sid not in product_ids:
                    product_ids.append(sid)
        except Exception:
            pass
    
    # Generate UX response
    generator = get_ux_response_generator()
    ux_response = await generator.generate_ux_response(
        intent=intent,
        previous_answer=previous_answer,
        product_ids=product_ids,
        ctx=ctx,
        user_query=user_query
    )
    
    # Combine with original answer
    result = dict(previous_answer)  # Copy original
    result.update({
        "ux_response": ux_response.to_dict(),
        "product_intent": intent
    })
    # Ensure product_ids live only inside ux_response (not at root content)
    if "product_ids" in result:
        try:
            # Keep source of truth inside ux_response
            if isinstance(result.get("ux_response"), dict) and not result["ux_response"].get("product_ids"):
                result["ux_response"]["product_ids"] = product_ids
        except Exception:
            pass
        del result["product_ids"]
    
    log.info(f"UX_INTEGRATION_COMPLETE | intent={intent} | products={len(product_ids)}")
    return result