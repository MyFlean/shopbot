
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Reuse existing core/services from shopping_bot
from shopping_bot.redis_manager import RedisContextManager
from shopping_bot.models import UserContext
from shopping_bot.bot_core import ShoppingBotCore
from shopping_bot.enums import ResponseType
from shopping_bot.fe_payload import build_envelope
from shopping_bot.llm_service import LLMService
from shopping_bot.ux_response_generator import generate_ux_response_for_intent


class ChatService:
    """Shared service for chat logic across platforms (web/whatsapp).
    Wraps the simplified fast path pipeline without touching WA logic.
    """

    def __init__(self, ctx_mgr: Optional[RedisContextManager] = None, bot_core: Optional[ShoppingBotCore] = None) -> None:
        self.ctx_mgr = ctx_mgr or RedisContextManager()
        self.bot_core = bot_core or ShoppingBotCore(self.ctx_mgr)
        self.llm_service = LLMService()

    def create_session(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new session. If user_id not provided, mirror it for session_id."""
        uid = str(user_id or 'web_user')
        session_id = uid  # simple mapping for now
        ctx = self.ctx_mgr.get_context(uid, session_id)
        # Touch session to ensure keys exist
        ctx.session.setdefault('created_at', datetime.utcnow().isoformat() + 'Z')
        self.ctx_mgr.save_context(ctx)
        return {"user_id": uid, "session_id": session_id}

    def get_history(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return conversation history (most recent first limited by limit)."""
        ctx = self.ctx_mgr.get_context(user_id, user_id)
        history = ctx.session.get('conversation_history', []) or []
        return history[-limit:]

    async def process_message(self, user_id: str, message: str, *, session_id: Optional[str] = None, platform: str = 'web') -> Dict[str, Any]:
        """
        Process user message via fast path core and return a normalized envelope-like dict.
        """
        user_id = str(user_id)
        sid = str(session_id or user_id)
        ctx = self.ctx_mgr.get_context(user_id, sid)

        # Inject current user text
        ctx.session = ctx.session or {}
        ctx.session['current_user_text'] = message
        ctx.session['last_user_message'] = message

        # Route image selection or image url if present in ctx (web can pass via kwargs later)
        bot_resp = await self.bot_core.process_query(message, ctx)

        envelope = build_envelope(
            wa_id=None,
            session_id=sid,
            bot_resp_type=getattr(bot_resp, 'response_type', ResponseType.FINAL_ANSWER),
            content=getattr(bot_resp, 'content', {}) or {},
            ctx=ctx,
            elapsed_time_seconds=0.0,
            mode_async_enabled=False,
            timestamp=getattr(bot_resp, 'timestamp', None),
            functions_executed=getattr(bot_resp, 'functions_executed', []),
        )
        # Persist context after processing
        self.ctx_mgr.save_context(ctx)
        # Minimal normalized payload for web
        return {
            "response_type": envelope.get('response_type'),
            "content": envelope.get('content', {}),
            "session_id": sid,
            "timestamp": envelope.get('timestamp') or datetime.utcnow().isoformat() + 'Z',
        }

    def get_product_recommendations(self, user_id: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return last recommendation snapshot from session memory."""
        ctx = self.ctx_mgr.get_context(user_id, user_id)
        last_rec = ctx.session.get('last_recommendation', {}) or {}
        return {"products": last_rec.get('products', []), "as_of": last_rec.get('as_of')}
