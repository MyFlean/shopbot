"""
Fixed Chat Endpoint - ADDRESSES CRITICAL ISSUES
"""
from __future__ import annotations

import logging
import asyncio
from typing import Any, Dict
from dataclasses import asdict, is_dataclass
from enum import Enum

from flask import Blueprint, current_app, jsonify, request, Response
from shopping_bot.enums import ResponseType
from shopping_bot.models import UserContext

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)

# ─────────────────────────────────────────────────────────────
# Utilities: make any object JSON-safe (dataclasses, Enums, etc.)
# ─────────────────────────────────────────────────────────────
def _to_json_safe(obj: Any) -> Any:
    """Convert objects to JSON-safe format with error handling."""
    try:
        if isinstance(obj, Enum):
            return obj.value
        if is_dataclass(obj):
            return _to_json_safe(asdict(obj))
        if isinstance(obj, dict):
            return {k: _to_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_to_json_safe(v) for v in obj]
        if hasattr(obj, "isoformat"):
            try:
                return obj.isoformat()
            except Exception:
                pass
        return obj
    except Exception as e:
        log.warning(f"JSON_SAFE_CONVERSION_ERROR | obj_type={type(obj)} | error={e}")
        return str(obj)


# ─────────────────────────────────────────────────────────────
# POST /chat - FIXED VERSION with comprehensive error handling
# ─────────────────────────────────────────────────────────────

@bp.get("/chat/test")
def chat_test():
    return {"test": "working"}


    
