#!/usr/bin/env python3
"""
Test script to verify the chat UI and streaming endpoint are working correctly.
Run this after starting the server to validate the setup.
"""
import os
import sys
import time
import json
import requests
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Configuration
BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8080")
ENABLE_STREAMING = os.getenv("ENABLE_STREAMING", "false").lower() in ("1", "true", "yes", "on")

def print_header(text):
    """Print a formatted header"""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)

def print_success(text):
    """Print success message"""
    print(f"✅ {text}")

def print_error(text):
    """Print error message"""
    print(f"❌ {text}")

def print_warning(text):
    """Print warning message"""
    print(f"⚠️  {text}")

def print_info(text):
    """Print info message"""
    print(f"ℹ️  {text}")

def test_server_health():
    """Test if the server is running"""
    print_header("Testing Server Health")
    
    try:
        response = requests.get(f"{BASE_URL}/__health", timeout=5)
        if response.status_code == 200:
            print_success(f"Server is running at {BASE_URL}")
            return True
        else:
            print_error(f"Server returned status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print_error(f"Cannot connect to server at {BASE_URL}")
        print_info("Make sure the server is running: python run.py")
        return False
    except Exception as e:
        print_error(f"Health check failed: {e}")
        return False

def test_chat_ui_route():
    """Test if the chat UI route is accessible"""
    print_header("Testing Chat UI Route")
    
    try:
        response = requests.get(f"{BASE_URL}/chat/ui", timeout=5)
        if response.status_code == 200:
            if "ShopBot" in response.text and "chat" in response.text.lower():
                print_success("Chat UI is accessible at /chat/ui")
                print_info(f"Open in browser: {BASE_URL}/chat/ui")
                return True
            else:
                print_warning("Route accessible but content unexpected")
                return False
        else:
            print_error(f"Chat UI returned status {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Chat UI test failed: {e}")
        return False

def test_streaming_config():
    """Check if streaming is enabled"""
    print_header("Testing Streaming Configuration")
    
    if ENABLE_STREAMING:
        print_success("ENABLE_STREAMING is set to true")
        return True
    else:
        print_error("ENABLE_STREAMING is not enabled")
        print_info("Set environment variable: export ENABLE_STREAMING=true")
        return False

def test_streaming_endpoint():
    """Test the streaming endpoint"""
    print_header("Testing Streaming Endpoint")
    
    if not ENABLE_STREAMING:
        print_warning("Skipping streaming endpoint test (ENABLE_STREAMING=false)")
        return False
    
    payload = {
        "user_id": "test_user",
        "session_id": f"test_session_{int(time.time())}",
        "message": "Hello",
        "channel": "web",
        "wa_id": ""
    }
    
    try:
        print_info("Sending test message to /rs/chat/stream...")
        
        response = requests.post(
            f"{BASE_URL}/rs/chat/stream",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream"
            },
            stream=True,
            timeout=30
        )
        
        if response.status_code != 200:
            print_error(f"Streaming endpoint returned status {response.status_code}")
            if response.text:
                print_info(f"Response: {response.text[:200]}")
            return False
        
        # Read first few events
        events_received = []
        lines_read = 0
        max_lines = 50  # Read up to 50 lines
        
        for line in response.iter_lines(decode_unicode=True):
            lines_read += 1
            if lines_read > max_lines:
                break
                
            if line and line.startswith("event:"):
                event_name = line[6:].strip()
                events_received.append(event_name)
        
        if events_received:
            print_success(f"Streaming endpoint is working")
            print_info(f"Received events: {', '.join(set(events_received))}")
            
            # Check for expected events
            if "ack" in events_received:
                print_success("  - Connection acknowledged")
            if "status" in events_received:
                print_success("  - Status updates received")
            if "end" in events_received or "final_answer.complete" in events_received:
                print_success("  - Stream completed successfully")
            
            return True
        else:
            print_warning("No events received from stream")
            return False
            
    except requests.exceptions.Timeout:
        print_error("Streaming request timed out")
        return False
    except Exception as e:
        print_error(f"Streaming endpoint test failed: {e}")
        return False

def test_environment_variables():
    """Check required environment variables"""
    print_header("Checking Environment Variables")
    
    required_vars = {
        "ANTHROPIC_API_KEY": "Required for LLM functionality",
        "REDIS_HOST": "Required for session management"
    }
    
    optional_vars = {
        "ENABLE_STREAMING": "Required for streaming chat",
        "REDIS_PORT": "Redis port (default: 6379)",
        "LLM_MODEL": "LLM model to use",
        "LLM_MAX_TOKENS": "Max tokens for responses"
    }
    
    all_good = True
    
    # Check required
    for var, description in required_vars.items():
        value = os.getenv(var)
        if value:
            # Mask sensitive values
            if "KEY" in var or "SECRET" in var:
                masked = value[:10] + "..." if len(value) > 10 else "***"
                print_success(f"{var} is set ({masked})")
            else:
                print_success(f"{var} = {value}")
        else:
            print_error(f"{var} is not set - {description}")
            all_good = False
    
    # Check optional
    print("\nOptional variables:")
    for var, description in optional_vars.items():
        value = os.getenv(var)
        if value:
            print_info(f"  {var} = {value}")
        else:
            print_info(f"  {var} not set (default will be used)")
    
    return all_good

def test_redis_connection():
    """Test Redis connectivity"""
    print_header("Testing Redis Connection")
    
    try:
        import redis
        
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_db = int(os.getenv("REDIS_DB", "0"))
        
        client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            decode_responses=True,
            socket_connect_timeout=5
        )
        
        # Try to ping
        if client.ping():
            print_success(f"Redis is accessible at {redis_host}:{redis_port}")
            
            # Get some info
            info = client.info("memory")
            memory_used = info.get("used_memory_human", "unknown")
            print_info(f"  Memory used: {memory_used}")
            
            return True
        else:
            print_error("Redis ping failed")
            return False
            
    except ImportError:
        print_warning("redis-py not installed, skipping Redis test")
        return True  # Don't fail if library not available
    except Exception as e:
        print_error(f"Redis connection failed: {e}")
        print_info("Make sure Redis is running: redis-server")
        return False

def main():
    """Run all tests"""
    print("\n" + "🤖 ShopBot Chat UI Test Suite".center(70))
    
    results = {
        "Environment Variables": test_environment_variables(),
        "Redis Connection": test_redis_connection(),
        "Server Health": test_server_health(),
        "Chat UI Route": test_chat_ui_route(),
        "Streaming Config": test_streaming_config(),
        "Streaming Endpoint": test_streaming_endpoint()
    }
    
    # Summary
    print_header("Test Summary")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {test_name:.<50} {status}")
    
    print("\n" + "-" * 70)
    print(f"  Results: {passed}/{total} tests passed")
    print("-" * 70)
    
    if passed == total:
        print("\n🎉 All tests passed! Your chat UI is ready to use.")
        print(f"\n👉 Open in browser: {BASE_URL}/chat/ui")
        return 0
    else:
        print("\n⚠️  Some tests failed. Please check the errors above.")
        print("\nQuick fixes:")
        print("  1. Make sure server is running: python run.py")
        print("  2. Set ENABLE_STREAMING=true in environment")
        print("  3. Ensure Redis is running: redis-server")
        print("  4. Check ANTHROPIC_API_KEY is set correctly")
        return 1

if __name__ == "__main__":
    sys.exit(main())

