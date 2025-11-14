#!/bin/bash
# Test script for streaming endpoint

echo "Testing /rs/chat/stream endpoint with SSE..."
echo "==========================================="
echo ""

curl -N -H "Accept: text/event-stream" \
  http://127.0.0.1:8080/rs/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_streaming_user",
    "session_id": "test_streaming_session",
    "message": "I want chips for a party"
  }'

echo ""
echo ""
echo "==========================================="
echo "If you see events streaming above, it worked!"

