"""
Flask *application factory*.

Why a factory?
--------------
• Lets pytest spin up isolated app instances
• Allows different configs (dev / prod / test) without code forks
• Keeps top-level imports side-effect-free
"""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Flask
from flask_cors import CORS

from .bot_core import ShoppingBotCore
from .config import get_config
from .redis_manager import RedisContextManager
from .routes import register_routes

# NEW: Option B dispatcher (safe import: works even if client missing)
try:
    from .dual_message_dispather import DualMessageDispatcher  # the file name in repo is 'dispather'
except Exception:  # pragma: no cover
    DualMessageDispatcher = None  # type: ignore[assignment]

# Optionally wire a real WhatsApp client here if you have one.
# We'll keep it None by default; DualMessageDispatcher will run in mock mode otherwise.
whatsapp_client = None

log = logging.getLogger(__name__)
Cfg = get_config()


# ──────────────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────────────
def create_app(**overrides: Any) -> Flask:
    """
    Build and return a fully-wired Flask application.

    Pass keyword arguments to override `app.config` values (handy in tests).
    """
    app = Flask(__name__)
    app.config.from_object(Cfg)
    app.config.update(overrides)

    # CORS for Postman / local dev
    CORS(app)

    # Initialise shared singletons
    ctx_mgr = RedisContextManager()          # handles Redis connection
    bot_core = ShoppingBotCore(ctx_mgr)      # main business logic

    # Stash them for blueprints: current_app.extensions["ctx_mgr"]
    app.extensions["ctx_mgr"] = ctx_mgr
    app.extensions["bot_core"] = bot_core

    # ──────────────────────────────────────────────────────────────────────────
    # Enhanced Bot Core with Flow Support
    # ──────────────────────────────────────────────────────────────────────────
    enable_flows = os.getenv("ENABLE_WHATSAPP_FLOWS", "true").lower() == "true"
    
    if enable_flows:
        try:
            from .enhanced_bot_core import EnhancedShoppingBotCore
            enhanced_bot_core = EnhancedShoppingBotCore(bot_core)

            # Configure Flow features based on environment
            enhanced_bot_core.enable_flows(
                os.getenv("ENABLE_FLOW_GENERATION", "true").lower() == "true"
            )
            enhanced_bot_core.enable_enhanced_llm(
                os.getenv("ENABLE_ENHANCED_LLM", "true").lower() == "true"
            )
            app.extensions["enhanced_bot_core"] = enhanced_bot_core

            flow_status = "✅ enabled" if enhanced_bot_core.flow_enabled else "⚠️ disabled"
            llm_status = "✅ enabled" if enhanced_bot_core.enhanced_llm_enabled else "⚠️ disabled"
            log.info(f"Enhanced bot core initialized - Flows: {flow_status}, Enhanced LLM: {llm_status}")

            # ──────────────────────────────────────────────────────────────────
            # BACKGROUND PROCESSING + DISPATCHER SETUP (Option B)
            # ──────────────────────────────────────────────────────────────────
            enable_background = os.getenv("ENABLE_BACKGROUND_PROCESSING", "true").lower() == "true"

            if enable_background:
                try:
                    from .background_processor import BackgroundProcessor, FrontendNotifier

                    # Create dispatcher (server-sent Flow). If the class isn't available, run without it.
                    dispatcher = DualMessageDispatcher(whatsapp_client) if DualMessageDispatcher else None
                    if dispatcher:
                        app.extensions["dispatcher"] = dispatcher
                        log.info("✅ DualMessageDispatcher initialized%s",
                                 "" if whatsapp_client else " (mock mode)")

                    # Initialize background processor with dispatcher injected
                    notifier = FrontendNotifier()
                    background_processor = BackgroundProcessor(
                        enhanced_bot_core,
                        ctx_mgr,
                        dispatcher=dispatcher,  # <- inject dispatcher
                    )

                    # Store in app extensions
                    app.extensions["background_processor"] = background_processor
                    app.extensions["frontend_notifier"] = notifier

                    log.info("✅ Background processor initialized (Option B ready)")
                except ImportError as e:
                    log.warning(f"⚠️ Background processor dependencies missing: {e}")
                    log.info("📱 Continuing without background processing")
                except Exception as e:
                    log.error(f"❌ Background processor initialization failed: {e}")
                    log.info("📱 Continuing without background processing")
            else:
                log.info("📱 Background processing disabled via ENABLE_BACKGROUND_PROCESSING environment variable")
        except ImportError as e:
            log.warning(f"⚠️ Enhanced bot core dependencies missing: {e}")
            log.info("📱 Continuing with base bot core (Flows disabled)")
            log.info("💡 Install Flow dependencies or set ENABLE_WHATSAPP_FLOWS=false")
        except Exception as e:
            log.error(f"❌ Enhanced bot core initialization failed: {e}")
            log.info("📱 Continuing with base bot core (Flows disabled)")
    else:
        log.info("📱 WhatsApp Flows disabled via ENABLE_WHATSAPP_FLOWS environment variable")

    # Register all blueprints in shopping_bot/routes/*
    register_routes(app)

    # Log final initialization status
    has_enhanced = "enhanced_bot_core" in app.extensions
    has_background = "background_processor" in app.extensions
    core_type = "Enhanced (with Flows)" if has_enhanced else "Base (text-only)"
    bg_status = "with Background Processing" if has_background else "synchronous only"
    log.info(f"Flask app initialised (env={Cfg.__name__}, core={core_type}, processing={bg_status})")
    
    return app


# ──────────────────────────────────────────────────────────────────────────────
# Environment Configuration Helper
# ──────────────────────────────────────────────────────────────────────────────
def get_flow_config_info() -> dict[str, Any]:
    """
    Get current Flow configuration for debugging/monitoring.
    """
    return {
        "flows_enabled": os.getenv("ENABLE_WHATSAPP_FLOWS", "true").lower() == "true",
        "flow_generation_enabled": os.getenv("ENABLE_FLOW_GENERATION", "true").lower() == "true", 
        "enhanced_llm_enabled": os.getenv("ENABLE_ENHANCED_LLM", "true").lower() == "true",
        "background_processing_enabled": os.getenv("ENABLE_BACKGROUND_PROCESSING", "true").lower() == "true",
        "environment": os.getenv("APP_ENV", "development"),
        "log_level": os.getenv("BOT_LOG_LEVEL", "STANDARD"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Alternative Factory for Testing
# ──────────────────────────────────────────────────────────────────────────────
def create_app_without_flows(**overrides: Any) -> Flask:
    """
    Create app with Flows explicitly disabled - useful for testing legacy behavior.
    """
    # Force disable Flows
    overrides.setdefault("ENABLE_WHATSAPP_FLOWS", "false")
    
    # Temporarily set environment variable
    original_env = os.getenv("ENABLE_WHATSAPP_FLOWS")
    os.environ["ENABLE_WHATSAPP_FLOWS"] = "false"
    
    try:
        app = create_app(**overrides)
        return app
    finally:
        # Restore original environment
        if original_env is not None:
            os.environ["ENABLE_WHATSAPP_FLOWS"] = original_env
        else:
            os.environ.pop("ENABLE_WHATSAPP_FLOWS", None)
