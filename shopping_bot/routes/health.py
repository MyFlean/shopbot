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


@bp.get("/health")
def health_check() -> tuple[Dict[str, Any], int]:
    """Health check endpoint for ALB routing (with /rs prefix from blueprint)."""
    try:
        ctx_mgr = current_app.extensions["ctx_mgr"]  # RedisContextManager
        ctx_mgr.redis.ping()
        return jsonify({"status": "healthy", "redis": "connected", "service": "shopbot"}), 200
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis ping failed: %s", exc)
        return jsonify({"status": "unhealthy", "redis": "disconnected", "service": "shopbot"}), 500

    