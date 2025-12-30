# shopping_bot/routes/reset.py
"""
/reset endpoint – clears a conversation session so you can
start fresh from Postman without restarting the backend.

POST body:
{
  "session_id": "abc123"
}
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, current_app, jsonify, request

log = logging.getLogger(__name__)
bp = Blueprint("reset", __name__)


@bp.route("/reset", methods=["POST"])
def reset_session() -> tuple[Dict[str, Any], int]:
    try:
        data: Dict[str, str] = request.get_json(force=True)  # type: ignore[assignment]
        session_id = data.get("session_id")
        if not session_id:
            return jsonify({"error": "Missing session_id"}), 400

        ctx_mgr = current_app.extensions["ctx_mgr"]
        ctx_mgr.delete_session(session_id)

        return jsonify({"message": "Session reset successfully"}), 200
    except Exception as exc:  # noqa: BLE001
        log.exception("reset endpoint failed")
        return jsonify({"error": str(exc)}), 500
