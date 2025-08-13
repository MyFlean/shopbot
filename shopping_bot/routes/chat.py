"""
Enhanced Chat Route with Background Processing Support and Product Recommendations Flow
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Union

from flask import Blueprint, current_app, jsonify, request

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)


@bp.post("/chat")
async def chat() -> tuple[Dict[str, Any], int]:
    """Enhanced chat endpoint with background processing option"""
    try:
        data: Dict[str, str] = request.get_json(force=True)

        # Validate basic schema
        missing = [k for k in ("user_id", "session_id", "message") if k not in data]
        if missing:
            return (
                {"error": f"Missing required fields: {', '.join(missing)}"},
                400,
            )

        user_id = data["user_id"]
        session_id = data["session_id"]
        message = data["message"]

        # Enhanced processing options
        enable_flows = data.get("enable_flows", True)
        response_format = data.get("response_format", "auto")
        background_processing = data.get("background_processing", False)

        # Resolve helpers from app context
        ctx_mgr = current_app.extensions["ctx_mgr"]
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        base_bot = current_app.extensions.get("bot_core")
        
        if not enhanced_bot and not base_bot:
            return jsonify({"error": "Bot core not initialized"}), 500

        # Background processing route
        if background_processing and enhanced_bot:
            background_processor = current_app.extensions.get("background_processor")
            if not background_processor:
                return jsonify({
                    "error": "Background processing not available",
                    "fallback": "Try without background_processing flag"
                }), 500
                
            # Start background processing
            try:
                processing_id = await background_processor.process_query_background(
                    query=message,
                    user_id=user_id,
                    session_id=session_id,
                    notification_callback=None  # No callback for now
                )
                
                return jsonify({
                    "processing_id": processing_id,
                    "status": "processing",
                    "message": "Your request is being processed. You'll be notified when ready.",
                    "estimated_time": "10-30 seconds"
                }), 202
                
            except Exception as exc:
                log.exception(f"Background processing failed: {exc}")
                return jsonify({
                    "error": "Background processing failed",
                    "details": str(exc),
                    "fallback": "Try without background_processing flag"
                }), 500

        # Standard synchronous processing
        ctx = ctx_mgr.get_context(user_id, session_id)
        
        if enhanced_bot and enable_flows:
            bot_resp = await enhanced_bot.process_query(message, ctx, enable_flows=True)
        elif enhanced_bot and not enable_flows:
            bot_resp = await enhanced_bot.process_query_legacy(message, ctx)
        else:
            bot_resp = await base_bot.process_query(message, ctx)

        return _format_response(bot_resp, response_format)

    except Exception as exc:
        log.exception("chat endpoint failed")
        return jsonify({"error": str(exc)}), 500


@bp.post("/chat/whatsapp")
async def chat_whatsapp() -> tuple[Union[Dict[str, Any], List[Dict[str, Any]]], int]:
    """WhatsApp-optimized endpoint with background processing and product recommendations flow"""
    try:
        data: Dict[str, str] = request.get_json(force=True)

        missing = [k for k in ("user_id", "message") if k not in data]
        if missing:
            return (
                {"error": f"Missing required fields: {', '.join(missing)}"},
                400,
            )

        user_id = data["user_id"]
        session_id = data.get("session_id", user_id)
        message = data["message"]

        ctx_mgr = current_app.extensions["ctx_mgr"]
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        
        if not enhanced_bot:
            return jsonify({"error": "Enhanced bot core required for WhatsApp endpoint"}), 500

        # Check if this query needs product recommendations flow
        if _needs_product_recommendations_flow(message):
            background_processor = current_app.extensions.get("background_processor")
            
            if background_processor:
                try:
                    # Phase 1: Check if we need questions first
                    questions_data = await background_processor.collect_questions_for_query(
                        query=message,
                        user_id=user_id,
                        session_id=session_id
                    )
                    
                    if questions_data:
                        # Return questions immediately with 200 status
                        return jsonify({
                            "type": "question",
                            "content": questions_data["content"],
                            "response_type": "question",
                            "requires_followup": True,
                            "message": "I need a bit more info to find the perfect products for you."
                        }), 200
                    
                    # No questions needed - proceed with background processing
                    processing_id = await background_processor.process_query_background(
                        query=message,
                        user_id=user_id,
                        session_id=session_id,
                        notification_callback=None
                    )
                    
                    # Return immediate "processing" response
                    return jsonify({
                        "type": "text",
                        "content": {
                            "message": "🔍 I'm finding the best products for you. This may take a moment...",
                            "processing_id": processing_id,
                            "response_type": "processing"
                        }
                    }), 200
                    
                except Exception as exc:
                    log.exception(f"Two-phase processing failed: {exc}")
                    # Fall back to synchronous processing

        # Standard processing for simple queries or fallback
        ctx = ctx_mgr.get_context(user_id, session_id)
        bot_resp = await enhanced_bot.process_query(message, ctx, enable_flows=True)
        return _format_whatsapp_response(bot_resp)

    except Exception as exc:
        log.exception("WhatsApp chat endpoint failed")
        return jsonify({"error": str(exc)}), 500


@bp.get("/chat/processing/<processing_id>/status")
async def get_processing_status(processing_id: str) -> tuple[Dict[str, Any], int]:
    """Get status of background processing"""
    try:
        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            return jsonify({"error": "Background processor not available"}), 500
            
        status = await background_processor.get_processing_status(processing_id)
        return jsonify(status), 200
        
    except Exception as exc:
        log.exception("Processing status check failed")
        return jsonify({"error": str(exc)}), 500


@bp.get("/chat/processing/<processing_id>/result")
async def get_processing_result(processing_id: str) -> tuple[Dict[str, Any], int]:
    """Get result of completed background processing"""
    try:
        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            return jsonify({"error": "Background processor not available"}), 500
            
        result = await background_processor.get_processing_result(processing_id)
        if not result:
            return jsonify({"error": "Processing result not found"}), 404
            
        return jsonify(result), 200
        
    except Exception as exc:
        log.exception("Processing result retrieval failed")
        return jsonify({"error": str(exc)}), 500


@bp.get("/chat/flows/status")
def flow_status() -> tuple[Dict[str, Any], int]:
    """Get Flow functionality status"""
    try:
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        base_bot = current_app.extensions.get("bot_core")
        background_processor = current_app.extensions.get("background_processor")
        
        status = {
            "enhanced_bot_available": enhanced_bot is not None,
            "base_bot_available": base_bot is not None,
            "background_processor_available": background_processor is not None,
            "flows_enabled": getattr(enhanced_bot, 'flow_enabled', False) if enhanced_bot else False,
            "enhanced_llm_enabled": getattr(enhanced_bot, 'enhanced_llm_enabled', False) if enhanced_bot else False,
        }
        
        return jsonify(status), 200
        
    except Exception as exc:
        log.exception("Flow status endpoint failed")
        return jsonify({"error": str(exc)}), 500


@bp.post("/chat/continue-processing")
async def continue_processing_after_questions() -> tuple[Dict[str, Any], int]:
    """Continue background processing after user answers questions"""
    try:
        data: Dict[str, str] = request.get_json(force=True)

        missing = [k for k in ("user_id", "message") if k not in data]
        if missing:
            return (
                {"error": f"Missing required fields: {', '.join(missing)}"},
                400,
            )

        user_id = data["user_id"]
        session_id = data.get("session_id", user_id)
        message = data["message"]  # This should be the user's answer

        ctx_mgr = current_app.extensions["ctx_mgr"]
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        background_processor = current_app.extensions.get("background_processor")
        
        if not enhanced_bot or not background_processor:
            return jsonify({"error": "Enhanced bot or background processor not available"}), 500

        # First, handle the user's answer
        ctx = ctx_mgr.get_context(user_id, session_id)
        
        # Check if we're in assessment mode
        if "assessment" not in ctx.session:
            return jsonify({"error": "No active assessment found"}), 400
        
        # Continue the question collection to see if more questions are needed
        questions_data = await background_processor.collect_questions_for_query(
            query=message,  # User's answer
            user_id=user_id,
            session_id=session_id
        )
        
        if questions_data:
            # Still need more questions
            return jsonify({
                "type": "question",
                "content": questions_data["content"],
                "response_type": "question",
                "requires_followup": True,
                "message": "Just one more thing..."
            }), 200
        
        # All questions answered - start background processing
        processing_id = await background_processor.process_query_background(
            query=ctx.session["assessment"]["original_query"],  # Use original query
            user_id=user_id,
            session_id=session_id,
            notification_callback=None
        )
        
        return jsonify({
            "type": "text",
            "content": {
                "message": "✅ Perfect! I'm now finding the best products for you...",
                "processing_id": processing_id,
                "response_type": "processing"
            }
        }), 200
        
    except Exception as exc:
        log.exception("Continue processing endpoint failed")
        return jsonify({"error": str(exc)}), 500


@bp.post("/webhook/processing-complete")
def handle_processing_complete_webhook():
    """Endpoint that frontend calls when background processing completes"""
    try:
        webhook_data = request.get_json(force=True)
        
        processing_id = webhook_data.get("processing_id")
        status = webhook_data.get("status")
        user_id = webhook_data.get("user_id")
        has_flow_data = webhook_data.get("has_flow_data", False)
        flow_data = webhook_data.get("flow_data", {})
        
        log.info(f"Processing complete webhook: {processing_id}, status: {status}, has_flow: {has_flow_data}")
        
        if status == "completed" and has_flow_data:
            # Here you would typically:
            # 1. Send WhatsApp message with flow button
            # 2. Or trigger frontend to show the flow button
            
            # For now, just log that flow button should be shown
            log.info(f"✅ Should show flow button for user {user_id} with processing_id {processing_id}")
            
            return jsonify({
                "success": True,
                "action": "show_flow_button",
                "flow_type": "product_recommendations",
                "processing_id": processing_id,
                "products_count": len(flow_data.get("products", [])),
                "header_text": flow_data.get("header_text", "Product Recommendations")
            }), 200
        
        return jsonify({"success": True}), 200
        
    except Exception as e:
        log.exception("Processing complete webhook failed")
        return jsonify({"error": str(e)}), 500


def _needs_product_recommendations_flow(message: str) -> bool:
    """Determine if query needs product recommendations flow"""
    
    product_indicators = [
        "recommend", "find", "best", "suggest", "show me", "need", 
        "looking for", "want to buy", "help me choose", "compare"
    ]
    
    product_categories = [
        "laptop", "phone", "shoes", "dress", "watch", "electronics", 
        "furniture", "rice", "apples", "food", "grocery", "product"
    ]
    
    message_lower = message.lower()
    
    has_product_intent = any(indicator in message_lower for indicator in product_indicators)
    has_product_category = any(category in message_lower for category in product_categories)
    
    # Also check message length (longer queries are likely more complex)
    is_complex_query = len(message.split()) > 5
    
    return has_product_intent and (has_product_category or is_complex_query)


def _format_whatsapp_response(bot_resp) -> tuple[List[Dict[str, Any]], int]:
    """Format response for WhatsApp"""
    
    # Check if it's an EnhancedBotResponse with flow
    if hasattr(bot_resp, 'requires_flow') and bot_resp.requires_flow:
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


def _format_response(bot_resp, response_format: str) -> tuple[Dict[str, Any], int]:
    """Format response based on requested format"""
    
    # For enhanced responses with flows
    if hasattr(bot_resp, 'requires_flow') and response_format == "dual":
        return jsonify(bot_resp.to_dual_messages()), 200
    
    # Standard response format
    return jsonify({
        "response_type": bot_resp.response_type.value,
        "content": bot_resp.content,
        "functions_executed": getattr(bot_resp, 'functions_executed', []),
        "timestamp": getattr(bot_resp, 'timestamp', None),
        "requires_flow": getattr(bot_resp, 'requires_flow', False),
    }), 200