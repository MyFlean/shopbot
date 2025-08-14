"""
Single Chat Endpoint (channel-agnostic) with Core-Owned Background Processing

Contract:
- POST /chat          → QUESTION | PROCESSING_STUB (202) | FINAL_ANSWER
- GET  /chat/processing/<id>/status
- GET  /chat/processing/<id>/result

Notes:
- No channel flags, no heuristics. The core returns QUESTION / PROCESSING_STUB / FINAL_ANSWER.
- WhatsApp/web/mobile all consume the same canonical JSON and render on their side.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Union

from flask import Blueprint, current_app, jsonify, request
from ..enums import ResponseType

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)

# ─────────────────────────────────────────────────────────────
# POST /chat  (only endpoint)
# ─────────────────────────────────────────────────────────────
@bp.post("/chat")
async def chat() -> tuple[Dict[str, Any], int]:
    try:
        data: Dict[str, Any] = request.get_json(force=True)

        # Minimal schema (channel-agnostic)
        missing = [k for k in ("user_id", "message") if k not in data]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        user_id = str(data["user_id"])
        session_id = str(data.get("session_id", user_id))  # default to user_id
        message = str(data["message"])

        # Resolve deps
        ctx_mgr = current_app.extensions["ctx_mgr"]
        background_processor = current_app.extensions.get("background_processor")
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        base_bot = current_app.extensions.get("bot_core")

        if not (enhanced_bot or base_bot):
            return jsonify({"error": "Bot core not initialized"}), 500

        # 1) Pull context
        ctx = ctx_mgr.get_context(user_id, session_id)

        # 2) Ask the core (flows always allowed; core decides whether to return Flow later)
        bot_resp = await (
            enhanced_bot.process_query(message, ctx, enable_flows=True)
            if enhanced_bot
            else base_bot.process_query(message, ctx)
        )

        # 3) If core asks to defer, enqueue and return a canonical processing stub
        if bot_resp.response_type == ResponseType.PROCESSING_STUB:
            if not background_processor:
                return jsonify({
                    "error": "Background processor unavailable",
                    "fallback": "Please retry shortly."
                }), 503

            processing_id = await background_processor.process_query_background(
                query=ctx.session.get("assessment", {}).get("original_query", message),
                user_id=user_id,
                session_id=session_id,
                notification_callback=None,
            )

            # Canonical stub (clients render however they want)
            return jsonify({
                "response_type": "processing",
                "message": bot_resp.content.get("message", "Processing your request…"),
                "processing_id": processing_id,
                "status": "processing",
            }), 202

        # 4) Otherwise just return QUESTION or FINAL_ANSWER in canonical shape
        return jsonify({
            "response_type": bot_resp.response_type.value,   # "question" | "final_answer"
            "content": bot_resp.content,                     # { message, sections? … }
            "functions_executed": getattr(bot_resp, "functions_executed", []),
            "requires_flow": getattr(bot_resp, "requires_flow", False),
            "flow_payload": getattr(bot_resp, "flow_payload", None),  # if EnhancedBotResponse
            "timestamp": getattr(bot_resp, "timestamp", None),
        }), 200

    except Exception as exc:  # noqa: BLE001
        log.exception("chat endpoint failed")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Background processing polling (unchanged)
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/processing/<processing_id>/status")
async def get_processing_status(processing_id: str) -> tuple[Dict[str, Any], int]:
    try:
        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            return jsonify({"error": "Background processor not available"}), 500
        status = await background_processor.get_processing_status(processing_id)
        return jsonify(status), 200
    except Exception as exc:  # noqa: BLE001
        log.exception("Processing status check failed")
        return jsonify({"error": str(exc)}), 500


@bp.get("/chat/processing/<processing_id>/result")
async def get_processing_result(processing_id: str) -> tuple[Dict[str, Any], int]:
    try:
        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            return jsonify({"error": "Background processor not available"}), 500
        result = await background_processor.get_processing_result(processing_id)
        if not result:
            return jsonify({"error": "Processing result not found"}), 404
        return jsonify(result), 200
    except Exception as exc:  # noqa: BLE001
        log.exception("Processing result retrieval failed")
        return jsonify({"error": str(exc)}), 500
