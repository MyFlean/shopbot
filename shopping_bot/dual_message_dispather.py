"""
Dual Message Dispatcher Service
Handles sending both text summary and WhatsApp Flow payloads sequentially
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from .models import BotResponse, ResponseType, FlowPayload
from .config import get_config

Cfg = get_config()
log = logging.getLogger(__name__)


class DualMessageDispatcher:
    """Service for dispatching dual messages (text + Flow)."""
    
    def __init__(self, whatsapp_client=None):
        """Initialize with WhatsApp client dependency injection."""
        self.whatsapp_client = whatsapp_client
        self.dispatch_delay = 0.5  # Delay between messages in seconds
    
    async def dispatch_response(
        self, 
        response: BotResponse, 
        user_id: str,
        phone_number: str
    ) -> Dict[str, Any]:
        """
        Main dispatch method handling both standard and Flow responses.
        
        Returns:
            Dict with dispatch results and metadata
        """
        
        dispatch_result = {
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "messages_sent": [],
            "errors": [],
            "success": True
        }
        
        try:
            # Always send the text message first
            text_result = await self._send_text_message(
                response.content.get("message", ""),
                phone_number,
                user_id
            )
            dispatch_result["messages_sent"].append(text_result)
            
            # If this is a Flow response, send Flow payload after delay
            if response.is_flow_response and response.flow_payload:
                await asyncio.sleep(self.dispatch_delay)
                
                flow_result = await self._send_flow_message(
                    response.flow_payload,
                    phone_number,
                    user_id
                )
                dispatch_result["messages_sent"].append(flow_result)
                
                log.info(f"Dual dispatch completed for user {user_id}: text + Flow")
            else:
                log.info(f"Standard dispatch completed for user {user_id}: text only")
            
        except Exception as exc:
            log.error(f"Dispatch failed for user {user_id}: {exc}")
            dispatch_result["success"] = False
            dispatch_result["errors"].append(str(exc))
        
        return dispatch_result
    
    async def _send_text_message(
        self, 
        message_text: str, 
        phone_number: str,
        user_id: str
    ) -> Dict[str, Any]:
        """Send standard WhatsApp text message."""
        
        message_payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {
                "body": message_text
            }
        }
        
        if self.whatsapp_client:
            try:
                result = await self.whatsapp_client.send_message(message_payload)
                return {
                    "type": "text",
                    "status": "sent",
                    "message_id": result.get("messages", [{}])[0].get("id"),
                    "timestamp": datetime.now().isoformat()
                }
            except Exception as exc:
                log.error(f"Text message send failed for {user_id}: {exc}")
                return {
                    "type": "text", 
                    "status": "failed",
                    "error": str(exc),
                    "timestamp": datetime.now().isoformat()
                }
        else:
            # Mock mode for testing
            log.info(f"Mock text message sent to {phone_number}: {message_text[:50]}...")
            return {
                "type": "text",
                "status": "sent_mock",
                "message_id": f"mock_text_{int(datetime.now().timestamp())}",
                "timestamp": datetime.now().isoformat()
            }
    
    async def _send_flow_message(
        self, 
        flow_payload: FlowPayload, 
        phone_number: str,
        user_id: str
    ) -> Dict[str, Any]:
        """Send WhatsApp Flow message."""
        
        # Convert FlowPayload to WhatsApp API format
        whatsapp_flow_payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "header": {
                    "type": "text",
                    "text": flow_payload.header or "Product Options"
                },
                "body": {
                    "text": flow_payload.body or "Here are your options"
                },
                "footer": {
                    "text": flow_payload.footer or "Tap to explore"
                },
                "action": {
                    "name": "flow",
                    "parameters": {
                        "flow_message_version": "3",
                        "flow_token": flow_payload.flow_id,
                        "flow_id": Cfg.WHATSAPP_FLOW_ID,  # Configured Flow ID
                        "flow_cta": "View Options",
                        "flow_action": "navigate",
                        "flow_action_payload": {
                            "screen": "PRODUCT_CATALOG",
                            "data": flow_payload.flow_data
                        }
                    }
                }
            }
        }
        
        if self.whatsapp_client:
            try:
                result = await self.whatsapp_client.send_message(whatsapp_flow_payload)
                return {
                    "type": "flow",
                    "flow_type": flow_payload.flow_type,
                    "flow_id": flow_payload.flow_id,
                    "status": "sent",
                    "message_id": result.get("messages", [{}])[0].get("id"),
                    "timestamp": datetime.now().isoformat()
                }
            except Exception as exc:
                log.error(f"Flow message send failed for {user_id}: {exc}")
                return {
                    "type": "flow",
                    "status": "failed", 
                    "error": str(exc),
                    "timestamp": datetime.now().isoformat()
                }
        else:
            # Mock mode for testing
            log.info(f"Mock Flow sent to {phone_number}: {flow_payload.flow_type} with {len(flow_payload.flow_data.get('products_data', []))} products")
            return {
                "type": "flow",
                "flow_type": flow_payload.flow_type,
                "flow_id": flow_payload.flow_id,
                "status": "sent_mock",
                "message_id": f"mock_flow_{int(datetime.now().timestamp())}",
                "timestamp": datetime.now().isoformat()
            }
    
    def set_dispatch_delay(self, delay_seconds: float) -> None:
        """Configure delay between text and Flow messages."""
        self.dispatch_delay = max(0.1, min(5.0, delay_seconds))  # Clamp between 0.1-5 seconds
    
    async def dispatch_flow_only(
        self, 
        flow_payload: FlowPayload, 
        phone_number: str,
        user_id: str
    ) -> Dict[str, Any]:
        """Send only Flow message (for specific use cases)."""
        
        try:
            flow_result = await self._send_flow_message(flow_payload, phone_number, user_id)
            return {
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
                "messages_sent": [flow_result],
                "errors": [],
                "success": flow_result["status"] in ["sent", "sent_mock"]
            }
        except Exception as exc:
            return {
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
                "messages_sent": [],
                "errors": [str(exc)],
                "success": False
            }
    
    async def dispatch_text_only(
        self, 
        message_text: str, 
        phone_number: str,
        user_id: str
    ) -> Dict[str, Any]:
        """Send only text message (for fallback scenarios)."""
        
        try:
            text_result = await self._send_text_message(message_text, phone_number, user_id)
            return {
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
                "messages_sent": [text_result],
                "errors": [],
                "success": text_result["status"] in ["sent", "sent_mock"]
            }
        except Exception as exc:
            return {
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
                "messages_sent": [],
                "errors": [str(exc)],
                "success": False
            }


# Integration helper for existing webhook handlers
class FlowIntegrationHelper:
    """Helper class for integrating Flow dispatch into existing webhook handlers."""
    
    def __init__(self, bot_core, dispatcher: DualMessageDispatcher):
        self.bot_core = bot_core
        self.dispatcher = dispatcher
    
    async def process_and_dispatch(
        self, 
        query: str, 
        ctx, 
        phone_number: str
    ) -> Dict[str, Any]:
        """
        Process query through bot core and dispatch appropriate response.
        This is the main integration point for existing webhook handlers.
        """
        
        # Process query through enhanced bot core
        bot_response = await self.bot_core.process_query(query, ctx)
        
        # Dispatch response using dual message dispatcher
        dispatch_result = await self.dispatcher.dispatch_response(
            bot_response, 
            ctx.user_id, 
            phone_number
        )
        
        # Add bot response metadata to dispatch result
        dispatch_result["bot_response_type"] = bot_response.response_type.value
        dispatch_result["functions_executed"] = bot_response.functions_executed
        dispatch_result["has_flow"] = bot_response.is_flow_response
        
        return dispatch_result