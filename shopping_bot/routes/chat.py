# shopping_bot/routes/chat.py
"""
/chat endpoint  – main entry-point for user messages.

Expects JSON:
{
  "user_id": "123",
  "session_id": "abc",
  "message": "I'm looking for a gaming laptop"
}
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, current_app, jsonify, request

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
        # Resolve helpers from app context
        # --------------------------------------------------
        ctx_mgr = current_app.extensions["ctx_mgr"]  # RedisContextManager
        bot_core = current_app.extensions["bot_core"]  # ShoppingBotCore

        # --------------------------------------------------
        # Load → process → respond
        # --------------------------------------------------
        ctx = ctx_mgr.get_context(user_id, session_id)
        bot_resp = await bot_core.process_query(message, ctx)

        body = {
            "response_type": bot_resp.response_type.value,
            "content": bot_resp.content,
            "functions_executed": bot_resp.functions_executed,
            "timestamp": bot_resp.timestamp,
        }
        return jsonify(body), 200

    except Exception as exc:  # noqa: BLE001
        log.exception("chat endpoint failed")
        return jsonify({"error": str(exc)}), 500
