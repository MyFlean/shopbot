import requests
import json

BASE_URL = "http://localhost:8080"

def test_flow_endpoints():
    """Test all flow endpoints"""
    
    # Test 1: Health checks
    print("🧪 Test 1: Health checks...")
    endpoints = ['/health', '/flow/health', '/flow/products/health', '/flow/onboarding/health']
    
    for endpoint in endpoints:
        try:
            response = requests.get(f"{BASE_URL}{endpoint}")
            print(f"{endpoint}: {response.status_code} - {response.json()}")
        except Exception as e:
            print(f"{endpoint}: Error - {e}")
    
    # Test 2: Products flow without processing_id (dummy data)
    print("\n🧪 Test 2: Products flow with dummy data...")
    try:
        response = requests.post(f"{BASE_URL}/flow/products", json={
            "action": "INIT",
            "version": "7.2"
        })
        
        data = response.json()
        products = data.get('data', {}).get('products', [])
        print(f"✅ Dummy products returned: {len(products)}")
        
        if len(products) > 0:
            print(f"First product: {products[0]['title']}")
        
    except Exception as e:
        print(f"❌ Products flow error: {e}")
    
    # Test 3: Onboarding flow
    print("\n🧪 Test 3: Onboarding flow...")
    try:
        response = requests.post(f"{BASE_URL}/flow/onboarding", json={
            "action": "INIT",
            "version": "7.2"
        })
        
        data = response.json()
        societies = data.get('data', {}).get('societies', [])
        print(f"✅ Onboarding societies: {len(societies)}")
        
    except Exception as e:
        print(f"❌ Onboarding flow error: {e}")

if __name__ == "__main__":
    test_flow_endpoints()