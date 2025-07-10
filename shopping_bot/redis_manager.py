# shopping_bot/redis_manager.py
"""
Thin wrapper around redis-py that handles:

• Key naming conventions
• TTL handling
• (De)serialisation to / from JSON

Keeps the rest of the codebase free from raw Redis calls.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Dict

import redis

from .config import get_config
from .models import UserContext

log = logging.getLogger(__name__)
Cfg = get_config()


class RedisContextManager:
    """
    Manages user + session state inside Redis.
    """

    # ————————————————————————————————————————————————
    # Construction
    # ————————————————————————————————————————————————
    def __init__(self, client: redis.Redis | None = None):
        self.redis: redis.Redis = client or redis.Redis(
            host=Cfg.REDIS_HOST,
            port=Cfg.REDIS_PORT,
            db=Cfg.REDIS_DB,
            decode_responses=Cfg.REDIS_DECODE_RESPONSES,
        )
        self.ttl = timedelta(seconds=Cfg.REDIS_TTL_SECONDS)

    # ————————————————————————————————————————————————
    # Public API
    # ————————————————————————————————————————————————
    def get_context(self, user_id: str, session_id: str) -> UserContext:
        """
        Pulls all three JSON blobs (permanent, session, fetched) and
        converts them into a single `UserContext`.
        """
        permanent = self._get_json(f"user:{user_id}:permanent", default={})
        session = self._get_json(f"session:{session_id}", default={})
        fetched = self._get_json(f"session:{session_id}:fetched", default={})

        ctx = UserContext(
            user_id=user_id,
            session_id=session_id,
            permanent=permanent,
            session=session,
            fetched_data=fetched,
        )
        log.debug("Loaded context %s", ctx)
        return ctx

    def save_context(self, ctx: UserContext) -> None:
        """
        Persists the three parts back to Redis with TTL.
        """
        self._set_json(f"user:{ctx.user_id}:permanent", ctx.permanent, ttl=None)
        self._set_json(f"session:{ctx.session_id}", ctx.session, ttl=self.ttl)
        self._set_json(
            f"session:{ctx.session_id}:fetched", ctx.fetched_data, ttl=self.ttl
        )
        log.debug("Saved context for user=%s session=%s", ctx.user_id, ctx.session_id)

    def delete_session(self, session_id: str) -> None:
        """
        Convenience helper for the /reset route.
        """
        self.redis.delete(f"session:{session_id}")
        self.redis.delete(f"session:{session_id}:fetched")

    # ————————————————————————————————————————————————
    # Internal helpers
    # ————————————————————————————————————————————————
    def _get_json(self, key: str, *, default: Any = None) -> Any:
        raw = self.redis.get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Key %s contained non-JSON data, resetting", key)
            return default

    def _set_json(self, key: str, value: Any, *, ttl: timedelta | None) -> None:
        if ttl is None:
            self.redis.set(key, json.dumps(value))
        else:
            self.redis.setex(key, int(ttl.total_seconds()), json.dumps(value))
