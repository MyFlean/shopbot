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

from flask import Blueprint, current_app, jsonify, request

log = logging.getLogger(__name__)
bp = Blueprint("health", __name__)


@bp.get("/health")
def health_check() -> tuple[Dict[str, Any], int]:
    try:
        ctx_mgr = current_app.extensions["ctx_mgr"]  # RedisContextManager
        ctx_mgr.redis.ping()
        return jsonify({"status": "healthy", "redis": "connected"}), 200
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis ping failed: %s", exc)
        return jsonify({"status": "unhealthy", "redis": "disconnected"}), 500

@bp.get("/rs/health")
def rs_health_check() -> tuple[Dict[str, Any], int]:
    try:
        ctx_mgr = current_app.extensions["ctx_mgr"]  # RedisContextManager
        ctx_mgr.redis.ping()
        return jsonify({"status": "healthy", "service": "shopbot", "redis": "connected"}), 200
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis ping failed: %s", exc)
        return jsonify({"status": "unhealthy", "service": "shopbot", "redis": "disconnected"}), 500

@bp.post("/health_check")
def health_check_new() -> tuple[Dict[str, Any], int]:
    payload = request.get_json(silent=True) or {}
    # ctx_mgr = current_app.extensions["ctx_mgr"]  # RedisContextManager
    # ctx_mgr.redis.ping()
    return jsonify(payload), 200
    