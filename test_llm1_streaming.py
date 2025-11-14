"""
Test script for LLM1 streaming implementation.

Tests the classify_and_assess_stream method with real Anthropic API calls
to verify that:
1. Tool arguments stream correctly via input_json_delta
2. User-facing strings are extracted incrementally
3. Complete payload is captured for internal state management
4. SSE events are emitted properly
"""

import asyncio
import json
import logging
import os
import sys
from typing import Dict, Any, List

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shopping_bot.llm_service import LLMService
from shopping_bot.models import UserContext
from shopping_bot.config import get_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)


class StreamingTestHarness:
    """Captures streaming events for testing"""
    
    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self.event_count = 0
        
    async def emit_callback(self, event_dict: Dict[str, Any]):
        """Callback for streaming events"""
        self.event_count += 1
        event_name = event_dict.get("event", "unknown")
        event_data = event_dict.get("data", {})
        
        self.events.append({
            "index": self.event_count,
            "event": event_name,
            "data": event_data
        })
        
        # Log events in real-time
        if event_name == "ask_message_delta":
            text = event_data.get("text", "")
            log.info(f"📝 ASK_MESSAGE | text='{text[:80]}...'")
        elif event_name == "simple_response_delta":
            text = event_data.get("text", "")
            log.info(f"💬 SIMPLE_RESPONSE | text='{text[:80]}...'")
        elif event_name == "classification_start":
            log.info("🚀 CLASSIFICATION_START")
        elif event_name == "classification_complete":
            log.info("✅ CLASSIFICATION_COMPLETE")
    
    def print_summary(self):
        """Print summary of captured events"""
        log.info("\n" + "="*80)
        log.info(f"STREAMING TEST SUMMARY")
        log.info("="*80)
        log.info(f"Total events captured: {len(self.events)}")
        
        event_counts = {}
        for event in self.events:
            event_name = event["event"]
            event_counts[event_name] = event_counts.get(event_name, 0) + 1
        
        log.info("\nEvent breakdown:")
        for event_name, count in sorted(event_counts.items()):
            log.info(f"  {event_name}: {count}")
        
        # Show captured messages
        messages = []
        for event in self.events:
            if event["event"] in ["ask_message_delta", "simple_response_delta"]:
                messages.append(event["data"].get("text", ""))
        
        if messages:
            log.info("\nCaptured messages:")
            for i, msg in enumerate(messages, 1):
                log.info(f"  {i}. {msg[:100]}...")
        
        log.info("="*80 + "\n")


async def test_product_query_streaming():
    """Test streaming with a product query (should generate ASK slots)"""
    log.info("\n" + "🧪 TEST 1: Product Query - 'I want chips for a party'")
    log.info("-" * 80)
    
    llm_service = LLMService()
    harness = StreamingTestHarness()
    
    # Create minimal context
    ctx = UserContext(user_id="test_user_1", session_id="test_session_1")
    ctx.session = {}
    ctx.permanent = {}
    ctx.fetched_data = {}
    
    query = "I want chips for a party"
    
    try:
        classification = await llm_service.classify_and_assess_stream(
            query, 
            ctx, 
            emit_callback=harness.emit_callback
        )
        
        log.info("\n📦 CLASSIFICATION RESULT:")
        log.info(f"  route: {classification.get('route')}")
        log.info(f"  data_strategy: {classification.get('data_strategy')}")
        log.info(f"  domain: {classification.get('domain')}")
        log.info(f"  category: {classification.get('category')}")
        log.info(f"  product_intent: {classification.get('product_intent')}")
        log.info(f"  is_product_related: {classification.get('is_product_related')}")
        
        ask_slots = classification.get('ask', {})
        if ask_slots:
            log.info(f"\n  ASK slots ({len(ask_slots)}):")
            for slot_name, slot_data in ask_slots.items():
                log.info(f"    {slot_name}: {slot_data.get('message')}")
                log.info(f"      options: {slot_data.get('options')}")
        
        harness.print_summary()
        
        # Assertions
        assert classification.get('route') == 'product', "Expected product route"
        assert classification.get('is_product_related') == True, "Expected product-related=True"
        assert len(ask_slots) >= 2, "Expected at least 2 ASK slots"
        assert harness.event_count > 0, "Expected streaming events"
        
        log.info("✅ TEST 1 PASSED\n")
        return True
        
    except Exception as e:
        log.error(f"❌ TEST 1 FAILED: {e}", exc_info=True)
        return False