@bp.post("/chat")
async def chat() -> Response:
    """
    Fixed chat endpoint with proper async handling and comprehensive logging.
    
    FIXES:
    - Proper await statements for all async operations
    - Immediate return for background processing (no blocking)
    - Guard against duplicate processing
    - Enhanced error handling and logging
    - CLI text fallback support
    """
    request_start_time = asyncio.get_event_loop().time()
    
    try:
        # FIX: Proper request data extraction with validation
        try:
            data: Dict[str, Any] = request.get_json(force=True)
            if not data:
                log.warning("CHAT_EMPTY_REQUEST | no JSON data received")
                return jsonify({"error": "No JSON data provided"}), 400
        except Exception as e:
            log.error(f"CHAT_JSON_PARSE_ERROR | error={e}")
            return jsonify({"error": "Invalid JSON format"}), 400

        # Validate required fields
        missing = [k for k in ("user_id", "message") if k not in data]
        if missing:
            log.warning(f"CHAT_MISSING_FIELDS | missing={missing}")
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        user_id = str(data["user_id"])
        session_id = str(data.get("session_id", user_id))
        message = str(data["message"]).strip()
        wa_id = data.get("wa_id")
        channel = str(data.get("channel", "api")).lower()  # For CLI support

        if not message:
            log.warning(f"CHAT_EMPTY_MESSAGE | user={user_id}")
            return jsonify({"error": "Message cannot be empty"}), 400

        log.info(f"CHAT_REQUEST | user={user_id} | session={session_id} | channel={channel} | message='{message[:50]}...' | wa_id={wa_id}")

        # FIX: Resolve dependencies with proper error handling
        try:
            ctx_mgr = current_app.extensions["ctx_mgr"]
            background_processor = current_app.extensions.get("background_processor")
            enhanced_bot = current_app.extensions.get("enhanced_bot_core")
            base_bot = current_app.extensions.get("bot_core")
        except KeyError as e:
            log.error(f"CHAT_MISSING_EXTENSION | extension={e}")
            return jsonify({"error": f"Required service not available: {e}"}), 500

        if not (enhanced_bot or base_bot):
            log.error("CHAT_NO_BOT_CORE | neither enhanced nor base bot available")
            return jsonify({"error": "Bot core not initialized"}), 500

        # FIX: Get context with error handling
        try:
            ctx = ctx_mgr.get_context(user_id, session_id)
            log.info(f"CONTEXT_LOADED | user={user_id} | session={session_id} | has_assessment={bool(ctx.session.get('assessment'))}")
        except Exception as e:
            log.error(f"CONTEXT_LOAD_ERROR | user={user_id} | session={session_id} | error={e}")
            return jsonify({"error": "Failed to load user context"}), 500

        # FIX: Guard against duplicate processing
        if "assessment" in ctx.session:
            current_phase = ctx.session["assessment"].get("phase")
            if current_phase == "processing":
                log.warning(f"DUPLICATE_PROCESSING_BLOCKED | user={user_id} | phase={current_phase}")
                return jsonify({
                    "response_type": "processing",
                    "status": "already_processing", 
                    "message": "Still working on your previous request. Please wait...",
                    "suppress_user_channel": True,
                }), 202

        # FIX: Persist wa_id in context with proper error handling
        if wa_id:
            try:
                ctx.session["wa_id"] = str(wa_id)
                user_bucket = ctx.session.get("user", {})
                user_bucket["wa_id"] = str(wa_id)
                ctx.session["user"] = user_bucket
                ctx_mgr.save_context(ctx)
                log.info(f"WA_ID_PERSISTED | user={user_id} | wa_id={wa_id}")
            except Exception as e:
                log.warning(f"WA_ID_PERSIST_FAILED | user={user_id} | wa_id={wa_id} | error={e}")

        # FIX: Ask the core with proper await and error handling
        try:
            log.info(f"BOT_PROCESSING_START | user={user_id} | using_enhanced={bool(enhanced_bot)}")
            
            if enhanced_bot:
                bot_resp = await enhanced_bot.process_query(message, ctx, enable_flows=True)
            else:
                bot_resp = await base_bot.process_query(message, ctx)
                
            log.info(f"BOT_PROCESSING_COMPLETE | user={user_id} | response_type={bot_resp.response_type.value}")
            
        except Exception as e:
            log.error(f"BOT_PROCESSING_ERROR | user={user_id} | error={e}", exc_info=True)
            return jsonify({
                "error": "Bot processing failed",
                "details": str(e),
                "response_type": "error"
            }), 500

        # FIX: Handle PROCESSING_STUB - start background work and return immediately
        if bot_resp.response_type == ResponseType.PROCESSING_STUB:
            if not background_processor:
                log.error(f"BACKGROUND_UNAVAILABLE | user={user_id}")
                return jsonify({
                    "error": "Background processor unavailable",
                    "fallback": "Please retry shortly.",
                    "response_type": "error"
                }), 503

            try:
                # Get original query from assessment if available
                original_q = ctx.session.get("assessment", {}).get("original_query", message)
                
                log.info(f"BACKGROUND_START | user={user_id} | original_query='{original_q[:50]}...'")
                
                # FIX: Start background work with proper signature
                processing_id = await background_processor.process_query_background(
                    query=original_q,
                    user_id=user_id,
                    session_id=session_id,
                    wa_id=wa_id,  # Now supported in fixed background processor
                    notification_callback=None,
                )
                
                elapsed_time = asyncio.get_event_loop().time() - request_start_time
                log.info(f"BACKGROUND_SPAWNED | user={user_id} | processing_id={processing_id} | elapsed_time={elapsed_time:.3f}s")

                # FIX: Return minimal stub immediately (no text to user)
                return jsonify({
                    "response_type": "processing",
                    "processing_id": processing_id,
                    "status": "processing",
                    "suppress_user_channel": True,   # Hint: don't synthesize user-visible text
                    "elapsed_time": f"{elapsed_time:.3f}s"
                }), 202

            except Exception as e:
                log.error(f"BACKGROUND_SPAWN_ERROR | user={user_id} | error={e}", exc_info=True)
                return jsonify({
                    "error": "Failed to start background processing",
                    "details": str(e),
                    "response_type": "error"
                }), 500

        # FIX: Handle responses that require Flow but somehow didn't defer
        requires_flow = bool(getattr(bot_resp, "requires_flow", False))
        if requires_flow:
            log.warning(f"UNEXPECTED_FLOW_SYNC | user={user_id} | suppressing text")
            
            # FIX: For CLI/test channels, provide text fallback
            if channel in ["cli", "test"]:
                return await _handle_cli_fallback(bot_resp, ctx, user_id, channel)
            
            # For other channels, suppress
            return jsonify({
                "response_type": "flow_only",
                "status": "ok",
                "suppress_user_channel": True,
                "message": "Content available via interactive elements"
            }), 200

        # FIX: Normal sync path - return canonical payload
        try:
            resp_payload = {
                "response_type": bot_resp.response_type.value,
                "content": bot_resp.content,
                "functions_executed": getattr(bot_resp, "functions_executed", []),
                "requires_flow": False,
                "flow_payload": None,
                "timestamp": getattr(bot_resp, "timestamp", None),
            }
            
            elapsed_time = asyncio.get_event_loop().time() - request_start_time
            resp_payload["elapsed_time"] = f"{elapsed_time:.3f}s"
            
            log.info(f"CHAT_SYNC_RESPONSE | user={user_id} | response_type={bot_resp.response_type.value} | elapsed_time={elapsed_time:.3f}s")
            
            return jsonify(_to_json_safe(resp_payload)), 200
            
        except Exception as e:
            log.error(f"RESPONSE_SERIALIZATION_ERROR | user={user_id} | error={e}", exc_info=True)
            return jsonify({
                "error": "Response serialization failed",
                "response_type": "error",
                "details": str(e)
            }), 500

    except Exception as exc:
        elapsed_time = asyncio.get_event_loop().time() - request_start_time
        log.error(f"CHAT_ENDPOINT_ERROR | elapsed_time={elapsed_time:.3f}s | error={exc}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "details": str(exc),
            "response_type": "error"
        }), 500


