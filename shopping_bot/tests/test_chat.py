# tests/test_chat.py
"""
Minimal happy-path test against the Flask app factory.

Run:  pytest -q
"""

from __future__ import annotations

import os
from typing import Dict

import pytest

# The app factory will live in shopping_bot.__init__.py (sent next turn)
from shopping_bot import create_app  # type: ignore


@pytest.fixture(scope="session")
def client():
    """Flask test client with a Redis URL pointing to local ephemeral instance."""
    os.environ.setdefault("REDIS_HOST", "localhost")
    os.environ.setdefault("REDIS_PORT", "6379")

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health(client):
    # All blueprints are registered with url_prefix='/rs' (see shopping_bot/__init__.py).
    res = client.get("/rs/health")
    assert res.status_code == 200
    data: Dict[str, str] = res.get_json()
    assert data["status"] == "healthy"


@pytest.mark.skipif(
    os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "") in ("", "ABSK-test-dummy-token-not-real"),
    reason="requires a real AWS_BEARER_TOKEN_BEDROCK with Claude/Bedrock access "
    "(conftest.py's dummy token lets the app construct, but any real LLM call "
    "will fail auth)",
)
def test_chat_flow(client):
    payload = {
        "user_id": "u1",
        "session_id": "s1",
        "message": "I'm looking for a gaming laptop",
    }
    res = client.post("/rs/chat", json=payload)
    assert res.status_code == 200
    body = res.get_json()
    assert body["response_type"] in ("question", "final_answer")
    assert "timestamp" in body
