#!/usr/bin/env python3
"""
Standalone Elasticsearch Test Script
Test your ES index independently from your main application.
"""

import os
import json
import requests
from typing import Dict, Any, List

# Configuration - read from environment; normalize leading '@' and whitespace
def _normalize_es_base(raw_url: str, index: str) -> str:
    s = str(raw_url or "").strip()
    s = s.lstrip("@").strip().strip("'\"")
    if not s:
        return ""
    if "/_search" in s:
        s = s.split("/_search", 1)[0]
    idx = str(index or "").strip().strip("'\"")
    if idx and s.endswith(f"/{idx}"):
        s = s[: -(len(idx) + 1)]
    while s.endswith('/'):
        s = s[:-1]
    if not (s.startswith("http://") or s.startswith("https://")):
        s = f"https://{s}"
    return s

RAW_ES_URL = os.getenv("ES_URL") or os.getenv("ELASTIC_BASE", "")
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "products-v2")
ELASTIC_BASE = _normalize_es_base(RAW_ES_URL, ELASTIC_INDEX)
ELASTIC_API_KEY = (os.getenv("ES_API_KEY") or os.getenv("ELASTIC_API_KEY", "")).strip().strip("'\"")
TIMEOUT = int(os.getenv("ELASTIC_TIMEOUT_SECONDS", "10"))

