# shopping_bot/routes/__init__.py
"""
Blueprint auto-registration.

Put any flask.Blueprint in `shopping_bot/routes/<name>.py`
with the variable name **bp** and it will be discovered &
registered when `register_routes(app)` is called.

The app factory (shopping_bot.__init__.py) stores shared
objects like `ctx_mgr` and `bot_core` into `app.extensions`
so the individual route modules can access them via
`from flask import current_app`.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from flask import Blueprint, Flask


def register_routes(app: Flask) -> None:
    for finder, name, _ in pkgutil.iter_modules(__path__):
        print(f"DEBUG: Trying to import {name}")
        try:
            module: ModuleType = importlib.import_module(f"{__name__}.{name}")
            bp: Blueprint | None = getattr(module, "bp", None)
            print(f"DEBUG: Module {name} bp = {bp}")
            if isinstance(bp, Blueprint):
                app.register_blueprint(bp)
                if name == "product_search":
                    print(f"DEBUG: ✅ PRODUCT_SEARCH REGISTERED with {len(bp.deferred_functions)} routes")
                else:
                    print(f"DEBUG: Registered blueprint {name}")
        except Exception as e:
            print(f"DEBUG: Failed to import {name}: {e}")