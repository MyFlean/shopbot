"""
Enhanced dataclass models with UX-driven components.
Extends the existing models to support DPL, PSL, and QRs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Union, Optional
from enum import Enum

from .enums import (
    QueryIntent, ResponseType, BackendFunction, UserSlot,
    UXIntentType, PSLType, EnhancedResponseType
)


@dataclass
class UserContext:
    user_id: str
    session_id: str
    permanent: Dict[str, Any] = field(default_factory=dict)
    session: Dict[str, Any] = field(default_factory=dict)
    fetched_data: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


@dataclass
class RequirementAssessment:
    intent: QueryIntent
    missing_data: List[Union[BackendFunction, UserSlot]]
    rationale: Dict[str, str]
    priority_order: List[Union[BackendFunction, UserSlot]]


# NEW: Quick Reply button
@dataclass
class QuickReply:
    """Individual quick reply button"""
    label: str              # Display text (e.g., "Why?", "Cheaper")
    value: str              # Action value to send back
    intent_type: Optional[UXIntentType] = None  # What UX pattern this triggers
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"label": self.label, "value": self.value}
        if self.intent_type:
            result["intent_type"] = self.intent_type.value
        return result


# NEW: Dynamic Persuasion Layer
@dataclass
class DPL:
    """Dynamic Persuasion Layer - runtime personalized text"""
    message: str            # Main persuasive message
    context_hint: Optional[str] = None  # Why this message was chosen
    personalization_factors: Optional[List[str]] = None  # What made it personal
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"message": self.message}
        if self.context_hint:
            result["context_hint"] = self.context_hint
        if self.personalization_factors:
            result["personalization_factors"] = self.personalization_factors
        return result


# NEW: Enhanced Product Data for UX
@dataclass
class UXProduct:
    """Enhanced product data for UX templates"""
    id: str
    name: str
    price: str
    image_url: Optional[str] = None
    brand: Optional[str] = None
    rating: Optional[float] = None
    
    # UX-specific fields
    persuasion_hook: Optional[str] = None    # One-liner why to buy
    key_differentiator: Optional[str] = None # What makes it special
    cart_action: Optional[str] = None        # Direct cart action ID
    
    # Nutritional/features for comparison
    features: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "price": self.price,
            "image_url": self.image_url,
            "brand": self.brand,
            "rating": self.rating,
            "persuasion_hook": self.persuasion_hook,
            "key_differentiator": self.key_differentiator,
            "cart_action": self.cart_action,
            "features": self.features or {}
        }


# NEW: Product Surface Layer
@dataclass
class PSL:
    """Product Surface Layer - template with products"""
    template_type: PSLType
    products: List[UXProduct]
    
    # Template-specific config
    max_visible: Optional[int] = None     # For carousel
    collection_title: Optional[str] = None  # For MPM
    view_more_action: Optional[str] = None  # Action for "View items"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_type": self.template_type.value,
            "products": [p.to_dict() for p in self.products],
            "max_visible": self.max_visible,
            "collection_title": self.collection_title,
            "view_more_action": self.view_more_action
        }


# NEW: Enhanced UX Response
@dataclass
class UXResponse:
    """Complete UX-driven response with DPL, PSL, and QRs"""
    ux_intent: UXIntentType
    dpl: DPL                    # Dynamic persuasion text
    psl: PSL                    # Product surface layer
    quick_replies: List[QuickReply]  # Action buttons
    
    # Metadata
    confidence_score: Optional[float] = None
    personalization_applied: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ux_intent": self.ux_intent.value,
            "dpl": self.dpl.to_dict(),
            "psl": self.psl.to_dict(),
            "quick_replies": [qr.to_dict() for qr in self.quick_replies],
            "confidence_score": self.confidence_score,
            "personalization_applied": self.personalization_applied
        }


# Enhanced BotResponse with UX support
@dataclass
class EnhancedBotResponse:
    """Enhanced bot response supporting both legacy and new UX patterns"""
    response_type: EnhancedResponseType
    content: Dict[str, Any]
    functions_executed: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # NEW: UX components (optional)
    ux_response: Optional[UXResponse] = None
    
    def to_legacy_response(self) -> 'BotResponse':
        """Convert to legacy BotResponse format for backward compatibility"""
        # Map enhanced response types back to original types
        legacy_type_map = {
            EnhancedResponseType.QUESTION: ResponseType.QUESTION,
            EnhancedResponseType.PROCESSING_STUB: ResponseType.PROCESSING_STUB,
            EnhancedResponseType.ERROR: ResponseType.ERROR,
            EnhancedResponseType.CASUAL: ResponseType.FINAL_ANSWER,
            EnhancedResponseType.UX_SPM: ResponseType.FINAL_ANSWER,
            EnhancedResponseType.UX_CAROUSEL: ResponseType.FINAL_ANSWER,
            EnhancedResponseType.UX_MPM: ResponseType.FINAL_ANSWER,
        }
        
        legacy_type = legacy_type_map.get(self.response_type, ResponseType.FINAL_ANSWER)
        
        return BotResponse(
            response_type=legacy_type,
            content=self.content,
            functions_executed=self.functions_executed,
            timestamp=self.timestamp
        )
    
    def to_json(self) -> str:
        data = {
            "response_type": self.response_type.value,
            "content": self.content,
            "functions_executed": self.functions_executed,
            "timestamp": self.timestamp
        }
        if self.ux_response:
            data["ux_response"] = self.ux_response.to_dict()
        return json.dumps(data, default=str)


# Keep original BotResponse for backward compatibility
@dataclass
class BotResponse:
    response_type: ResponseType
    content: Dict[str, Any]
    functions_executed: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


# Follow-up models (unchanged but included for completeness)
@dataclass
class FollowUpPatch:
    slots: Dict[str, Any]
    intent_override: str | None = None
    reset_context: bool = False


@dataclass
class FollowUpResult:
    is_follow_up: bool
    patch: FollowUpPatch
    reason: str = ""


# Legacy models for backward compatibility
class FlowType(Enum):
    """Types of WhatsApp Flows supported"""
    PRODUCT_CATALOG = "product_catalog"
    COMPARISON = "comparison" 
    RECOMMENDATION = "recommendation"


@dataclass
class ProductData:
    """Structured product data for Flow rendering"""
    product_id: str
    title: str
    subtitle: str
    image_url: str
    price: str
    rating: Optional[float] = None
    discount: Optional[str] = None
    availability: Optional[str] = None
    brand: Optional[str] = None
    key_features: Optional[List[str]] = None


@dataclass
class FlowPayload:
    """WhatsApp Flow payload data"""
    flow_type: FlowType
    products: List[ProductData]
    header_text: str
    footer_text: Optional[str] = None
    action_buttons: Optional[List[Dict[str, str]]] = None