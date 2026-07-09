"""
shopping_bot/tests/conftest.py

Ensures AWS_BEARER_TOKEN_BEDROCK has SOME value before any test constructs a
Flask app via create_app() with the default config_name='production'. That
path eagerly builds ShoppingBotCore -> LLMService(), which raises immediately
if this var is unset or empty (see llm_service.py's __init__) — unrelated to
the Bedrock embedding migration (this is the separate Claude/chat LLM client,
AsyncBedrockClient, not search_v2's embedding path), but it means even a pure
Flask/Redis smoke test like test_health (which never touches the LLM) could
not construct an app at all without a real credential.

Only sets a dummy value if the real one isn't already present in the
environment — if you DO have a real AWS_BEARER_TOKEN_BEDROCK configured
locally (e.g. via .env), tests will use that real credential instead, and any
test that actually depends on a working LLM call will get real behavior
instead of an auth failure.
"""
from __future__ import annotations

import os

if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
    # Not a real credential — only long/prefixed enough to satisfy
    # LLMService.__init__'s truthiness check and let app construction
    # succeed. Any test that actually calls the LLM with this value will
    # get a real (expected) auth failure from Bedrock, not a crash at
    # construction time — see test_chat.py/test_chat_stream.py for how
    # each test's own assertions account for that.
    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = "ABSK-test-dummy-token-not-real"
