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


@bp.route("/redis-health", methods=["GET"])
def redis_health_check() -> tuple[Dict[str, Any], int]:
    """
    Redis-specific health check endpoint.
    
    Returns detailed Redis connection status and health information.
    Returns 200 if Redis is healthy, 503 if Redis is unavailable.
    """
    import os
    import time
    
    start_time = time.time()
    health_data = {
        "status": "unhealthy",
        "service": "shopbot",
        "redis": {
            "connected": False,
            "host": os.getenv("REDIS_HOST", "not_set"),
            "port": os.getenv("REDIS_PORT", "not_set"),
            "db": os.getenv("REDIS_DB", "not_set"),
            "password_set": bool(os.getenv("REDIS_PASSWORD")),
            "ping_success": False,
            "connection_healthy": False,
            "memory_info": {},
            "error": None,
            "response_time_ms": 0
        },
        "timestamp": time.time()
    }
    
    try:
        # Try to get or initialize Redis context manager
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        
        if ctx_mgr is None:
            # Try to initialize Redis if not already initialized
            if "_get_or_init_redis" in current_app.extensions:
                try:
                    log.info("REDIS_HEALTH | Initializing Redis for health check")
                    ctx_mgr = current_app.extensions["_get_or_init_redis"]()
                except Exception as init_error:
                    health_data["redis"]["error"] = f"Failed to initialize Redis: {str(init_error)}"
                    health_data["status"] = "unhealthy"
                    log.error(f"REDIS_HEALTH | Initialization failed: {init_error}")
                    return jsonify(health_data), 503
            else:
                health_data["redis"]["error"] = "Redis context manager not available"
                health_data["status"] = "unhealthy"
                log.error("REDIS_HEALTH | Redis context manager not available")
                return jsonify(health_data), 503
        
        # Perform comprehensive health check
        try:
            health_check_result = ctx_mgr.health_check()
            response_time = (time.time() - start_time) * 1000
            
            health_data["redis"]["ping_success"] = health_check_result.get("ping_success", False)
            health_data["redis"]["connection_healthy"] = health_check_result.get("connection_healthy", False)
            health_data["redis"]["memory_info"] = health_check_result.get("memory_info", {})
            health_data["redis"]["response_time_ms"] = round(response_time, 2)
            health_data["redis"]["connected"] = health_data["redis"]["ping_success"]
            
            if health_data["redis"]["connection_healthy"]:
                health_data["status"] = "healthy"
                log.info(f"REDIS_HEALTH | Healthy | response_time={response_time:.2f}ms")
                return jsonify(health_data), 200
            else:
                error_msg = health_check_result.get("error", "Health check failed")
                health_data["redis"]["error"] = error_msg
                health_data["status"] = "unhealthy"
                log.warning(f"REDIS_HEALTH | Unhealthy | error={error_msg}")
                return jsonify(health_data), 503
                
        except Exception as health_error:
            response_time = (time.time() - start_time) * 1000
            health_data["redis"]["error"] = str(health_error)
            health_data["redis"]["response_time_ms"] = round(response_time, 2)
            health_data["status"] = "unhealthy"
            log.error(f"REDIS_HEALTH | Error during health check: {health_error}", exc_info=True)
            return jsonify(health_data), 503
            
    except Exception as exc:
        response_time = (time.time() - start_time) * 1000
        health_data["redis"]["error"] = str(exc)
        health_data["redis"]["response_time_ms"] = round(response_time, 2)
        health_data["status"] = "unhealthy"
        log.error(f"REDIS_HEALTH | Unexpected error: {exc}", exc_info=True)
        return jsonify(health_data), 503

    