# ─────────────────────────────────────────────────────────────
# FIX: CLI text fallback for flow_only responses 
# ─────────────────────────────────────────────────────────────
async def _handle_cli_fallback(bot_resp, ctx: UserContext, user_id: str, channel: str) -> Response:
    """
    FIX: Provide text fallback for CLI/test channels when Flow is produced.
    This addresses issue #9 from the diagnostic.
    """
    try:
        log.info(f"CLI_FALLBACK | user={user_id} | channel={channel}")
        
        # Try to get fetched data for text generation
        fetched_data = ctx.fetched_data or {}
        
        # Check if we have product search results
        products = []
        for key in ["search_products", "SEARCH_PRODUCTS"]:
            if key in fetched_data:
                data = fetched_data[key].get("data", {})
                if isinstance(data, dict) and "products" in data:
                    products = data["products"][:5]  # Top 5 for CLI
                    break
        
        if products:
            # Generate text summary of products
            lines = ["Here are the top options I found:"]
            for i, product in enumerate(products, 1):
                title = product.get("title", "Product")
                price = product.get("price", "Price on request")
                lines.append(f"{i}. {title} - {price}")
            
            fallback_text = "\n".join(lines)
            log.info(f"CLI_PRODUCTS_FALLBACK | user={user_id} | products_count={len(products)}")
            
        else:
            # Check if there's a current question to ask
            assessment = ctx.session.get("assessment", {})
            currently_asking = assessment.get("currently_asking")
            
            if currently_asking:
                contextual_questions = ctx.session.get("contextual_questions", {})
                question_text = contextual_questions.get(currently_asking, {}).get("message", "")
                if question_text:
                    fallback_text = question_text
                    log.info(f"CLI_QUESTION_FALLBACK | user={user_id} | asking={currently_asking}")
                else:
                    fallback_text = f"I need to know: {currently_asking.replace('ASK_', '').replace('_', ' ').lower()}"
            else:
                # Generic fallback
                content = getattr(bot_resp, "content", {})
                message = content.get("message", "") if isinstance(content, dict) else str(content)
                fallback_text = message or "I can help you find products. Please provide more details about what you're looking for."
        
        cli_response = {
            "response_type": "final_answer",
            "content": {"message": fallback_text},
            "functions_executed": getattr(bot_resp, "functions_executed", []),
            "requires_flow": False,
            "flow_payload": None,
            "cli_fallback": True,
            "original_response_type": getattr(bot_resp, "response_type", ResponseType.FINAL_ANSWER).value
        }
        
        log.info(f"CLI_FALLBACK_SUCCESS | user={user_id} | text_length={len(fallback_text)}")
        return jsonify(_to_json_safe(cli_response)), 200
        
    except Exception as e:
        log.error(f"CLI_FALLBACK_ERROR | user={user_id} | error={e}", exc_info=True)
        return jsonify({
            "response_type": "final_answer", 
            "content": {"message": "I can help you with shopping queries. Please provide more details."},
            "cli_fallback": True,
            "fallback_error": str(e)
        }), 200


