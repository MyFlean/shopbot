# shopping_bot/__init__.py
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
from typing import Any

from flask import Flask
from flask_cors import CORS

from .bot_core import ShoppingBotCore
from .config import get_config
from .redis_manager import RedisContextManager
from .routes import register_routes

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

    # Register all blueprints in shopping_bot/routes/*
    register_routes(app)

    log.info("Flask app initialised (env=%s)", Cfg.__name__)
    return app
