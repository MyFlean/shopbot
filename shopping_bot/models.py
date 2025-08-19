"""
Dataclass models shared across the whole application.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Union, Optional
from enum import Enum

from .enums import QueryIntent, ResponseType, BackendFunction, UserSlot


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
    # Allow both slots & backend functions
    missing_data: List[Union[BackendFunction, UserSlot]]
    rationale: Dict[str, str]
    priority_order: List[Union[BackendFunction, UserSlot]]


@dataclass
class BotResponse:
    response_type: ResponseType
    content: Dict[str, Any]
    functions_executed: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


# NEW ── Follow-up result & patch
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


# ══════════════════════════════════════════════════════════════════════════════
# Enhanced Models for WhatsApp Flow Support
# ══════════════════════════════════════════════════════════════════════════════

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


@dataclass
class EnhancedBotResponse:
    """Enhanced response that extends BotResponse with Flow support"""
    # Base response data
    response_type: ResponseType
    content: Dict[str, Any]
    functions_executed: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # Flow enhancement fields
    flow_payload: Optional[FlowPayload] = None
    requires_flow: bool = False
    
    def to_legacy_bot_response(self) -> BotResponse:
        """Convert to legacy BotResponse format"""
        return BotResponse(
            response_type=self.response_type,
            content=self.content,
            functions_executed=self.functions_executed,
            timestamp=self.timestamp
        )
    
    def to_dual_messages(self) -> List[Dict[str, Any]]:
        """Convert to dual message format for WhatsApp API"""
        messages = []
        
        # Text message (always first)
        text_msg = {
            "type": "text",
            "content": {
                "message": self.content.get("message", ""),
                "response_type": self.response_type.value
            }
        }
        if self.functions_executed:
            text_msg["content"]["functions_executed"] = self.functions_executed
        messages.append(text_msg)
        
        # Flow message (if available)
        if self.requires_flow and self.flow_payload:
            flow_msg = {
                "type": "flow", 
                "content": self._generate_flow_json()
            }
            messages.append(flow_msg)
            
        return messages
    
    def _generate_flow_json(self) -> Dict[str, Any]:
        """Generate WhatsApp-compliant Flow JSON"""
        if not self.flow_payload:
            return {}
            
        return {
            "type": "flow",
            "header": {
                "type": "text",
                "text": self.flow_payload.header_text
            },
            "body": {
                "type": "text",
                "text": self.content.get("message", "")
            },
            "footer": {
                "type": "text", 
                "text": self.flow_payload.footer_text or "Tap to explore options"
            },
            "action": {
                "type": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_token": f"flow_{self.flow_payload.flow_type.value}",
                    "flow_id": self._get_flow_id(),
                    "flow_cta": "View Options",
                    "flow_action": "data_exchange",
                    "flow_action_payload": {
                        "screen": "PRODUCT_LIST",
                        "data": {
                            "products": [self._serialize_product(p) for p in self.flow_payload.products]
                        }
                    }
                }
            }
        }
    
    def _get_flow_id(self) -> str:
        """Get Flow ID based on flow type"""
        flow_ids = {
            FlowType.PRODUCT_CATALOG: "product_catalog_flow_v1",
            FlowType.COMPARISON: "product_comparison_flow_v1",
            FlowType.RECOMMENDATION: "product_recommendation_flow_v1"
        }
        return flow_ids.get(self.flow_payload.flow_type, "default_flow_v1")
    
    def _serialize_product(self, product: ProductData) -> Dict[str, Any]:
        """Serialize product data for Flow"""
        return {
            "id": product.product_id,
            "title": product.title,
            "subtitle": product.subtitle,
            "image": product.image_url,
            "price": product.price,
            "rating": product.rating,
            "discount": product.discount,
            "availability": product.availability,
            "brand": product.brand,
            "features": product.key_features or []
        }
    
    def to_json(self) -> str:
        """Convert to JSON (for compatibility)"""
        return json.dumps({
            "response_type": self.response_type.value,
            "content": self.content,
            "functions_executed": self.functions_executed,
            "timestamp": self.timestamp,
            "requires_flow": self.requires_flow,
            "flow_payload": asdict(self.flow_payload) if self.flow_payload else None
        }, default=str)