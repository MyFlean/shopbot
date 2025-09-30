"""
Simplified Shopping Bot Application Factory
==========================================

Uses only the new architecture:
- bot_core.py (with 4-intent classification)
- llm_service.py (updated) 
- ux_response_generator.py (new)
- Redis context manager
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from flask import Flask

from .bot_core import ShoppingBotCore
from .config import get_config
from .redis_manager import RedisContextManager

log = logging.getLogger(__name__)
Cfg = get_config()


def create_app() -> Flask:
    """
    Simplified app factory using only the new architecture components.
    
    SIMPLIFIED INITIALIZATION ORDER:
    1. Redis connection & health check
    2. Bot core (with integrated 4-intent classification and UX generation)
    3. Register routes
    4. Health checks and monitoring
    """
    
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')
    
    # ────────────────────────────────────────────────────────
    # STEP 1: Initialize Redis
    # ────────────────────────────────────────────────────────
    try:
        log.info("INIT_REDIS | starting Redis connection")
        ctx_mgr = RedisContextManager()
        
        # Test Redis connection
        health = ctx_mgr.health_check()
        if not health.get("connection_healthy"):
            log.error(f"INIT_REDIS_FAILED | health={health}")
            raise RuntimeError(f"Redis connection failed: {health.get('error')}")
            
        log.info(f"INIT_REDIS_SUCCESS | memory_usage={health.get('memory_info', {}).get('used_memory_human', 'unknown')}")
        app.extensions["ctx_mgr"] = ctx_mgr
        
    except Exception as e:
        log.error(f"INIT_REDIS_ERROR | error={e}", exc_info=True)
        raise RuntimeError(f"Failed to initialize Redis: {e}")

    # ────────────────────────────────────────────────────────  
    # STEP 2: Initialize Bot Core (includes 4-intent + UX generation)
    # ────────────────────────────────────────────────────────
    try:
        log.info("INIT_BOT_CORE | initializing with 4-intent classification and UX generation")
        
        bot_core = ShoppingBotCore(ctx_mgr)
        app.extensions["bot_core"] = bot_core
        log.info("INIT_BOT_CORE_SUCCESS | 4-intent classification enabled | UX generation enabled")
        
    except Exception as e:
        log.error(f"INIT_BOT_CORE_ERROR | error={e}", exc_info=True)
        raise RuntimeError(f"Failed to initialize bot core: {e}")

    # ────────────────────────────────────────────────────────
    # STEP 3: Register Routes
    # ────────────────────────────────────────────────────────
    try:
        log.info("REGISTER_ROUTES | registering simplified routes")
        
        # Import and register the simplified chat routes
        from .routes.chat import bp as chat_bp
        app.register_blueprint(chat_bp, url_prefix='/rs')
        
        # Import other essential routes
        try:
            from .routes.health import bp as health_bp
            app.register_blueprint(health_bp)
        except ImportError:
            log.info("REGISTER_ROUTES | health routes not found, using built-in health check")

        # Register onboarding/meta flow routes
        try:
            from .routes.onboarding_flow import bp as flow_bp
            app.register_blueprint(flow_bp)
            log.info("REGISTER_ROUTES_SUCCESS | onboarding/meta flow routes registered")
        except Exception as e:
            log.error(f"REGISTER_ROUTES_ERROR | onboarding/meta flow routes failed: {e}")
        
        log.info("REGISTER_ROUTES_SUCCESS | simplified routes registered")
        
    except Exception as e:
        log.error(f"REGISTER_ROUTES_ERROR | error={e}", exc_info=True)
        raise RuntimeError(f"Failed to register routes: {e}")

    # ────────────────────────────────────────────────────────
    # STEP 4: Built-in Health Check (avoid conflicts with routes)
    # ────────────────────────────────────────────────────────
    @app.get("/__system_health")
    def system_health_check():
        """System health check - different endpoint to avoid conflicts."""
        try:
            health_status = {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "architecture": "simplified",
                "components": {}
            }
            
            # Redis health
            redis_health = ctx_mgr.health_check()
            health_status["components"]["redis"] = {
                "healthy": redis_health.get("connection_healthy", False),
                "ping": redis_health.get("ping_success", False),
                "memory": redis_health.get("memory_info", {})
            }
            
            # Bot core health
            bot_core = app.extensions.get("bot_core")
            health_status["components"]["bot_core"] = {
                "available": bool(bot_core),
                "features": {
                    "4_intent_classification": True,
                    "ux_generation": True,
                    "product_search": True
                }
            }
            
            # Overall health assessment
            redis_ok = health_status["components"]["redis"]["healthy"]
            bot_ok = health_status["components"]["bot_core"]["available"]
            
            if not (redis_ok and bot_ok):
                health_status["status"] = "degraded"
                health_status["issues"] = []
                if not redis_ok:
                    health_status["issues"].append("Redis connection unhealthy")
                if not bot_ok:
                    health_status["issues"].append("Bot core unavailable")
                
            return health_status, 200 if health_status["status"] == "healthy" else 503
            
        except Exception as e:
            return {
                "status": "unhealthy", 
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }, 500

    @app.get("/__diagnostics/<user_id>")
    def user_diagnostics(user_id: str):
        """Get user diagnostics for debugging."""
        try:
            ctx = ctx_mgr.get_context(user_id, user_id)
            
            return {
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
                "context_summary": {
                    "session_size": len(str(ctx.session)),
                    "permanent_size": len(str(ctx.permanent)),
                    "fetched_size": len(str(ctx.fetched_data)),
                    "session_keys": list(ctx.session.keys()),
                    "has_assessment": bool(ctx.session.get("assessment")),
                    "intent_l3": ctx.session.get("intent_l3"),
                    "product_intent": ctx.session.get("product_intent"),
                }
            }, 200
            
        except Exception as e:
            return {"error": str(e), "user_id": user_id}, 500

    # ────────────────────────────────────────────────────────
    # STEP 5: Error Handlers
    # ────────────────────────────────────────────────────────
    @app.errorhandler(500)
    def handle_internal_error(error):
        """Handle internal server errors with proper logging."""
        log.error(f"INTERNAL_ERROR | error={error}", exc_info=True)
        return {
            "error": "Internal server error",
            "timestamp": datetime.now().isoformat(),
            "details": str(error) if app.debug else "Contact support"
        }, 500

    @app.errorhandler(404)
    def handle_not_found(error):
        """Handle 404 errors."""
        return {
            "error": "Endpoint not found",
            "timestamp": datetime.now().isoformat()
        }, 404

    # ────────────────────────────────────────────────────────
    # STEP 6: Final validation
    # ────────────────────────────────────────────────────────
    log.info("APP_INIT_COMPLETE | simplified architecture initialized successfully")
    
    # Validate required components
    required_extensions = ["ctx_mgr", "bot_core"]
    missing_extensions = [ext for ext in required_extensions if ext not in app.extensions]
    
    if missing_extensions:
        raise RuntimeError(f"Missing required extensions: {missing_extensions}")
    
    log.info(f"APP_VALIDATION_SUCCESS | extensions={list(app.extensions.keys())}")
    
    # Set app version
    app.version = "simplified-4-intent-v1.0.0"
    
    return app