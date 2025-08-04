"""
Enhanced Chat Route with Flow Support
───────────────────────────────────────
Replace your existing shopping_bot/routes/chat.py with this enhanced version
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Union

from flask import Blueprint, current_app, jsonify, request

from ..models import BotResponse, EnhancedBotResponse

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)


@bp.post("/chat")
async def chat() -> tuple[Dict[str, Any], int]:  # type: ignore[override]
    try:
        data: Dict[str, str] = request.get_json(force=True)  # type: ignore[assignment]

        # --------------------------------------------------
        # Validate basic schema
        # --------------------------------------------------
        missing = [k for k in ("user_id", "session_id", "message") if k not in data]
        if missing:
            return (
                {"error": f"Missing required fields: {', '.join(missing)}"},
                400,
            )

        user_id = data["user_id"]
        session_id = data["session_id"]
        message = data["message"]

        # --------------------------------------------------
        # Optional parameters for Flow control
        # --------------------------------------------------
        enable_flows = data.get("enable_flows", True)  # Default to enabled
        response_format = data.get("response_format", "auto")  # auto, legacy, dual, enhanced

        # --------------------------------------------------
        # Resolve helpers from app context
        # --------------------------------------------------
        ctx_mgr = current_app.extensions["ctx_mgr"]  # RedisContextManager
        
        # Try to get enhanced bot core first, fallback to base
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        base_bot = current_app.extensions.get("bot_core")
        
        if not enhanced_bot and not base_bot:
            return jsonify({"error": "Bot core not initialized"}), 500

        # --------------------------------------------------
        # Load context and process query
        # --------------------------------------------------
        ctx = ctx_mgr.get_context(user_id, session_id)
        
        # Choose processing method based on available components and preferences
        if enhanced_bot and enable_flows:
            bot_resp = await enhanced_bot.process_query(message, ctx, enable_flows=True)
        elif enhanced_bot and not enable_flows:
            bot_resp = await enhanced_bot.process_query_legacy(message, ctx)
        else:
            # Use base bot core
            bot_resp = await base_bot.process_query(message, ctx)

        # --------------------------------------------------
        # Format response based on requested format
        # --------------------------------------------------
        return _format_response(bot_resp, response_format)

    except Exception as exc:  # noqa: BLE001
        log.exception("chat endpoint failed")
        return jsonify({"error": str(exc)}), 500


@bp.post("/chat/whatsapp")
async def chat_whatsapp() -> tuple[Union[Dict[str, Any], List[Dict[str, Any]]], int]:
    """
    WhatsApp-optimized endpoint that returns dual messages when appropriate
    """
    try:
        data: Dict[str, str] = request.get_json(force=True)  # type: ignore[assignment]

        # --------------------------------------------------
        # Validate basic schema
        # --------------------------------------------------
        missing = [k for k in ("user_id", "message") if k not in data]
        if missing:
            return (
                {"error": f"Missing required fields: {', '.join(missing)}"},
                400,
            )

        user_id = data["user_id"]
        session_id = data.get("session_id", user_id)  # Use user_id as session_id if not provided
        message = data["message"]

        # --------------------------------------------------
        # Resolve components
        # --------------------------------------------------
        ctx_mgr = current_app.extensions["ctx_mgr"]
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        
        if not enhanced_bot:
            return jsonify({"error": "Enhanced bot core required for WhatsApp endpoint"}), 500

        # --------------------------------------------------
        # Process with enhanced bot
        # --------------------------------------------------
        ctx = ctx_mgr.get_context(user_id, session_id)
        bot_resp = await enhanced_bot.process_query(message, ctx, enable_flows=True)

        # --------------------------------------------------
        # Return dual messages format for WhatsApp
        # --------------------------------------------------
        if isinstance(bot_resp, EnhancedBotResponse):
            if bot_resp.requires_flow:
                # Return dual messages
                return jsonify(bot_resp.to_dual_messages()), 200
            else:
                # Return single text message
                return jsonify([{
                    "type": "text",
                    "content": {
                        "message": bot_resp.content.get("message", ""),
                        "response_type": bot_resp.response_type.value
                    }
                }]), 200
        else:
            # Legacy BotResponse - convert to single message
            return jsonify([{
                "type": "text",
                "content": {
                    "message": bot_resp.content.get("message", ""),
                    "response_type": bot_resp.response_type.value
                }
            }]), 200

    except Exception as exc:  # noqa: BLE001
        log.exception("WhatsApp chat endpoint failed")
        return jsonify({"error": str(exc)}), 500


@bp.get("/chat/flows/status")
def flow_status() -> tuple[Dict[str, Any], int]:
    """
    Get Flow functionality status
    """
    try:
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        base_bot = current_app.extensions.get("bot_core")
        
        status = {
            "enhanced_bot_available": enhanced_bot is not None,
            "base_bot_available": base_bot is not None,
            "flows_enabled": getattr(enhanced_bot, 'flow_enabled', False) if enhanced_bot else False,
            "enhanced_llm_enabled": getattr(enhanced_bot, 'enhanced_llm_enabled', False) if enhanced_bot else False,
        }
        
        return jsonify(status), 200
        
    except Exception as exc:
        log.exception("Flow status endpoint failed")
        return jsonify({"error": str(exc)}), 500


def _format_response(
    bot_resp: Union[BotResponse, EnhancedBotResponse], 
    response_format: str
) -> tuple[Dict[str, Any], int]:
    """Format response based on requested format"""
    
    if response_format == "legacy" or isinstance(bot_resp, BotResponse):
        # Legacy format
        if isinstance(bot_resp, EnhancedBotResponse):
            legacy_resp = bot_resp.to_legacy_bot_response()
        else:
            legacy_resp = bot_resp
            
        return jsonify({
            "response_type": legacy_resp.response_type.value,
            "content": legacy_resp.content,
            "functions_executed": legacy_resp.functions_executed,
            "timestamp": legacy_resp.timestamp,
        }), 200
    
    elif response_format == "dual" and isinstance(bot_resp, EnhancedBotResponse):
        # Dual messages format
        return jsonify(bot_resp.to_dual_messages()), 200
    
    elif response_format == "enhanced" and isinstance(bot_resp, EnhancedBotResponse):
        # Enhanced format with Flow info
        return jsonify({
            "response_type": bot_resp.response_type.value,
            "content": bot_resp.content,
            "functions_executed": bot_resp.functions_executed,
            "timestamp": bot_resp.timestamp,
            "requires_flow": bot_resp.requires_flow,
            "flow_payload": bot_resp.flow_payload.__dict__ if bot_resp.flow_payload else None,
        }), 200
    
    else:
        # Auto format - choose best format based on response type
        if isinstance(bot_resp, EnhancedBotResponse):
            if bot_resp.requires_flow:
                # Return dual messages for Flow-capable responses
                return jsonify(bot_resp.to_dual_messages()), 200
            else:
                # Return enhanced format for non-Flow responses
                return jsonify({
                    "response_type": bot_resp.response_type.value,
                    "content": bot_resp.content,
                    "functions_executed": bot_resp.functions_executed,
                    "timestamp": bot_resp.timestamp,
                    "requires_flow": False,
                }), 200
        else:
            # Legacy BotResponse
            return jsonify({
                "response_type": bot_resp.response_type.value,
                "content": bot_resp.content,
                "functions_executed": bot_resp.functions_executed,
                "timestamp": bot_resp.timestamp,
            }), 200