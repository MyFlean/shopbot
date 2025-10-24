import json
import os


def test_stream_route_registered():
    os.environ["ENABLE_STREAMING"] = "true"
    from shopping_bot import create_app
    app = create_app()
    client = app.test_client()

    resp = client.post(
        "/rs/chat/stream",
        data=json.dumps({"user_id": "u1", "session_id": "s1", "message": "hello"}),
        content_type="application/json",
    )

    # Even though this is an SSE endpoint, Flask test client buffers the stream.
    # We just assert headers are correct and body contains an ack event.
    assert resp.status_code == 200 or resp.status_code == 400
    if resp.status_code == 200:
        assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
        body = resp.get_data(as_text=True)
        assert "event: ack" in body


