#!/usr/bin/env python3
"""
Shopping Bot Application Entry Point - Enhanced with UX System
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from flask import request, jsonify

# Load environment variables before any other imports
from dotenv import load_dotenv
load_dotenv()

from shopping_bot import create_app
from shopping_bot.utils.smart_logger import configure_logging, LogLevel


def setup_smart_logging() -> LogLevel:
    """Configure the smart logging system with validation."""
    log_level_name = os.getenv("BOT_LOG_LEVEL", "STANDARD").upper()
    
    valid_levels = {level.name for level in LogLevel}
    if log_level_name not in valid_levels:
        print(f"Warning: Invalid BOT_LOG_LEVEL '{log_level_name}'. Valid options: {', '.join(valid_levels)}")
        log_level = LogLevel.STANDARD
    else:
        log_level = LogLevel[log_level_name]

    configure_logging(
        level=log_level,
        format_string="%(asctime)s | %(message)s",
        silence_external=True,
    )
    return log_level


def validate_environment() -> None:
    """Validate critical environment variables."""
    required_vars = {
        "ANTHROPIC_API_KEY": "Anthropic API integration",
        "REDIS_HOST": "Session storage",
    }
    
    missing_vars = []
    for var, purpose in required_vars.items():
        if not os.getenv(var):
            missing_vars.append(f"  - {var} (required for {purpose})")
    
    if missing_vars:
        print("Error: Missing required environment variables:")
        print("\n".join(missing_vars))
        print("\nPlease check your .env file or environment setup.")
        sys.exit(1)


def create_application():
    """Create and configure the Flask application with enhanced UX system."""
    try:
        app = create_app()
        
        # Initialize enhanced bot core alongside existing bot core
        from shopping_bot.enhanced_core import get_enhanced_shopping_bot_core
        from shopping_bot.redis_manager import RedisContextManager
        
        # Get existing context manager
        ctx_mgr = app.extensions.get("ctx_mgr")
        if ctx_mgr is None:
            # Fallback if not initialized yet
            ctx_mgr = RedisContextManager()
            app.extensions["ctx_mgr"] = ctx_mgr
        
        # Initialize enhanced bot core
        try:
            enhanced_bot_core = get_enhanced_shopping_bot_core(ctx_mgr)
            app.extensions["enhanced_bot_core"] = enhanced_bot_core
            
            # Log UX system status
            ux_enabled = app.config.get('UX_PATTERNS_ENABLED', True)
            rollout_pct = app.config.get('UX_ROLLOUT_PERCENTAGE', 100)
            print(f"Enhanced UX System: {'Enabled' if ux_enabled else 'Disabled'}")
            print(f"UX Rollout: {rollout_pct}%")
            
        except Exception as e:
            print(f"Warning: Failed to initialize enhanced bot core: {e}")
            print("Falling back to base bot core only")
        
        # Health check endpoint with UX system status
        @app.get("/__health")
        def health_check():
            enhanced_bot = app.extensions.get("enhanced_bot_core")
            base_bot = app.extensions.get("bot_core")
            
            health_data = {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "version": getattr(app, 'version', 'unknown'),
                "ux_system": {
                    "enhanced_bot_available": bool(enhanced_bot),
                    "base_bot_available": bool(base_bot),
                    "ux_patterns_enabled": app.config.get('UX_PATTERNS_ENABLED', False),
                    "rollout_percentage": app.config.get('UX_ROLLOUT_PERCENTAGE', 0)
                }
            }
            
            # Set degraded status if neither bot is available
            if not (enhanced_bot or base_bot):
                health_data["status"] = "degraded"
                health_data["issues"] = ["No bot core available"]
            
            return jsonify(health_data), 200

        # Enhanced request logging middleware
        @app.before_request
        def log_request():
            # Check for client version header
            client_version = request.headers.get('X-Client-Version', 'v1')
            if hasattr(request, 'get_json'):
                try:
                    json_data = request.get_json(silent=True)
                    if json_data and 'client_version' in json_data:
                        client_version = json_data['client_version']
                except:
                    pass
            
            app.logger.info("→ %s %s (client: %s)", request.method, request.path, client_version)

        # UX system debug endpoint
        @app.get("/__ux_debug")
        def ux_debug():
            """Debug endpoint for UX system status"""
            enhanced_bot = app.extensions.get("enhanced_bot_core")
            
            if not enhanced_bot:
                return jsonify({"error": "Enhanced bot core not available"}), 404
            
            debug_info = {
                "ux_patterns_enabled": app.config.get('UX_PATTERNS_ENABLED', False),
                "rollout_percentage": app.config.get('UX_ROLLOUT_PERCENTAGE', 0),
                "confidence_threshold": app.config.get('UX_CONFIDENCE_THRESHOLD', 0.7),
                "enhanced_services": {
                    "ux_classifier": hasattr(enhanced_bot, 'ux_classifier'),
                    "ux_response_generator": hasattr(enhanced_bot, 'ux_response_generator'),
                    "enhanced_llm_service": hasattr(enhanced_bot, 'enhanced_llm_service')
                }
            }
            
            return jsonify(debug_info), 200

        return app
        
    except Exception as e:
        print(f"Failed to create application: {e}")
        sys.exit(1)


def get_server_config() -> tuple[str, int, bool]:
    """Extract server configuration from environment."""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    
    # Determine debug mode
    flask_debug = os.getenv("FLASK_DEBUG", "").lower()
    if flask_debug in ("1", "true", "yes", "on"):
        debug = True
    elif flask_debug in ("0", "false", "no", "off"):
        debug = False
    else:
        # Fallback to app config or environment
        app_env = os.getenv("APP_ENV", "development").lower()
        debug = app_env == "development"
    
    return host, port, debug


def print_startup_info(host: str, port: int, debug: bool, log_level: LogLevel, app) -> None:
    """Print startup information including UX system status."""
    config_name = app.config.__class__.__name__ if hasattr(app, 'config') else "Unknown"
    
    # UX system info
    enhanced_bot = app.extensions.get("enhanced_bot_core")
    ux_enabled = app.config.get('UX_PATTERNS_ENABLED', False)
    rollout_pct = app.config.get('UX_ROLLOUT_PERCENTAGE', 0)
    
    print("Shopping Bot Starting")
    print("=" * 60)
    print(f"Server: http://{host}:{port}")
    print(f"Health check: http://{host}:{port}/__health")
    print(f"UX Debug: http://{host}:{port}/__ux_debug")
    print(f"Environment: {os.getenv('APP_ENV', 'development')}")
    print(f"Configuration: {config_name}")
    print(f"Debug mode: {debug}")
    print(f"Log level: {log_level.name}")
    print(f"Process ID: {os.getpid()}")
    print("-" * 60)
    print("UX System Status:")
    print(f"  Enhanced Bot Core: {'✓ Available' if enhanced_bot else '✗ Not Available'}")
    print(f"  UX Patterns: {'✓ Enabled' if ux_enabled else '✗ Disabled'}")
    print(f"  Rollout: {rollout_pct}% of users")
    print(f"  Client Support: v1 (legacy), v2 (enhanced)")
    print("=" * 60)


def main() -> None:
    """Main application entry point."""
    # Setup logging first
    log_level = setup_smart_logging()
    
    # Validate environment
    validate_environment()
    
    # Create application
    app = create_application()
    
    # Get server configuration
    host, port, debug = get_server_config()
    
    # Print startup information
    print_startup_info(host, port, debug, log_level, app)
    
    # Start the server
    try:
        app.run(
            host=host,
            port=port,
            debug=debug,
            use_reloader=False,  # Disable to avoid double initialization
            threaded=True
        )
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    except Exception as e:
        print(f"Server error: {e}")
        sys.exit(1)


# Global app instance for WSGI servers
app = create_application()

if __name__ == "__main__":
    main()