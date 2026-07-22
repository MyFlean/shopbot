import json
from functools import wraps
from typing import Callable

from flask import current_app, g, jsonify, request


def _get_redis_client():
    """Return Redis client, including lazy-init path used in Lambda."""
    try:
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        if ctx_mgr is None and "_get_or_init_redis" in current_app.extensions:
            ctx_mgr = current_app.extensions["_get_or_init_redis"]()
        return getattr(ctx_mgr, "redis", None) if ctx_mgr else None
    except Exception:
        return None


def app_session_required(func: Callable) -> Callable:
    """
    Require valid app session token and attach identity to flask.g.
    Mirrors ecom/user-service session pattern.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "message": "Missing or invalid session token"}), 401

        session_token = auth_header.split(" ", 1)[1].strip()
        if not session_token:
            return jsonify({"success": False, "message": "Missing or invalid session token"}), 401

        redis_client = _get_redis_client()
        if not redis_client:
            return jsonify({"success": False, "message": "Session service unavailable"}), 401

        raw = redis_client.get(f"session:{session_token}")
        if not raw:
            return jsonify({"success": False, "message": "Invalid or expired session"}), 401

        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return jsonify({"success": False, "message": "Corrupted session data"}), 401

        if not isinstance(data, dict) or data.get("status") != "ACTIVE":
            return jsonify({"success": False, "message": "Session not active"}), 401

        g.app_user_id = data.get("user_id")
        g.app_device_id = data.get("device_id")
        g.app_session_token = session_token
        return func(*args, **kwargs)

    return wrapper