# ─────────────────────────────────────────────────────────────
# Background processing polling endpoints (enhanced with logging)
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/processing/<processing_id>/status")
async def get_processing_status(processing_id: str) -> Response:
    """Get processing status with enhanced logging and error handling."""
    try:
        log.info(f"STATUS_LOOKUP | processing_id={processing_id}")
        
        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            log.error(f"STATUS_NO_PROCESSOR | processing_id={processing_id}")
            return jsonify({"error": "Background processor not available"}), 500
        
        # FIX: Proper await for async method
        status = await background_processor.get_processing_status(processing_id)
        
        if not status or status.get("status") == "not_found":
            log.warning(f"STATUS_NOT_FOUND | processing_id={processing_id}")
            return jsonify({"error": "Processing ID not found"}), 404
        
        log.info(f"STATUS_FOUND | processing_id={processing_id} | status={status.get('status')}")
        return jsonify(_to_json_safe(status)), 200
        
    except Exception as exc:
        log.error(f"STATUS_LOOKUP_ERROR | processing_id={processing_id} | error={exc}", exc_info=True)
        return jsonify({"error": str(exc)}), 500


@bp.get("/chat/processing/<processing_id>/result")
async def get_processing_result(processing_id: str) -> Response:
    """Get processing result with enhanced logging and error handling."""
    try:
        log.info(f"RESULT_LOOKUP | processing_id={processing_id}")
        
        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            log.error(f"RESULT_NO_PROCESSOR | processing_id={processing_id}")
            return jsonify({"error": "Background processor not available"}), 500
        
        # FIX: Proper await for async method
        result = await background_processor.get_processing_result(processing_id)
        
        if not result:
            log.warning(f"RESULT_NOT_FOUND | processing_id={processing_id}")
            return jsonify({"error": "Processing result not found"}), 404
        
        # Log result summary
        flow_data = result.get("flow_data", {})
        products_count = len(flow_data.get("products", [])) if flow_data else 0
        text_length = len(result.get("text_content", ""))
        
        log.info(f"RESULT_FOUND | processing_id={processing_id} | products_count={products_count} | text_length={text_length}")
        
        return jsonify(_to_json_safe(result)), 200
        
    except Exception as exc:
        log.error(f"RESULT_LOOKUP_ERROR | processing_id={processing_id} | error={exc}", exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Health and debug endpoints
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/health")
def chat_health() -> Response:
    """Health check for chat endpoint."""
    try:
        # Check if core services are available
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        background_processor = current_app.extensions.get("background_processor") 
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        base_bot = current_app.extensions.get("bot_core")
        
        health_status = {
            "status": "healthy",
            "services": {
                "ctx_mgr": bool(ctx_mgr),
                "background_processor": bool(background_processor),
                "enhanced_bot": bool(enhanced_bot),
                "base_bot": bool(base_bot)
            }
        }
        
        # Overall health check
        if not (enhanced_bot or base_bot) or not ctx_mgr:
            health_status["status"] = "degraded"
        
        return jsonify(health_status), 200
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500


@bp.get("/chat/debug/<user_id>")
def debug_user_context(user_id: str) -> Response:
    """Debug endpoint to inspect user context (development only)."""
    try:
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        if not ctx_mgr:
            return jsonify({"error": "Context manager not available"}), 500
        
        # Get context for default session
        ctx = ctx_mgr.get_context(user_id, user_id)
        
        debug_info = {
            "user_id": user_id,
            "session_id": ctx.session_id,
            "session_keys": list(ctx.session.keys()),
            "permanent_keys": list(ctx.permanent.keys()),
            "fetched_data_keys": list(ctx.fetched_data.keys()),
            "has_assessment": bool(ctx.session.get("assessment")),
            "assessment_phase": ctx.session.get("assessment", {}).get("phase"),
            "needs_background": ctx.session.get("needs_background"),
            "intent_l3": ctx.session.get("intent_l3")
        }
        
        return jsonify(debug_info), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500