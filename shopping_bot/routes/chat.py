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
from typing import Any
from dataclasses import asdict, is_dataclass
from enum import Enum

from flask import Blueprint, current_app, jsonify, request, Response
from ..enums import ResponseType

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)

# ─────────────────────────────────────────────────────────────
# Utilities: make any object JSON-safe (dataclasses, Enums, etc.)
# ─────────────────────────────────────────────────────────────
def _to_json_safe(obj: Any) -> Any:
    # Enum → its value
    if isinstance(obj, Enum):
        return obj.value
    # Dataclass → dict, then recurse
    if is_dataclass(obj):
        return _to_json_safe(asdict(obj))
    # Dict → recurse keys/values
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    # List/tuple/set → recurse
    if isinstance(obj, (list, tuple, set)):
        return [_to_json_safe(v) for v in obj]
    # datetime-like → isoformat if available
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            pass
    # Anything else is returned as-is (str, int, float, bool, None)
    return obj


# ─────────────────────────────────────────────────────────────
# POST /chat  (only endpoint)
# ─────────────────────────────────────────────────────────────
@bp.post("/chat")
async def chat() -> Response:
    try:
        data: dict[str, Any] = request.get_json(force=True)

        # Minimal schema (channel-agnostic)
        missing = [k for k in ("user_id", "message") if k not in data]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        user_id = str(data["user_id"])
        session_id = str(data.get("session_id", user_id))  # default to user_id
        message = str(data["message"])
        wa_id = data["wa_id"]
        

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
            stub_message = (bot_resp.content or {}).get("message", "Processing your request…")
            log.info(
                "CHAT defer → PROCESSING_STUB | processing_id=%s | user=%s | session=%s",
                processing_id, user_id, session_id,
            )
            return jsonify({
                "response_type": "processing",
                "message": stub_message,
                "processing_id": processing_id,
                "status": "processing",
            }), 202

        # 4) Otherwise just return QUESTION or FINAL_ANSWER in canonical shape
        resp_payload = {
            "response_type": bot_resp.response_type.value,   # "question" | "final_answer"
            "content": bot_resp.content,                     # { message, sections? … }
            "functions_executed": getattr(bot_resp, "functions_executed", []),
            "requires_flow": getattr(bot_resp, "requires_flow", False),
            "flow_payload": getattr(bot_resp, "flow_payload", None),  # may be a dataclass with Enums
            "timestamp": getattr(bot_resp, "timestamp", None),
        }
        log.info(
            "CHAT sync → %s | user=%s | session=%s",
            bot_resp.response_type.value, user_id, session_id,
        )

        # Ensure everything is JSON serializable (handles FlowType/FlowPayload/etc.)
        return jsonify(_to_json_safe(resp_payload)), 200

    except Exception as exc:  # noqa: BLE001
        log.exception("chat endpoint failed")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Background processing polling (unchanged)
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/processing/<processing_id>/status")
async def get_processing_status(processing_id: str) -> Response:
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
async def get_processing_result(processing_id: str) -> Response:
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
