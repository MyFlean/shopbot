
import pytest

from flean.backend.services.chat_service import ChatService

class DummyCore:
    async def process_query(self, message, ctx):
        class R:
            response_type = type('T', (), {'value': 'final_answer'})
            content = {'message': 'ok'}
            timestamp = None
            functions_executed = []
        return R()

def test_chat_service_history_and_session(monkeypatch):
    # Avoid real Redis/LLM by monkeypatching ctx_mgr to a lightweight fake
    svc = ChatService.__new__(ChatService)
    class DummyCtxMgr:
        def __init__(self):
            self.redis = None
            self._store = {}
        def get_context(self, uid, sid):
            class Ctx:
                def __init__(self, uid, sid):
                    self.user_id = uid
                    self.session_id = sid
                    self.permanent = {}
                    self.session = {}
                    self.fetched_data = {}
            return Ctx(uid, sid)
        def save_context(self, ctx):
            return True
    svc.ctx_mgr = DummyCtxMgr()
    svc.bot_core = DummyCore()

    sess = svc.create_session('u1')
    assert sess['session_id'] == 'u1'
    hist = svc.get_history('u1')
    assert isinstance(hist, list)
