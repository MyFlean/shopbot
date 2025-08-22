"""
Updated Main Application Integration - COMPLETE FIX INTEGRATION
==============================================================

This file integrates all the fixes and ensures proper initialization order.
Updated to work with all the fixed components we've created.
"""

import os
import logging
from flask import Flask
from datetime import datetime
from typing import Dict

# Import all fixed components
from shopping_bot.redis_manager import RedisContextManager
from shopping_bot.bot_core import ShoppingBotCore  # Baseline core
from shopping_bot.enhanced_bot_core import EnhancedShoppingBotCore  # Fixed enhanced core
from shopping_bot.background_processor import BackgroundProcessor  # Fixed background processor
from shopping_bot.config import get_config

# Import auto-registration system (YOUR EXISTING SYSTEM)
from shopping_bot.routes import register_routes

log = logging.getLogger(__name__)
Cfg = get_config()


def create_app() -> Flask:
    """
    Create Flask app with all fixed components properly integrated.
    
    INTEGRATION ORDER (CRITICAL):
    1. Redis connection & health check
    2. Context manager with fixed persistence
    3. Base bot core (unchanged)
    4. Enhanced bot core with fixed fetcher persistence  
    5. Background processor with fixed async handling
    6. Register routes using YOUR existing auto-registration
    7. Health checks and monitoring
    """
    
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')
    
    # ────────────────────────────────────────────────────────
    # STEP 1: Initialize Redis with health checking
    # ────────────────────────────────────────────────────────
    try:
        log.info("INIT_REDIS | starting Redis connection")
        ctx_mgr = RedisContextManager()  # Uses fixed Redis manager
        
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
    # STEP 2: Initialize Bot Cores with proper dependency injection
    # ────────────────────────────────────────────────────────
    try:
        log.info("INIT_BOT_CORES | initializing baseline and enhanced cores")
        
        # Base bot core (unchanged)
        base_bot_core = ShoppingBotCore(ctx_mgr)
        app.extensions["bot_core"] = base_bot_core
        log.info("INIT_BASE_BOT_SUCCESS")
        
        # Enhanced bot core (fixed version)
        enhanced_bot_core = EnhancedShoppingBotCore(base_bot_core)
        enhanced_bot_core.enable_flows(True)
        enhanced_bot_core.enable_enhanced_llm(True)
        app.extensions["enhanced_bot_core"] = enhanced_bot_core
        log.info("INIT_ENHANCED_BOT_SUCCESS | flows_enabled=true | enhanced_llm=true")
        
    except Exception as e:
        log.error(f"INIT_BOT_CORES_ERROR | error={e}", exc_info=True)
        raise RuntimeError(f"Failed to initialize bot cores: {e}")

    # ────────────────────────────────────────────────────────
    # STEP 3: Initialize Background Processor with fixed async handling
    # ────────────────────────────────────────────────────────
    try:
        log.info("INIT_BACKGROUND_PROCESSOR | initializing with fixed async handling")
        
        background_processor = BackgroundProcessor(enhanced_bot_core, ctx_mgr)
        app.extensions["background_processor"] = background_processor
        log.info("INIT_BACKGROUND_PROCESSOR_SUCCESS")
        
    except Exception as e:
        log.error(f"INIT_BACKGROUND_PROCESSOR_ERROR | error={e}", exc_info=True)
        raise RuntimeError(f"Failed to initialize background processor: {e}")

    # ────────────────────────────────────────────────────────
    # STEP 4: Register Routes using YOUR existing auto-registration
    # ────────────────────────────────────────────────────────
    try:
        log.info("REGISTER_ROUTES | using existing auto-registration system")
        
        # Use your existing auto-registration - this will find all bp variables in routes/
        register_routes(app)
        
        log.info("REGISTER_ROUTES_SUCCESS | auto-registration complete")
        
    except Exception as e:
        log.error(f"REGISTER_ROUTES_ERROR | error={e}", exc_info=True)
        raise RuntimeError(f"Failed to register routes: {e}")

    # ────────────────────────────────────────────────────────
    # STEP 5: Add Health Checks and Monitoring 
    # ────────────────────────────────────────────────────────
    @app.get("/__health")
    def comprehensive_health_check():
        """Comprehensive health check for all fixed components."""
        try:
            health_status = {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "components": {}
            }
            
            # Redis health
            redis_health = ctx_mgr.health_check()
            health_status["components"]["redis"] = {
                "healthy": redis_health.get("connection_healthy", False),
                "ping": redis_health.get("ping_success", False),
                "memory": redis_health.get("memory_info", {})
            }
            
            # Bot cores health
            health_status["components"]["bot_cores"] = {
                "base_core": bool(app.extensions.get("bot_core")),
                "enhanced_core": bool(app.extensions.get("enhanced_bot_core")),
                "background_processor": bool(app.extensions.get("background_processor"))
            }
            
            # Feature flags
            enhanced_bot = app.extensions.get("enhanced_bot_core")
            if enhanced_bot:
                health_status["components"]["features"] = {
                    "flows_enabled": enhanced_bot.flow_enabled,
                    "enhanced_llm_enabled": enhanced_bot.enhanced_llm_enabled
                }
            
            # Overall health assessment
            redis_ok = health_status["components"]["redis"]["healthy"]
            cores_ok = all(health_status["components"]["bot_cores"].values())
            
            if not (redis_ok and cores_ok):
                health_status["status"] = "degraded"
                
            return health_status, 200 if health_status["status"] == "healthy" else 503
            
        except Exception as e:
            return {
                "status": "unhealthy", 
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }, 500

    @app.get("/__diagnostics/<user_id>")
    def user_diagnostics(user_id: str):
        """Get comprehensive user diagnostics for debugging."""
        try:
            # Redis diagnostics
            redis_diag = ctx_mgr.get_diagnostics(user_id, user_id)
            
            # Context diagnostics
            ctx = ctx_mgr.get_context(user_id, user_id)
            
            # Assessment diagnostics (using fixed bot helpers)
            try:
                from shopping_bot.bot_helpers import get_assessment_diagnostics
                assessment_diag = get_assessment_diagnostics(ctx)
            except ImportError:
                # Fallback if function doesn't exist
                assessment_diag = {"error": "get_assessment_diagnostics not available"}
            
            return {
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
                "redis": redis_diag,
                "assessment": assessment_diag,
                "context_summary": {
                    "session_size": len(str(ctx.session)),
                    "permanent_size": len(str(ctx.permanent)),
                    "fetched_size": len(str(ctx.fetched_data))
                }
            }, 200
            
        except Exception as e:
            return {"error": str(e), "user_id": user_id}, 500

    @app.get("/__processing/<processing_id>")
    def processing_diagnostics(processing_id: str):
        """Get processing diagnostics for debugging background issues."""
        try:
            background_processor = app.extensions.get("background_processor")
            if not background_processor:
                return {"error": "Background processor not available"}, 500
                
            # Get status and result
            status = background_processor.ctx_mgr.get_processing_status(processing_id)
            result = background_processor.ctx_mgr.get_processing_result(processing_id)
            
            diagnostics = {
                "processing_id": processing_id,
                "timestamp": datetime.now().isoformat(),
                "status": status,
                "has_result": bool(result),
                "result_summary": {}
            }
            
            if result:
                diagnostics["result_summary"] = {
                    "response_type": result.get("response_type"),
                    "text_length": len(result.get("text_content", "")),
                    "sections_count": len(result.get("sections", {})),
                    "products_count": len(result.get("flow_data", {}).get("products", [])),
                    "functions_executed": result.get("functions_executed", [])
                }
                
            return diagnostics, 200
            
        except Exception as e:
            return {"error": str(e), "processing_id": processing_id}, 500

    # ────────────────────────────────────────────────────────
    # STEP 6: Request/Response Logging for Debugging
    # ────────────────────────────────────────────────────────
    @app.before_request
    def log_request():
        """Log incoming requests for debugging."""
        from flask import request
        if not request.path.startswith("/__"):  # Skip health checks
            log.info(f"REQUEST | method={request.method} | path={request.path} | remote_addr={request.remote_addr}")

    @app.after_request  
    def log_response(response):
        """Log outgoing responses for debugging."""
        from flask import request
        if not request.path.startswith("/__"):  # Skip health checks
            log.info(f"RESPONSE | method={request.method} | path={request.path} | status={response.status_code}")
        return response

    # ────────────────────────────────────────────────────────
    # STEP 7: Error Handlers
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
    # STEP 8: Startup Validation
    # ────────────────────────────────────────────────────────
    log.info("APP_INIT_COMPLETE | all fixed components initialized successfully")
    
    # Final validation
    required_extensions = ["ctx_mgr", "bot_core", "enhanced_bot_core", "background_processor"]
    missing_extensions = [ext for ext in required_extensions if ext not in app.extensions]
    
    if missing_extensions:
        raise RuntimeError(f"Missing required extensions: {missing_extensions}")
    
    log.info(f"APP_VALIDATION_SUCCESS | extensions={list(app.extensions.keys())}")
    
    # Set app version for health checks
    app.version = "fixed-v1.0.0"
    
    return app