class ESIndexTester:
    def __init__(self, base_url: str, index: str, api_key: str):
        self.base_url = base_url
        self.index = index
        self.api_key = api_key
        self.endpoint = f"{base_url}/{index}/_search"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"ApiKey {api_key}"
        }
    
    def test_connection(self) -> bool:
        """Test basic connection to ES"""
        print("Testing ES connection...")
        try:
            response = requests.get(
                f"{self.base_url}/_cluster/health",
                headers=self.headers,
                timeout=TIMEOUT
            )
            if response.status_code == 200:
                print(f"✅ ES connection successful: {response.json().get('status', 'unknown')}")
                return True
            else:
                print(f"❌ ES connection failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ ES connection error: {e}")
            return False
    
    def get_index_info(self) -> Dict[str, Any]:
        """Get basic index information"""
        print(f"\nGetting info for index '{self.index}'...")
        try:
            # Get index stats
            response = requests.get(
                f"{self.base_url}/{self.index}/_stats",
                headers=self.headers,
                timeout=TIMEOUT
            )
            if response.status_code == 200:
                stats = response.json()
                total_docs = stats.get("_all", {}).get("total", {}).get("docs", {}).get("count", 0)
                index_size = stats.get("_all", {}).get("total", {}).get("store", {}).get("size_in_bytes", 0)
                
                print(f"✅ Index exists")
                print(f"   Documents: {total_docs:,}")
                print(f"   Size: {index_size / (1024*1024):.2f} MB")
                return {"docs": total_docs, "size_mb": index_size / (1024*1024)}
            else:
                print(f"❌ Failed to get index info: {response.status_code}")
                return {}
        except Exception as e:
            print(f"❌ Error getting index info: {e}")
            return {}
    
    def sample_documents(self, size: int = 5) -> List[Dict[str, Any]]:
        """Get sample documents from the index"""
        print(f"\nGetting {size} sample documents...")
        query = {
            "size": size,
            "query": {"match_all": {}},
            "_source": ["name", "brand", "price", "mrp", "category_group", "package_claims.dietary_labels"]
        }
        
        try:
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query,
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                hits = data.get("hits", {}).get("hits", [])
                
                print(f"✅ Found {len(hits)} sample documents:")
                for i, hit in enumerate(hits, 1):
                    src = hit.get("_source", {})
                    print(f"\n{i}. {src.get('name', 'No name')}")
                    print(f"   Brand: {src.get('brand', 'No brand')}")
                    print(f"   Price: ₹{src.get('price', 'No price')}")
                    print(f"   Category: {src.get('category_group', 'No category')}")
                    
                    dietary = src.get("package_claims", {})
                    if isinstance(dietary, dict):
                        labels = dietary.get("dietary_labels", [])
                        if labels:
                            print(f"   Dietary: {labels}")
                
                return hits
            else:
                print(f"❌ Failed to get samples: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            print(f"❌ Error getting samples: {e}")
            return []
    
    def test_food_category(self) -> int:
        """Test how many food & beverage items exist"""
        print(f"\nTesting food & beverage category...")
        query = {
            "size": 0,  # Just count
            "query": {
                "term": {"category_group": "f_and_b"}
            }
        }
        
        try:
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query,
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                count = data.get("hits", {}).get("total", {}).get("value", 0)
                print(f"✅ Found {count:,} food & beverage items")
                return count
            else:
                print(f"❌ Failed to test category: {response.status_code}")
                return 0
                
        except Exception as e:
            print(f"❌ Error testing category: {e}")
            return 0
    
    def test_gluten_free_products(self) -> int:
        """Test how many gluten-free products exist"""
        print(f"\nTesting gluten-free products...")
        query = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"category_group": "f_and_b"}},
                        {"terms": {"package_claims.dietary_labels": ["GLUTEN FREE", "gluten free", "Gluten Free"]}}
                    ]
                }
            }
        }
        
        try:
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query,
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                count = data.get("hits", {}).get("total", {}).get("value", 0)
                print(f"✅ Found {count:,} gluten-free food items")
                
                # Get a few samples
                if count > 0:
                    query["size"] = 3
                    response = requests.post(self.endpoint, headers=self.headers, json=query, timeout=TIMEOUT)
                    if response.status_code == 200:
                        hits = response.json().get("hits", {}).get("hits", [])
                        print("   Sample gluten-free products:")
                        for hit in hits:
                            src = hit.get("_source", {})
                            print(f"   - {src.get('name', 'No name')} (₹{src.get('price', 'N/A')})")
                
                return count
            else:
                print(f"❌ Failed to test gluten-free: {response.status_code}")
                return 0
                
        except Exception as e:
            print(f"❌ Error testing gluten-free: {e}")
            return 0
    
    def test_price_range(self, max_price: float = 100) -> int:
        """Test how many products are under a certain price"""
        print(f"\nTesting products under ₹{max_price}...")
        query = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"category_group": "f_and_b"}},
                        {"range": {"price": {"lte": max_price}}}
                    ]
                }
            }
        }
        
        try:
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query,
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                count = data.get("hits", {}).get("total", {}).get("value", 0)
                print(f"✅ Found {count:,} food items under ₹{max_price}")
                return count
            else:
                print(f"❌ Failed to test price range: {response.status_code}")
                return 0
                
        except Exception as e:
            print(f"❌ Error testing price range: {e}")
            return 0
    
    def test_bread_search(self) -> int:
        """Test searching for bread products"""
        print(f"\nSearching for 'bread' products...")
        query = {
            "size": 5,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": "bread",
                                "fields": ["name^6", "ingredients.raw_text^4"],
                                "type": "most_fields",
                                "fuzziness": "AUTO"
                            }
                        }
                    ],
                    "filter": [
                        {"term": {"category_group": "f_and_b"}}
                    ]
                }
            }
        }
        
        try:
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query,
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                hits = data.get("hits", {}).get("hits", [])
                total = data.get("hits", {}).get("total", {}).get("value", 0)
                
                print(f"✅ Found {total:,} bread products (showing {len(hits)}):")
                for hit in hits:
                    src = hit.get("_source", {})
                    score = hit.get("_score", 0)
                    print(f"   - {src.get('name', 'No name')} (₹{src.get('price', 'N/A')}) [score: {score:.2f}]")
                
                return total
            else:
                print(f"❌ Failed to search bread: {response.status_code}")
                return 0
                
        except Exception as e:
            print(f"❌ Error searching bread: {e}")
            return 0
    
    def test_exact_query(self) -> int:
        """Test the exact query from your application"""
        print(f"\nTesting exact query: gluten free bread under ₹100...")
        query = {
            "size": 10,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": "gluten free bread",
                                "fields": ["name^6", "ingredients.raw_text^4", "package_claims.health_claims^2", "package_claims.dietary_labels^3"],
                                "type": "most_fields",
                                "operator": "and",
                                "fuzziness": "AUTO"
                            }
                        }
                    ],
                    "filter": [
                        {"term": {"category_group": "f_and_b"}},
                        {"range": {"price": {"lte": 100.0}}},
                        {"terms": {"package_claims.dietary_labels": ["GLUTEN FREE"]}}
                    ]
                }
            }
        }
        
        try:
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query,
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                hits = data.get("hits", {}).get("hits", [])
                total = data.get("hits", {}).get("total", {}).get("value", 0)
                
                print(f"✅ Exact query result: {total:,} products found")
                if hits:
                    print("   Matching products:")
                    for hit in hits:
                        src = hit.get("_source", {})
                        dietary = src.get("package_claims", {}).get("dietary_labels", [])
                        print(f"   - {src.get('name', 'No name')}")
                        print(f"     Price: ₹{src.get('price', 'N/A')}")
                        print(f"     Dietary: {dietary}")
                else:
                    print("   No matching products found")
                
                return total
            else:
                print(f"❌ Failed exact query: {response.status_code} - {response.text}")
                return 0
                
        except Exception as e:
            print(f"❌ Error in exact query: {e}")
            return 0

def main():
    print("Elasticsearch Index Tester")
    print("=" * 50)
    
    # Validate config
    api_key = ELASTIC_API_KEY
    if not ELASTIC_BASE:
        print("❌ No ES_URL or ELASTIC_BASE found in environment.")
        return
    if not api_key:
        print("❌ No API key found. Please set ES_API_KEY or ELASTIC_API_KEY in env.")
        return
    
    tester = ESIndexTester(ELASTIC_BASE, ELASTIC_INDEX, api_key)
    
    # Run all tests
    if not tester.test_connection():
        return
    
    tester.get_index_info()
    tester.sample_documents()
    tester.test_food_category()
    tester.test_gluten_free_products()
    tester.test_price_range(100)
    tester.test_bread_search()
    tester.test_exact_query()
    
    print("\n" + "=" * 50)
    print("Testing complete!")
    print("\nKey findings to check:")
    print("1. Are there any food & beverage items in your index?")
    print("2. Do any products have 'GLUTEN FREE' in dietary_labels?")
    print("3. Are there products under ₹100?")
    print("4. Does the field structure match your query expectations?")

if __name__ == "__main__":
    main()