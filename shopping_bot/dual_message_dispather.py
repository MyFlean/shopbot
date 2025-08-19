"""
Dual Message Dispatcher Service
Handles sending both text summary and WhatsApp Flow payloads sequentially
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from ..models import BotResponse, FlowPayload  # relative import
from ..config import get_config

Cfg = get_config()
log = logging.getLogger(__name__)


class DualMessageDispatcher:
    """Service for dispatching dual messages (text + Flow)."""
    
    def __init__(self, whatsapp_client=None):
        self.whatsapp_client = whatsapp_client
        self.dispatch_delay = 0.5  # seconds
    
    async def dispatch_response(
        self, 
        response: BotResponse, 
        user_id: str,
        phone_number: str,
        *,
        session_id: Optional[str] = None,
        processing_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        dispatch_result = {
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "messages_sent": [],
            "errors": [],
            "success": True
        }
        
        try:
            # 1) Text first (always)
            text_result = await self._send_text_message(
                response.content.get("message", "") or " ",
                phone_number,
                user_id
            )
            dispatch_result["messages_sent"].append(text_result)
            
            # 2) Flow (if present)
            has_flow = bool(getattr(response, "flow_payload", None)) and (
                getattr(response, "is_flow_response", False) or getattr(response, "requires_flow", False)
            )
            if has_flow:
                await asyncio.sleep(self.dispatch_delay)

                pid = processing_id or response.content.get("processing_id")
                extra_flow_data = {"processing_id": pid, "user_id": user_id}
                if session_id:
                    extra_flow_data["session_id"] = session_id

                flow_result = await self._send_flow_message(
                    response.flow_payload,  # type: ignore[attr-defined]
                    phone_number,
                    user_id,
                    extra_flow_data=extra_flow_data
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
        message_payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": message_text}
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
        user_id: str,
        *,
        extra_flow_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        # Merge/augment flow data (processing_id, user_id, session_id)
        base_flow_data = getattr(flow_payload, "flow_data", None) or {}
        flow_data = dict(base_flow_data)
        if extra_flow_data:
            for k, v in extra_flow_data.items():
                if v is not None and k not in flow_data:
                    flow_data[k] = v

        # Prefer explicit IDs/tokens on payload; fall back to config
        flow_id   = getattr(flow_payload, "flow_id", None) or Cfg.WHATSAPP_PRODUCT_RECOMMENDATIONS_FLOW_ID or Cfg.WHATSAPP_FLOW_ID
        flow_token = getattr(flow_payload, "flow_token", None) or getattr(Cfg, "WHATSAPP_FLOW_TOKEN", None)
        if not flow_token:
            log.warning("WHATSAPP_FLOW_TOKEN not configured; Flow may fail in production.")

        header_text = getattr(flow_payload, "header_text", None) or getattr(flow_payload, "header", None) or "Product Options"
        body_text   = getattr(flow_payload, "body_text", None)   or getattr(flow_payload, "body", None)   or "Here are your options"
        footer_text = getattr(flow_payload, "footer_text", None) or getattr(flow_payload, "footer", None) or "Tap to explore"
        screen_name = getattr(flow_payload, "screen", None) or "PRODUCT_LIST"

        whatsapp_flow_payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "header": {"type": "text", "text": header_text},
                "body":   {"text": body_text},
                "footer": {"text": footer_text},
                "action": {
                    "name": "flow",
                    "parameters": {
                        "flow_message_version": "3",
                        "flow_id": flow_id,          # the published Flow ID
                        "flow_token": flow_token,    # the Flow token from Meta
                        "flow_cta": "View Options",
                        "flow_action": "navigate",
                        "flow_action_payload": {
                            "screen": screen_name,
                            "data": flow_data
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
                    "flow_type": getattr(flow_payload, "flow_type", None),
                    "flow_id": flow_id,
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
            products_count = 0
            try:
                if isinstance(flow_data, dict):
                    if "products_data" in flow_data and isinstance(flow_data["products_data"], list):
                        products_count = len(flow_data["products_data"])
                    elif "products" in flow_data and isinstance(flow_data["products"], list):
                        products_count = len(flow_data["products"])
            except Exception:
                pass

            log.info(
                f"Mock Flow sent to {phone_number}: "
                f"{getattr(flow_payload, 'flow_type', None)} "
                f"with {products_count} products; "
                f"data keys={list(flow_data.keys())}"
            )
            return {
                "type": "flow",
                "flow_type": getattr(flow_payload, "flow_type", None),
                "flow_id": flow_id,
                "status": "sent_mock",
                "message_id": f"mock_flow_{int(datetime.now().timestamp())}",
                "timestamp": datetime.now().isoformat()
            }
    
    def set_dispatch_delay(self, delay_seconds: float) -> None:
        self.dispatch_delay = max(0.1, min(5.0, delay_seconds))
    
    async def dispatch_flow_only(
        self, 
        flow_payload: FlowPayload, 
        phone_number: str,
        user_id: str,
        *,
        session_id: Optional[str] = None,
        processing_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            extra_flow_data = {"processing_id": processing_id, "user_id": user_id}
            if session_id:
                extra_flow_data["session_id"] = session_id

            flow_result = await self._send_flow_message(
                flow_payload, phone_number, user_id, extra_flow_data=extra_flow_data
            )
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


class FlowIntegrationHelper:
    """Helper class for integrating Flow dispatch into existing webhook handlers."""
    
    def __init__(self, bot_core, dispatcher: DualMessageDispatcher):
        self.bot_core = bot_core
        self.dispatcher = dispatcher
    
    async def process_and_dispatch(
        self, 
        query: str, 
        ctx, 
        phone_number: str,
        *,
        session_id: Optional[str] = None,
        processing_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        bot_response = await self.bot_core.process_query(query, ctx)
        dispatch_result = await self.dispatcher.dispatch_response(
            bot_response, 
            ctx.user_id, 
            phone_number,
            session_id=session_id,
            processing_id=processing_id,
        )
        dispatch_result["bot_response_type"] = bot_response.response_type.value
        dispatch_result["functions_executed"] = getattr(bot_response, "functions_executed", [])
        dispatch_result["has_flow"] = bool(getattr(bot_response, "flow_payload", None))
        return dispatch_result
