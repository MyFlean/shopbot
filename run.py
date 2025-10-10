#!/usr/bin/env python3
"""
Shopping Bot Application Entry Point – Production-Safe
- Works under both Gunicorn (WSGI import) and python CLI.
- Ensures smart logging is initialized exactly once per process.
- Aligns Flask app logger with root logger for consistent output.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Tuple

from dotenv import load_dotenv
from flask import request

# Load env before any other imports that might read it
load_dotenv()

# Local imports after env load
from shopping_bot import create_app  # your app factory
from shopping_bot.utils.smart_logger import LogLevel, configure_logging

# --------------------------------------------------------------------------------------
# Logging initialization (one-time, safe under multiprocess servers like Gunicorn)
# --------------------------------------------------------------------------------------

_LOGGING_INITIALIZED = False  # process-level guard


def _to_python_level(level: LogLevel) -> int:
    """
    Map your LogLevel enum to a stdlib logging level.
    Falls back to INFO if unknown.
    """
    # If your LogLevel already mirrors stdlib names, this is trivial:
    mapping = {
        "TRACE": logging.DEBUG,   # or custom if you use TRACE
        "DEBUG": logging.DEBUG,
        "STANDARD": logging.INFO,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
        "SILENT": logging.CRITICAL,  # if you have one
    }
    name = getattr(level, "name", "INFO")
    return mapping.get(name, logging.INFO)


def setup_smart_logging() -> LogLevel:
    """
    Configure the smart logging system with validation.
    Idempotent: won’t add duplicate handlers if called multiple times.
    """
    global _LOGGING_INITIALIZED

    # Optional hard kill for logs (kept for compatibility with your existing behavior)
    if os.getenv("ONLY_LLM2_OUTPUTS", "").lower() in ("1", "true", "yes", "on"):
        # Respect the project's convention: silence most logs. Still initialize once to avoid surprises.
        if not _LOGGING_INITIALIZED:
            configure_logging(
                level=LogLevel.STANDARD,  # level value won’t matter if your configure_logging sets root to CRITICAL
                format_string="%(asctime)s | %(message)s",
                silence_external=True,
            )
            # Ensure root is effectively silent
            logging.getLogger().setLevel(logging.CRITICAL)
            _LOGGING_INITIALIZED = True
        return LogLevel.STANDARD  # nominal return

    # Resolve desired level from env
    desired = os.getenv("BOT_LOG_LEVEL", "STANDARD").upper()
    valid = {lvl.name for lvl in LogLevel}
    if desired not in valid:
        print(f"Warning: Invalid BOT_LOG_LEVEL '{desired}'. Valid options: {', '.join(sorted(valid))}")
        log_level = LogLevel.STANDARD
    else:
        log_level = LogLevel[desired]

    # Initialize root handlers exactly once
    if not _LOGGING_INITIALIZED:
        root = logging.getLogger()
        if not root.handlers:  # extra guard against double-init by other modules
            configure_logging(
                level=log_level,
                format_string="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                silence_external=True,  # keeps 3rd-party noise down
            )
        _LOGGING_INITIALIZED = True

    return log_level


# --------------------------------------------------------------------------------------
# Environment validation
# --------------------------------------------------------------------------------------

def validate_environment(strict: bool) -> None:
    """
    Validate critical env vars.
    - If strict=True: exit on missing vars (CLI path).
    - If strict=False: log a warning (WSGI path) so the pod can come up and emit a health page, etc.
    """
    required = {
        "ANTHROPIC_API_KEY": "Anthropic API integration",
        "REDIS_HOST": "Session storage",
    }
    missing = [f"{k} (required for {v})" for k, v in required.items() if not os.getenv(k)]

    if missing:
        msg = "Missing required environment variables: " + ", ".join(missing)
        if strict:
            print("Error:", msg)
            sys.exit(1)
        else:
            logging.getLogger(__name__).warning(msg)


# --------------------------------------------------------------------------------------
# Flask application creation and alignment with logging
# --------------------------------------------------------------------------------------

def _wire_app_logger(app, log_level: LogLevel) -> None:
    """
    Make Flask's app.logger flow into the root logger configured by smart logging.
    Prevent duplicate handlers and ensure correct level.
    """
    # Remove default Flask handlers (which often point at werkzeug logger) to avoid double logs
    if app.logger.handlers:
        app.logger.handlers.clear()

    # Propagate into root handlers configured by configure_logging()
    app.logger.propagate = True
    app.logger.setLevel(_to_python_level(log_level))


def create_application(strict_env: bool = False):
    """
    Create and configure the Flask application.
    - strict_env: whether to hard-fail on missing env (True for CLI, False for WSGI).
    """
    # Validate env first so we can fail/warn before wiring routes
    validate_environment(strict=strict_env)

    # Create the actual Flask app from your factory
    app = create_app()

    # Align Flask logger with root logging
    # Note: we call setup_smart_logging first to ensure handlers exist
    log_level = setup_smart_logging()
    _wire_app_logger(app, log_level)

    # Lightweight request log (emoji-friendly)
    @app.before_request
    def _log_request():
        # Example: "→ GET /api/v1/products"
        app.logger.info("→ %s %s", request.method, request.path)

    return app


# --------------------------------------------------------------------------------------
# Local dev server (python run.py)
# --------------------------------------------------------------------------------------

def _resolve_server_config() -> Tuple[str, int, bool]:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))

    flask_debug = os.getenv("FLASK_DEBUG", "").lower()
    if flask_debug in ("1", "true", "yes", "on"):
        debug = True
    elif flask_debug in ("0", "false", "no", "off"):
        debug = False
    else:
        debug = os.getenv("APP_ENV", "development").lower() == "development"

    return host, port, debug


def _print_startup_info(host: str, port: int, debug: bool, log_level: LogLevel) -> None:
    print("Shopping Bot Starting")
    print("=" * 60)
    print(f"Server:       http://{host}:{port}")
    print(f"Health check: http://{host}:{port}/__health")
    print(f"Environment:  {os.getenv('APP_ENV', 'development')}")
    print(f"Debug mode:   {debug}")
    print(f"Log level:    {log_level.name}")
    print(f"Process ID:   {os.getpid()}")
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
    # Initialize logging explicitly (idempotent)
    log_level = setup_smart_logging()

    # Build app with strict env validation for CLI path
    app = create_application(strict_env=True)

    host, port, debug = _resolve_server_config()
    _print_startup_info(host, port, debug, log_level)

    try:
        app.run(
            host=host,
            port=port,
            debug=debug,
            use_reloader=False,  # Avoid double init/log handlers in dev
            threaded=True,
        )
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    except Exception as e:
        print(f"Server error: {e}")
        sys.exit(1)


# --------------------------------------------------------------------------------------
# WSGI entrypoint for Gunicorn: `gunicorn run:app`
# --------------------------------------------------------------------------------------
# We want logging initialized even when imported by Gunicorn.
# We keep env validation non-strict here so the container can boot and emit diagnostics.
try:
    _ = setup_smart_logging()
except Exception:
    # Never block app creation due to logging issues
    pass

app = create_application(strict_env=False)

if __name__ == "__main__":
    main()
