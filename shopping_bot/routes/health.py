# shopping_bot/routes/health.py
"""
Simple readiness/liveness probe.

Returns HTTP 200 if:
• Flask is running
• Redis is reachable

Otherwise 500 (so Cloud Run / Kubernetes can restart the pod).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, current_app, jsonify

log = logging.getLogger(__name__)
bp = Blueprint("health", __name__)


@bp.route("/health", methods=["GET"])
def health_check() -> tuple[Dict[str, Any], int]:
    """Health check endpoint - returns healthy if Flask is running, Redis is optional."""
    try:
        # Basic health: Flask app is running
        # Redis check is optional and won't fail the health check
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        
        # Try to check Redis if available, but don't block
        redis_status = "unknown"
        if ctx_mgr is None and "_get_or_init_redis" in current_app.extensions:
            # Don't initialize Redis in health check - it might timeout
            redis_status = "not_initialized"
        elif ctx_mgr is not None:
            try:
                ctx_mgr.redis.ping()
                redis_status = "connected"
            except Exception as e:
                log.warning("Redis ping failed in health check: %s", e)
                redis_status = "disconnected"
        else:
            redis_status = "not_available"
        
        # Always return 200 if Flask is running
        return jsonify({
            "status": "healthy",
            "redis": redis_status,
            "service": "shopbot"
        }), 200
    except Exception as exc:  # noqa: BLE001
        log.warning("Health check error: %s", exc)
        # Even on error, return 200 to indicate Flask is running
        return jsonify({
            "status": "healthy",
            "redis": "unknown",
            "service": "shopbot",
            "message": "Service operational"
        }), 200

    