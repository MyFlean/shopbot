"""
Test script for memory-based answering system
==============================================

This demonstrates the XML-tagged memory structure and how it's used
for memory-only answering.
"""

import json
from shopping_bot.bot_helpers import format_memory_for_llm, _classify_content_type

# Sample conversation history with different content types
sample_conversation = [
    {
        "user_query": "Hello! What is Flean?",
        "final_answer": {
            "response_type": "bot_identity",
            "message_preview": "Hi! I'm Flean, your shopping assistant...",
            "has_products": False
        },
        "internal_actions": {
            "intent_classified": "General_Help",
            "fetchers_executed": []
        },
        "timestamp": "2025-10-09T10:00:00",
        "content_type": "CASUAL",
        "data_source": "none",
        "product_metadata": None
    },
    {
        "user_query": "I want chips",
        "final_answer": {
            "response_type": "final_answer",
            "message_preview": "Here are some great chip options for you...",
            "has_products": True
        },
        "internal_actions": {
            "intent_classified": "Product_Discovery",
            "fetchers_executed": ["search_products"]
        },
        "timestamp": "2025-10-09T10:01:00",
        "content_type": "PRODUCT",
        "data_source": "es_fetch",
        "product_metadata": {
            "product_intent": "show_me_options",
            "has_products": True,
            "data_source": "es_fetch",
            "domain": "f_and_b"
        }
    },
    {
        "user_query": "tell me more about the first two",
        "final_answer": {
            "response_type": "final_answer",
            "message_preview": "The first product is Lays Classic Salted Chips at ₹50...",
            "has_products": True
        },
        "internal_actions": {
            "intent_classified": "Product_Discovery",
            "fetchers_executed": []
        },
        "timestamp": "2025-10-09T10:02:00",
        "content_type": "PRODUCT",
        "data_source": "memory_only",
        "product_metadata": {
            "product_intent": "show_me_options",
            "has_products": True,
            "data_source": "memory_only",
            "domain": "f_and_b"
        }
    }
]

def test_content_classification():
    """Test content type classification"""
    print("=" * 80)
    print("TEST 1: Content Type Classification")
    print("=" * 80)
    
    test_cases = [
        {
            "final_answer": {"response_type": "bot_identity", "has_products": False},
            "internal_actions": {"intent_classified": "General_Help"},
            "expected": "CASUAL"
        },
        {
            "final_answer": {"response_type": "final_answer", "has_products": True},
            "internal_actions": {"intent_classified": "Product_Discovery"},
            "expected": "PRODUCT"
        },
        {
            "final_answer": {"response_type": "is_support_query", "has_products": False},
            "internal_actions": {"intent_classified": "Technical_Support"},
            "expected": "SUPPORT"
        }
    ]
    
    for i, case in enumerate(test_cases, 1):
        result = _classify_content_type(case["final_answer"], case["internal_actions"])
        status = "✅" if result == case["expected"] else "❌"
        print(f"{status} Test {i}: Expected {case['expected']}, Got {result}")
    
    print()

def test_xml_formatting():
    """Test XML-formatted memory output"""
    print("=" * 80)
    print("TEST 2: XML Memory Formatting")
    print("=" * 80)
    
    xml_output = format_memory_for_llm(sample_conversation, max_turns=3)
    print(xml_output)
    print()
    
    # Verify key XML elements are present
    checks = [
        ("<conversation_memory>", "Opening tag"),
        ("</conversation_memory>", "Closing tag"),
        ('type="CASUAL"', "Casual turn type"),
        ('type="PRODUCT"', "Product turn type"),
        ("<product_intent>", "Product intent tag"),
        ("<data_source>", "Data source tag"),
        ("es_fetch", "ES fetch source"),
        ("memory_only", "Memory-only source")
    ]
    
    print("XML Validation:")
    for check, description in checks:
        status = "✅" if check in xml_output else "❌"
        print(f"{status} {description}: '{check}'")
    
    print()

def test_memory_indicators():
    """Test memory indicator detection (from bot_core logic)"""
    print("=" * 80)
    print("TEST 3: Memory Indicator Detection")
    print("=" * 80)
    
    memory_indicators = [
        "above", "those", "these", "that", "previous", "earlier",
        "you showed", "you recommended", "you suggested", "from the list",
        "first", "second", "third", "last one", "compare them"
    ]
    
    test_queries = [
        ("tell me more about those products", True),
        ("what were the options you showed?", True),
        ("compare the first two from your list", True),
        ("explain the second product", True),
        ("I want organic chips", False),  # New search, not memory reference
        ("show me more brands", False),   # Might be follow-up but not specific reference
    ]
    
    for query, should_match in test_queries:
        has_reference = any(indicator in query.lower() for indicator in memory_indicators)
        status = "✅" if has_reference == should_match else "❌"
        action = "memory_only" if has_reference else "es_fetch"
        print(f"{status} '{query}' → {action}")
    
    print()

def test_data_source_tracking():
    """Verify data_source is tracked in all turns"""
    print("=" * 80)
    print("TEST 4: Data Source Tracking")
    print("=" * 80)
    
    for i, turn in enumerate(sample_conversation, 1):
        user_q = turn["user_query"][:40]
        source = turn.get("data_source", "MISSING")
        content_type = turn.get("content_type", "MISSING")
        
        status = "✅" if source != "MISSING" else "❌"
        print(f"{status} Turn {i}: '{user_q}' → type={content_type}, source={source}")
    
    print()

if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "MEMORY-BASED ANSWERING TEST SUITE" + " " * 25 + "║")
    print("╚" + "=" * 78 + "╝")
    print()
    
    test_content_classification()
    test_xml_formatting()
    test_memory_indicators()
    test_data_source_tracking()
    
    print("=" * 80)
    print("ALL TESTS COMPLETE")
    print("=" * 80)
    print()
    print("Next Steps:")
    print("1. Run this test: python test_memory_answering.py")
    print("2. Test with actual queries in your development environment")
    print("3. Monitor logs for DATA_STRATEGY, MEMORY_ONLY_PATH messages")
    print("4. Verify memory-only queries are faster than ES queries")
    print()

