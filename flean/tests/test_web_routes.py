
import json
import pytest

from flean.backend.app import create_app

@pytest.fixture()
def app():
    app = create_app()
    app.config.update(TESTING=True)
    return app

@pytest.fixture()
def client(app):
    return app.test_client()

@pytest.mark.asyncio
async def test_session_create(client):
    rv = client.post('/api/chat/session', json={"user_id": "web_user"})
    assert rv.status_code == 201
    data = rv.get_json()
    assert data and data.get('session_id')

@pytest.mark.asyncio
async def test_send_message_monkeypatched(client, monkeypatch):
    # Monkeypatch ChatService to avoid external LLM/ES
    from flean.backend.routes import services_placeholder as sp

    class FakeSvc:
        def __init__(self):
            self.ctx_mgr = type('X', (), {'redis': None})
        async def process_message(self, user_id, message, session_id=None, platform='web'):
            return {"response_type": "final_answer", "content": {"message": "ok"}, "session_id": session_id or user_id}
        def get_history(self, user_id, limit=50):
            return []
        def create_session(self, user_id=None):
            return {"user_id": user_id or 'web_user', "session_id": (user_id or 'web_user')}

    monkeypatch.setattr(sp, 'get_chat_service', lambda: FakeSvc())

    rv = client.post('/api/chat/message', json={"user_id": "web_user", "message": "hi"})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data.get('response_type') == 'final_answer'

