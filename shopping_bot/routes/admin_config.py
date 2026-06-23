"""Operational admin routes."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

from flask import Blueprint, current_app, jsonify, request

from shopping_bot.utils.cards_config import (
    ensure_cards_config_in_redis,
    load_cards_config_source,
)

log = logging.getLogger(__name__)
bp = Blueprint("admin_config", __name__)


def _get_redis_client():
    ctx_mgr = current_app.extensions.get("ctx_mgr")
    if ctx_mgr is None and "_get_or_init_redis" in current_app.extensions:
        ctx_mgr = current_app.extensions["_get_or_init_redis"]()
    if ctx_mgr is None:
        raise RuntimeError("Redis context manager not available")
    return ctx_mgr.redis


@bp.route("/api/v1/admin/cards-config/reload", methods=["POST"])
def reload_cards_config() -> Tuple[Dict[str, Any], int]:
    """
    Load flean_card_config.json from S3/local and seed scorecard/* Redis keys.

    Equivalent to: python scripts/load_cards_config_to_redis.py [--force]
    """
    force_raw = request.args.get("force", "true")
    force = str(force_raw).strip().lower() in {"1", "true", "yes", "on"}

    try:
        source = load_cards_config_source(force_refresh=True)
        if not source:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No card config source found (S3 or local file)",
                    }
                ),
                404,
            )

        redis_client = _get_redis_client()
        written = ensure_cards_config_in_redis(redis_client, force=force)
        host = os.getenv("REDIS_HOST", "localhost")

        log.info(
            "CARDS_CONFIG_RELOAD | keys_written=%s | subcategories=%s | force=%s | host=%s",
            written,
            len(source),
            force,
            host,
        )

        return (
            jsonify(
                {
                    "success": True,
                    "keys_written": written,
                    "subcategories_in_source": len(source),
                    "force": force,
                    "redis_host": host,
                    "message": (
                        f"Seeded {written} scorecard/* keys to Redis at {host} "
                        f"({len(source)} subcategories in source)"
                    ),
                }
            ),
            200,
        )
    except Exception as exc:
        log.error("CARDS_CONFIG_RELOAD_ERROR | error=%s", exc, exc_info=True)
        return (
            jsonify({"success": False, "error": str(exc)}),
            500,
        )
