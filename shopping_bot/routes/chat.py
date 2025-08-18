"""
Single Chat Endpoint (channel-agnostic) with Core-Owned Background Processing

Contract:
- POST /chat          → QUESTION | PROCESSING_STUB (202) | FINAL_ANSWER
- GET  /chat/processing/<id>/status
- GET  /chat/processing/<id>/result

Rules we enforce here:
- When the core defers (PROCESSING_STUB), we start background work and return a minimal 202 stub.
  (No user-facing text is returned from this endpoint in that case.)
- When the core returns QUESTION or FINAL_ANSWER:
    • If requires_flow==True → suppress text (defensive guard; should not happen if core uses stub path)
    • Else → return the normal canonical payload.
"""
from __future__ import annotations

import logging
from typing import Any, Dict
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


# ─────────────────────────────────────────────────────────────
# POST /chat  (only endpoint)
# ─────────────────────────────────────────────────────────────
@bp.post("/chat")
async def chat() -> Response:
    try:
        data: Dict[str, Any] = request.get_json(force=True)

        # Minimal schema (channel-agnostic)
        missing = [k for k in ("user_id", "message") if k not in data]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        user_id = str(data["user_id"])
        session_id = str(data.get("session_id", user_id))  # default to user_id
        message = str(data["message"])
        wa_id = data.get("wa_id")  # optional but preferred for WhatsApp

        # Resolve deps
        ctx_mgr = current_app.extensions["ctx_mgr"]
        background_processor = current_app.extensions.get("background_processor")
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        base_bot = current_app.extensions.get("bot_core")

        if not (enhanced_bot or base_bot):
            return jsonify({"error": "Bot core not initialized"}), 500

        # 1) Pull context
        ctx = ctx_mgr.get_context(user_id, session_id)

        # Persist wa_id in context so background jobs can read it later
        if wa_id:
            try:
                ctx.session["wa_id"] = str(wa_id)
                user_bucket = ctx.session.get("user") or {}
                user_bucket["wa_id"] = str(wa_id)
                ctx.session["user"] = user_bucket
                ctx_mgr.save_context(ctx)
            except Exception:
                log.warning("Failed to persist wa_id in context", exc_info=True)

        # 2) Ask the core (flows allowed; core decides PROCESSING_STUB vs direct answer)
        bot_resp = await (
            enhanced_bot.process_query(message, ctx, enable_flows=True)
            if enhanced_bot
            else base_bot.process_query(message, ctx)
        )

        # 3) If the core asks to defer, enqueue and return a canonical processing stub (no text)
        if bot_resp.response_type == ResponseType.PROCESSING_STUB:
            if not background_processor:
                return jsonify({
                    "error": "Background processor unavailable",
                    "fallback": "Please retry shortly."
                }), 503

            # Prefer original query if present (from assessment); else use current message
            original_q = ctx.session.get("assessment", {}).get("original_query", message)

            # Start background work. Support both signatures (with/without wa_id) for compatibility.
            try:
                processing_id = await background_processor.process_query_background(
                    query=original_q,
                    user_id=user_id,
                    session_id=session_id,
                    wa_id=wa_id,                      # newer BG supports this
                    notification_callback=None,
                )
            except TypeError:
                # Fallback for older BG that doesn't accept wa_id
                processing_id = await background_processor.process_query_background(
                    query=original_q,
                    user_id=user_id,
                    session_id=session_id,
                    notification_callback=None,
                )

            log.info(
                "CHAT defer → PROCESSING_STUB | processing_id=%s | user=%s | session=%s",
                processing_id, user_id, session_id,
            )

            # Minimal stub: FE shows loader and waits for FE webhook ping; nothing is sent to the user here.
            
            return jsonify({
                "response_type": "processing",
                "processing_id": processing_id,
                "status": "processing",
                "suppress_user_channel": True,   # hint to clients: do not synthesize user-visible text
            }), 202

        # 4) Otherwise: QUESTION or FINAL_ANSWER
        requires_flow = bool(getattr(bot_resp, "requires_flow", False))

        # Defensive: if a Flow was (incorrectly) produced on the sync path, suppress text.
        # (By policy, Flow paths should have returned a PROCESSING_STUB above.)
        if requires_flow:
            log.warning(
                "CHAT sync produced requires_flow=True without deferral; suppressing text. user=%s session=%s",
                user_id, session_id,
            )
            #Return a tiny ack so API clients know nothing textual should be sent.
            
            return jsonify({
                "response_type": "flow_only",
                "status": "ok",
                "suppress_user_channel": True,
            }), 200
           

        # Normal non-flow path → return canonical payload (this is what channels render as text/ask)
        resp_payload = {
            "response_type": bot_resp.response_type.value,   # "question" | "final_answer" | "error"
            "content": bot_resp.content,                     # { message, sections? … }
            "functions_executed": getattr(bot_resp, "functions_executed", []),
            "requires_flow": False,                          # explicitly false on text path
            "flow_payload": None,                            # no flow on text path
            "timestamp": getattr(bot_resp, "timestamp", None),
        }
        log.info(
            "CHAT sync → %s | user=%s | session=%s",
            bot_resp.response_type.value, user_id, session_id,
        )
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