async def test_simple_query_streaming():
    """Test streaming with a simple query (should generate simple_response)"""
    log.info("\n" + "🧪 TEST 2: Simple Query - 'What is Flean?'")
    log.info("-" * 80)
    
    llm_service = LLMService()
    harness = StreamingTestHarness()
    
    ctx = UserContext(user_id="test_user_2", session_id="test_session_2")
    ctx.session = {}
    ctx.permanent = {}
    ctx.fetched_data = {}
    
    query = "What is Flean?"
    
    try:
        classification = await llm_service.classify_and_assess_stream(
            query, 
            ctx, 
            emit_callback=harness.emit_callback
        )
        
        log.info("\n📦 CLASSIFICATION RESULT:")
        log.info(f"  route: {classification.get('route')}")
        log.info(f"  data_strategy: {classification.get('data_strategy')}")
        log.info(f"  is_product_related: {classification.get('is_product_related')}")
        
        simple_response = classification.get('simple_response', {})
        if simple_response:
            log.info(f"\n  Simple response:")
            log.info(f"    message: {simple_response.get('message', '')[:100]}...")
            log.info(f"    response_type: {simple_response.get('response_type')}")
        
        harness.print_summary()
        
        # Assertions
        assert classification.get('route') == 'general', "Expected general route"
        assert classification.get('data_strategy') == 'none', "Expected data_strategy=none"
        assert simple_response.get('message'), "Expected simple_response message"
        assert harness.event_count > 0, "Expected streaming events"
        
        log.info("✅ TEST 2 PASSED\n")
        return True
        
    except Exception as e:
        log.error(f"❌ TEST 2 FAILED: {e}", exc_info=True)
        return False


async def test_personal_care_query_streaming():
    """Test streaming with personal care query (should generate 4 ASK slots)"""
    log.info("\n" + "🧪 TEST 3: Personal Care Query - 'I need shampoo'")
    log.info("-" * 80)
    
    llm_service = LLMService()
    harness = StreamingTestHarness()
    
    ctx = UserContext(user_id="test_user_3", session_id="test_session_3")
    ctx.session = {}
    ctx.permanent = {}
    ctx.fetched_data = {}
    
    query = "I need shampoo"
    
    try:
        classification = await llm_service.classify_and_assess_stream(
            query, 
            ctx, 
            emit_callback=harness.emit_callback
        )
        
        log.info("\n📦 CLASSIFICATION RESULT:")
        log.info(f"  route: {classification.get('route')}")
        log.info(f"  domain: {classification.get('domain')}")
        log.info(f"  category: {classification.get('category')}")
        
        ask_slots = classification.get('ask', {})
        if ask_slots:
            log.info(f"\n  ASK slots ({len(ask_slots)}):")
            for slot_name, slot_data in ask_slots.items():
                log.info(f"    {slot_name}: {slot_data.get('message')}")
        
        harness.print_summary()
        
        # Assertions
        assert classification.get('route') == 'product', "Expected product route"
        assert classification.get('domain') == 'personal_care', "Expected personal_care domain"
        assert len(ask_slots) == 4, f"Expected exactly 4 ASK slots for personal_care, got {len(ask_slots)}"
        
        log.info("✅ TEST 3 PASSED\n")
        return True
        
    except Exception as e:
        log.error(f"❌ TEST 3 FAILED: {e}", exc_info=True)
        return False


async def main():
    """Run all streaming tests"""
    log.info("\n" + "="*80)
    log.info("LLM1 STREAMING TEST SUITE")
    log.info("="*80)
    
    # Verify config
    cfg = get_config()
    if not cfg.ANTHROPIC_API_KEY:
        log.error("❌ ANTHROPIC_API_KEY not set in environment")
        return False
    
    log.info(f"✅ Config loaded | Model: {cfg.LLM_MODEL}")
    
    # Run tests
    results = []
    
    results.append(await test_product_query_streaming())
    results.append(await test_simple_query_streaming())
    results.append(await test_personal_care_query_streaming())
    
    # Summary
    log.info("\n" + "="*80)
    log.info("FINAL RESULTS")
    log.info("="*80)
    passed = sum(results)
    total = len(results)
    log.info(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        log.info("🎉 ALL TESTS PASSED!")
        return True
    else:
        log.error(f"❌ {total - passed} test(s) failed")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