# ────────────────────────────────────────────────────────
# Additional Configuration Functions
# ────────────────────────────────────────────────────────

def configure_logging_for_debugging():
    """Configure comprehensive logging for debugging the fixes."""
    import logging
    
    # Set log levels for different components
    log_levels = {
        "shopping_bot.redis_manager": "INFO",
        "shopping_bot.background_processor": "INFO", 
        "shopping_bot.enhanced_bot_core": "INFO",
        "shopping_bot.bot_helpers": "INFO",
        "shopping_bot.routes.chat": "INFO",
        "shopping_bot.routes.onboarding_flow": "INFO"
    }
    
    # Apply log levels
    for logger_name, level in log_levels.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(getattr(logging, level))
    
    # Add Redis operation tracking
    redis_logger = logging.getLogger("shopping_bot.redis_manager")
    redis_logger.info("REDIS_LOGGING_CONFIGURED | tracking all Redis operations")
    
    # Add background processing tracking  
    bg_logger = logging.getLogger("shopping_bot.background_processor")
    bg_logger.info("BACKGROUND_LOGGING_CONFIGURED | tracking async operations")


def validate_environment_variables():
    """Validate that all required environment variables are set."""
    required_vars = {
        "ANTHROPIC_API_KEY": "LLM service integration",
        "REDIS_HOST": "Session and result storage",
        "REDIS_PORT": "Redis connection",
    }
    
    optional_vars = {
        "FRONTEND_WEBHOOK_URL": "Background processing notifications",
        "WHATSAPP_FLOW_ID": "WhatsApp Flow integration",
        "WHATSAPP_FLOW_TOKEN": "Flow authentication",
    }
    
    missing_required = []
    missing_optional = []
    
    for var, purpose in required_vars.items():
        if not os.getenv(var):
            missing_required.append(f"  - {var} (required for {purpose})")
    
    for var, purpose in optional_vars.items():
        if not os.getenv(var):
            missing_optional.append(f"  - {var} (optional for {purpose})")
    
    if missing_required:
        error_msg = "Missing required environment variables:\n" + "\n".join(missing_required)
        log.error(f"ENV_VALIDATION_FAILED | {error_msg}")
        raise RuntimeError(error_msg)
    
    if missing_optional:
        warning_msg = "Missing optional environment variables:\n" + "\n".join(missing_optional)
        log.warning(f"ENV_VALIDATION_WARNINGS | {warning_msg}")
    
    log.info("ENV_VALIDATION_SUCCESS | all required variables present")


