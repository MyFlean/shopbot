#!/usr/bin/env python3
"""
Test script for the new two-phase background processing
"""
import requests
import json
import time

BASE_URL = "http://localhost:8080"

def test_two_phase_processing():
    """Test the new two-phase processing flow"""
    
    print("🧪 Testing Two-Phase Background Processing")
    print("=" * 50)
    
    # Test data
    user_id = "test_user_123"
    session_id = "test_session_123"
    
    # Phase 1: Initial query - should return questions
    print("\n📝 Phase 1: Sending initial query...")
    initial_payload = {
        "user_id": user_id,
        "session_id": session_id,
        "message": "find me the best laptops for gaming"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/chat/whatsapp", json=initial_payload)
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Response type: {data.get('type')}")
            print(f"Message: {data.get('message', 'N/A')}")
            
            if data.get('type') == 'question':
                print("✅ Phase 1 Success: Got question as expected!")
                question_content = data.get('content', {})
                print(f"Question: {question_content.get('message', 'N/A')}")
                
                # Phase 2: Answer the question
                print("\n📝 Phase 2: Answering question...")
                answer_payload = {
                    "user_id": user_id,
                    "session_id": session_id,
                    "message": "High performance"  # Sample answer
                }
                
                answer_response = requests.post(f"{BASE_URL}/chat/continue-processing", json=answer_payload)
                print(f"Answer Status: {answer_response.status_code}")
                
                if answer_response.status_code == 200:
                    answer_data = answer_response.json()
                    print(f"Answer Response type: {answer_data.get('type')}")
                    
                    if answer_data.get('type') == 'question':
                        print("📝 Got another question - this is normal for multi-question flows")
                        print(f"Next question: {answer_data.get('content', {}).get('message', 'N/A')}")
                        
                        # Answer second question
                        print("\n📝 Phase 3: Answering second question...")
                        second_answer_payload = {
                            "user_id": user_id,
                            "session_id": session_id,
                            "message": "₹50k-₹80k"  # Budget range
                        }
                        
                        second_response = requests.post(f"{BASE_URL}/chat/continue-processing", json=second_answer_payload)
                        if second_response.status_code == 200:
                            second_data = second_response.json()
                            if second_data.get('type') == 'text' and 'processing_id' in second_data.get('content', {}):
                                processing_id = second_data['content']['processing_id']
                                print(f"✅ Phase 3 Success: Started background processing with ID: {processing_id}")
                                
                                # Wait and check status
                                print("\n⏳ Waiting for background processing...")
                                time.sleep(5)
                                
                                status_response = requests.get(f"{BASE_URL}/chat/processing/{processing_id}/status")
                                if status_response.status_code == 200:
                                    status = status_response.json()
                                    print(f"Processing Status: {status.get('status', 'unknown')}")
                                    
                                    if status.get('status') == 'completed':
                                        print("🎉 Background processing completed!")
                                        
                                        # Get results
                                        result_response = requests.get(f"{BASE_URL}/chat/processing/{processing_id}/result")
                                        if result_response.status_code == 200:
                                            result = result_response.json()
                                            print(f"✅ Got results with {len(result.get('products', []))} products")
                                        else:
                                            print(f"❌ Failed to get results: {result_response.status_code}")
                                    else:
                                        print(f"⏳ Still processing: {status.get('status')}")
                                else:
                                    print(f"❌ Failed to get status: {status_response.status_code}")
                            else:
                                print(f"❌ Expected processing start, got: {second_data}")
                        else:
                            print(f"❌ Second answer failed: {second_response.status_code}")
                    
                    elif answer_data.get('type') == 'text' and 'processing_id' in answer_data.get('content', {}):
                        processing_id = answer_data['content']['processing_id']
                        print(f"✅ Phase 2 Success: Started background processing with ID: {processing_id}")
                    else:
                        print(f"❌ Unexpected response type: {answer_data.get('type')}")
                else:
                    print(f"❌ Phase 2 failed: {answer_response.status_code}")
                    print(f"Error: {answer_response.text}")
            
            elif data.get('type') == 'text' and 'processing_id' in data.get('content', {}):
                print("✅ No questions needed - went straight to background processing")
                processing_id = data['content']['processing_id']
                print(f"Processing ID: {processing_id}")
            else:
                print(f"❌ Unexpected initial response: {data}")
        else:
            print(f"❌ Phase 1 failed: {response.status_code}")
            print(f"Error: {response.text}")
            
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")

def test_health_check():
    """Test if the server is running"""
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            print("✅ Server is running")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Cannot connect to server: {e}")
        return False

if __name__ == "__main__":
    print("🚀 Two-Phase Processing Test Suite")
    print("Make sure the server is running on http://localhost:8080")
    print()
    
    if test_health_check():
        test_two_phase_processing()
    else:
        print("❌ Server not available. Please start the server first:")
        print("   python run.py")
