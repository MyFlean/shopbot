#!/usr/bin/env python3
"""
Shopping Bot Application Entry Point - Simplified Architecture
Uses only: bot_core.py, llm_service.py, and ux_response_generator.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# Load environment variables before any other imports
from dotenv import load_dotenv
from flask import jsonify, request

load_dotenv()

from shopping_bot import create_app
from shopping_bot.utils.smart_logger import LogLevel, configure_logging

# Import-time logging initialization guard
_LOGGING_INITIALIZED = False


def setup_smart_logging() -> LogLevel:
    """Configure the smart logging system with validation."""
    global _LOGGING_INITIALIZED
    log_level_name = os.getenv("BOT_LOG_LEVEL", "STANDARD").upper()
    
    valid_levels = {level.name for level in LogLevel}
    if log_level_name not in valid_levels:
        print(f"Warning: Invalid BOT_LOG_LEVEL '{log_level_name}'. Valid options: {', '.join(valid_levels)}")
        log_level = LogLevel.STANDARD
    else:
        log_level = LogLevel[log_level_name]

    # Configure root logging only once per process
    if not _LOGGING_INITIALIZED:
        configure_logging(
            level=log_level,
            format_string="%(asctime)s | %(message)s",
            silence_external=True,
        )
        _LOGGING_INITIALIZED = True
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
    """Create and configure the Flask application with simplified architecture."""
    try:
        app = create_app()
        
        # Request logging middleware
        @app.before_request
        def log_request():
            app.logger.info("→ %s %s", request.method, request.path)

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
    """Print startup information."""
    print("Shopping Bot Starting (Simplified Architecture)")
    print("=" * 60)
    print(f"Server: http://{host}:{port}")
    print(f"Health check: http://{host}:{port}/__health")
    print(f"Environment: {os.getenv('APP_ENV', 'development')}")
    print(f"Debug mode: {debug}")
    print(f"Log level: {log_level.name}")
    print(f"Process ID: {os.getpid()}")
    print("-" * 60)
    print("Architecture:")
    print("  ✓ bot_core.py (with 4-intent classification)")
    print("  ✓ llm_service.py (updated with product intents)")
    print("  ✓ ux_response_generator.py (DPL/PSL/QR generation)")
    print("  ✓ Redis context manager")
    print("  ✗ enhanced_bot_core.py (removed)")
    print("  ✗ background_processor.py (simplified)")
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
# Ensure logging is initialized even when loaded under Gunicorn (run:app)
try:
    _wsgi_log_level = setup_smart_logging()
except Exception:
    # Never block app creation due to logging issues
    pass
app = create_application()

if __name__ == "__main__":
    main()