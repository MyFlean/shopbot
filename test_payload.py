#!/usr/bin/env python3
"""
Test script for FE payload module
"""

import sys
import os
sys.path.append('.')

try:
    from shopping_bot.fe_payload import build_envelope, map_fe_response_type, normalize_content
    from shopping_bot.enums import ResponseType
    from shopping_bot.models import UserContext
    
    print("✅ All imports successful")
    
    # Test basic functions
    print("\n🧪 Testing map_fe_response_type:")
    print(f"QUESTION -> {map_fe_response_type(ResponseType.QUESTION, {})}")
    print(f"FINAL_ANSWER -> {map_fe_response_type(ResponseType.FINAL_ANSWER, {})}")
    print(f"PROCESSING_STUB -> {map_fe_response_type(ResponseType.PROCESSING_STUB, {})}")
    print(f"ERROR -> {map_fe_response_type(ResponseType.ERROR, {})}")
    
    print("\n🧪 Testing normalize_content:")
    print(f"QUESTION content: {normalize_content(ResponseType.QUESTION, {'message': 'Test question'})}")
    print(f"FINAL_ANSWER content: {normalize_content(ResponseType.FINAL_ANSWER, {'message': 'Test answer'})}")
    
    print("\n🧪 Testing build_envelope:")
    
    # Mock context
    class MockContext:
        def __init__(self):
            self.session = {"wa_id": "+1234567890"}
            self.session_id = "test_session_123"
    
    mock_ctx = MockContext()
    
    envelope = build_envelope(
        wa_id="+1234567890",
        session_id="test_session_123",
        bot_resp_type=ResponseType.FINAL_ANSWER,
        content={"message": "Test response"},
        ctx=mock_ctx,
        elapsed_time_seconds=1.234,
        mode_async_enabled=False,
        timestamp="2025-08-26T14:00:00Z",
        functions_executed=["search_products"]
    )
    
    print(f"Envelope: {envelope}")
    print(f"Response type: {envelope.get('response_type')}")
    print(f"Content: {envelope.get('content')}")
    print(f"Meta: {envelope.get('meta')}")
    
    print("\n✅ All tests passed!")
    
except Exception as e:
    print(f"❌ Test failed: {e}")
    import traceback
    traceback.print_exc()