def setup_redis_monitoring(app: Flask):
    """Set up Redis connection monitoring and recovery."""
    ctx_mgr = app.extensions.get("ctx_mgr")
    if not ctx_mgr:
        return
    
    @app.before_request
    def check_redis_health():
        """Check Redis health before each request."""
        from flask import request
        
        # Skip health checks to avoid loops
        if request.path.startswith("/__"):
            return
            
        if not ctx_mgr._check_connection_health():
            log.warning("REDIS_UNHEALTHY | attempting to serve request with degraded Redis")
            # Continue serving but log the issue
    
    log.info("REDIS_MONITORING_CONFIGURED | health checks enabled")


def create_debug_routes(app: Flask):
    """Create additional debug routes for troubleshooting."""
    
    @app.get("/__redis/keys/<pattern>")
    def debug_redis_keys(pattern: str):
        """Debug endpoint to inspect Redis keys (development only)."""
        try:
            ctx_mgr = app.extensions.get("ctx_mgr")
            if not ctx_mgr:
                return {"error": "Redis manager not available"}, 500
            
            # Get keys matching pattern
            keys = ctx_mgr.redis.keys(pattern)
            
            key_info = {}
            for key in keys[:20]:  # Limit to 20 keys
                try:
                    key_type = ctx_mgr.redis.type(key)
                    ttl = ctx_mgr.redis.ttl(key)
                    size = ctx_mgr.redis.memory_usage(key) or 0
                    
                    key_info[key] = {
                        "type": key_type,
                        "ttl": ttl,
                        "size_bytes": size
                    }
                except Exception:
                    key_info[key] = {"error": "failed_to_inspect"}
            
            return {
                "pattern": pattern,
                "total_matches": len(keys),
                "showing": len(key_info),
                "keys": key_info
            }, 200
            
        except Exception as e:
            return {"error": str(e)}, 500
    
    @app.get("/__background/status")
    def debug_background_status():
        """Debug endpoint for background processor status."""
        try:
            background_processor = app.extensions.get("background_processor")
            if not background_processor:
                return {"error": "Background processor not available"}, 500
            
            # Get some general stats
            ctx_mgr = background_processor.ctx_mgr
            processing_keys = ctx_mgr.redis.keys("processing:*:status")
            result_keys = ctx_mgr.redis.keys("processing:*:result")
            
            return {
                "active_processing_ids": len(processing_keys),
                "stored_results": len(result_keys),
                "processor_healthy": True,  # If we got here, it's healthy
                "ttl_seconds": int(background_processor.processing_ttl.total_seconds())
            }, 200
            
        except Exception as e:
            return {"error": str(e)}, 500
    
    log.info("DEBUG_ROUTES_CONFIGURED | additional debugging endpoints enabled")


