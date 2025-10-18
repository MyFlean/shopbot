
from __future__ import annotations
from typing import Optional
from shopping_bot.redis_manager import RedisContextManager
from shopping_bot.bot_core import ShoppingBotCore
from ..services.chat_service import ChatService

_singleton: Optional[ChatService] = None

def get_chat_service() -> ChatService:
    global _singleton
    if _singleton is None:
        ctx_mgr = RedisContextManager()
        bot_core = ShoppingBotCore(ctx_mgr)
        _singleton = ChatService(ctx_mgr, bot_core)
    return _singleton
