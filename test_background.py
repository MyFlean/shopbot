import asyncio
import requests
import json
import time

BASE_URL = "http://localhost:8080"

def test_background_processing():
    """Test background processing functionality"""
    
    # Test 1: Start background processing
    print("🧪 Test 1: Starting background processing...")
    response = requests.post(f"{BASE_URL}/chat", json={
        "user_id": "test123",
        "session_id": "session123", 
        "message": "recommend gaming laptops under $1000",
        "background_processing": True
    })
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        processing_id = data.get("processing_id")
        print(f"Processing ID: {processing_id}")
        print("✅ Background processing started successfully!")
    else:
        print(f"❌ Error: {response.text}")

if __name__ == "__main__":
    test_background_processing()