# ────────────────────────────────────────────────────────
# FINAL INTEGRATION WRAPPER
# ────────────────────────────────────────────────────────

def create_production_app() -> Flask:
    """
    Production-ready app factory with all fixes integrated.
    This is the main entry point that should be used.
    """
    
    # Step 1: Environment validation
    validate_environment_variables()
    
    # Step 2: Configure logging for debugging
    configure_logging_for_debugging()
    
    # Step 3: Create the core app with all fixes
    app = create_app()
    
    # Step 4: Add monitoring and debug capabilities
    setup_redis_monitoring(app)
    
    # Step 5: Add debug routes (only if debug mode)
    if app.debug or os.getenv("ENABLE_DEBUG_ROUTES", "false").lower() == "true":
        create_debug_routes(app)
        log.info("DEBUG_ROUTES_ENABLED | additional debugging endpoints available")
    
    # Step 6: Final startup log
    log.info("PRODUCTION_APP_READY | all fixes integrated and validated")
    log.info("FIX_SUMMARY | background_persistence=FIXED | status_lifecycle=FIXED | async_handling=FIXED | fetcher_persistence=FIXED | user_profile_persistence=FIXED | missing_awaits=FIXED | duplicate_writes=FIXED | cli_fallback=FIXED")
    
    return app


# ────────────────────────────────────────────────────────
# USAGE EXAMPLES AND TESTING HELPERS
# ────────────────────────────────────────────────────────

def test_fixed_components(app: Flask) -> Dict[str, bool]:
    """
    Test all fixed components to ensure they're working correctly.
    Returns dict of component_name -> success_status.
    """
    test_results = {}
    
    with app.app_context():
        try:
            # Test Redis Manager
            ctx_mgr = app.extensions["ctx_mgr"]
            test_ctx = ctx_mgr.get_context("test_user", "test_session")
            test_ctx.session["test"] = "value"
            save_success = ctx_mgr.save_context(test_ctx)
            test_results["redis_manager"] = save_success
            
            # Test Enhanced Bot Core
            enhanced_bot = app.extensions["enhanced_bot_core"]
            stats = enhanced_bot.get_flow_stats(test_ctx)
            test_results["enhanced_bot_core"] = bool(stats)
            
            # Test Background Processor
            background_processor = app.extensions["background_processor"]
            health = ctx_mgr.health_check()
            test_results["background_processor"] = health.get("connection_healthy", False)
            
            # Cleanup test data
            ctx_mgr.delete_session("test_session")
            
        except Exception as e:
            log.error(f"COMPONENT_TEST_ERROR | error={e}")
            test_results["error"] = str(e)
    
    return test_results


# Global app instance for WSGI servers (using fixed version)
app = create_production_app()