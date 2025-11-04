#!/bin/bash
# Test Cases for Recommendation Engine - Testing all 3 LLMs
# ============================================================
# 
# This script tests the recommendation engine by exercising each of the 3 LLM calls:
# 
# LLM1: classify_and_assess - Classification and assessment (route, domain, product_intent, ask_slots)
# LLM2: extract_search_params / plan_es_search - ES parameter extraction (q, category, filters, price)
# LLM3: generate_final_answer_unified - Final response generation (summary, products, UX)
#
# Usage:
#   export BASE_URL="http://localhost:5000"  # or your server URL
#   export USER_ID="test_user_$(date +%s)"   # unique user ID
#   bash test_recommendation_engine.sh

set -e

BASE_URL="${BASE_URL:-http://localhost:5000}"
USER_ID="${USER_ID:-test_user_$(date +%s)}"
SESSION_ID="${SESSION_ID:-$USER_ID}"

echo "=========================================="
echo "Recommendation Engine Test Suite"
echo "=========================================="
echo "Base URL: $BASE_URL"
echo "User ID: $USER_ID"
echo "Session ID: $SESSION_ID"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Helper function to make API calls
make_request() {
    local test_name="$1"
    local payload="$2"
    local expected_status="${3:-200}"
    
    echo -e "${BLUE}▶ Testing: $test_name${NC}"
    echo "Request: $payload"
    echo ""
    
    response=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "$BASE_URL/chat")
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')
    
    echo "HTTP Status: $http_code"
    
    if [ "$http_code" -eq "$expected_status" ]; then
        echo -e "${GREEN}✓ Status OK${NC}"
    else
        echo -e "${RED}✗ Expected $expected_status, got $http_code${NC}"
    fi
    
    echo "Response:"
    echo "$body" | jq '.' 2>/dev/null || echo "$body"
    echo ""
    echo "----------------------------------------"
    echo ""
    
    # Extract and log LLM call indicators
    if echo "$body" | jq -e '.response_type' > /dev/null 2>&1; then
        response_type=$(echo "$body" | jq -r '.response_type')
        echo -e "${YELLOW}Response Type: $response_type${NC}"
    fi
    
    return $http_code
}

# ============================================================================
# TEST GROUP 1: LLM1 - Classification and Assessment
# ============================================================================
echo -e "${GREEN}=========================================="
echo "TEST GROUP 1: LLM1 - Classification & Assessment"
echo "==========================================${NC}"
echo ""

# Test 1.1: Simple product query (Food & Beverage)
make_request \
    "LLM1: Food product query - should classify as product route" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"I want chips\"}"

# Test 1.2: Personal care product query
make_request \
    "LLM1: Personal care query - should classify as product route with domain=personal_care" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"show me shampoo\"}"

# Test 1.3: Support query - should route to support
make_request \
    "LLM1: Support query - should route to support" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"where is my order\"}"

# Test 1.4: General query - should route to general
make_request \
    "LLM1: General query - should route to general" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"hello\"}"

# Test 1.5: Out of category query
make_request \
    "LLM1: Out of category - should route to general with out_of_category" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"I need a laptop\"}"

# Test 1.6: Product intent classification - "is this good"
make_request \
    "LLM1: Product intent 'is_this_good' - should detect single product evaluation" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"is Veeba ketchup good?\"}"

# Test 1.7: Product intent classification - "which is better"
make_request \
    "LLM1: Product intent 'which_is_better' - should detect comparison" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"which is better, Lays or Kurkure chips?\"}"

# Test 1.8: Product intent classification - "show_me_alternate"
make_request \
    "LLM1: Product intent 'show_me_alternate' - should detect alternative request" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"show me alternatives to this shampoo\"}"

# Test 1.9: Product intent classification - "show_me_options"
make_request \
    "LLM1: Product intent 'show_me_options' - should detect exploration" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"what are my options for protein bars?\"}"

# ============================================================================
# TEST GROUP 2: LLM2 - ES Parameter Extraction
# ============================================================================
echo -e "${GREEN}=========================================="
echo "TEST GROUP 2: LLM2 - ES Parameter Extraction"
echo "==========================================${NC}"
echo ""

# Test 2.1: Basic query with category extraction
make_request \
    "LLM2: Basic food query - should extract category_path, q, category_group" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"show me organic chips\"}"

# Test 2.2: Query with dietary requirements
make_request \
    "LLM2: Query with dietary - should extract dietary_terms (GLUTEN FREE, PALM OIL FREE)" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"gluten free noodles without palm oil\"}"

# Test 2.3: Query with price range
make_request \
    "LLM2: Query with budget - should extract price_min/price_max" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"chips under 100 rupees\"}"

# Test 2.4: Query with brand filter
make_request \
    "LLM2: Query with brand - should extract brands array" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"show me Lays chips\"}"

# Test 2.5: Complex query with multiple filters
make_request \
    "LLM2: Complex query - should extract all filters (dietary, price, brand)" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"vegan protein bars under 200 rupees from Quest\"}"

# Test 2.6: Personal care query with compatibility
make_request \
    "LLM2: Personal care query - should extract skin_types, hair_types, efficacy_terms" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"shampoo for oily hair with dandruff\"}"

# Test 2.7: Follow-up query - should maintain anchor and apply delta
make_request \
    "LLM2: Follow-up constraint - should maintain category and add new constraint" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"make it organic\"}"

# Test 2.8: Generic follow-up - should carry over category
make_request \
    "LLM2: Generic follow-up - should carry category_path from previous turn" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"cheaper options\"}"

# Test 2.9: Query with nutritional constraints
make_request \
    "LLM2: Macro filters - should extract macro_filters (protein, sodium)" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"protein bars with more than 20g protein and less than 200mg sodium\"}"

# ============================================================================
# TEST GROUP 3: LLM3 - Final Response Generation
# ============================================================================
echo -e "${GREEN}=========================================="
echo "TEST GROUP 3: LLM3 - Final Response Generation"
echo "==========================================${NC}"
echo ""

# Test 3.1: MPM response (Multiple Product Mode)
make_request \
    "LLM3: MPM response - should generate summary with multiple products and hero" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"show me options for healthy snacks\"}"

# Test 3.2: SPM response (Single Product Mode)
make_request \
    "LLM3: SPM response - should generate focused single product response" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"is this Himalayan chips product good?\"}"

# Test 3.3: Response with product recommendations
make_request \
    "LLM3: Product recommendations - should include product_ids, summary, UX components" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"recommend some breakfast cereals\"}"

# Test 3.4: Response with quick replies
make_request \
    "LLM3: Quick replies - should include dpl_runtime_text and quick_replies array" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"what chocolate options do you have?\"}"

# Test 3.5: Response with enriched product details
make_request \
    "LLM3: Enriched details - should include product descriptions with nutritional info" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"show me high protein snacks\"}"

# ============================================================================
# TEST GROUP 4: End-to-End Recommendation Flow (All 3 LLMs)
# ============================================================================
echo -e "${GREEN}=========================================="
echo "TEST GROUP 4: End-to-End Flow (All 3 LLMs)"
echo "==========================================${NC}"
echo ""

# Test 4.1: Complete flow - Food product
echo -e "${YELLOW}Testing complete flow: Initial query${NC}"
make_request \
    "E2E: Initial query - LLM1 classifies, LLM2 extracts, LLM3 generates" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"I want chips\"}"

sleep 1

# Test 4.2: Follow-up with answer to slot question
echo -e "${YELLOW}Testing complete flow: Answering slot question${NC}"
make_request \
    "E2E: Slot answer - LLM2 updates params, LLM3 generates with filters" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"under 100\"}"

sleep 1

# Test 4.3: Another follow-up to refine
echo -e "${YELLOW}Testing complete flow: Refinement${NC}"
make_request \
    "E2E: Refinement - LLM2 applies delta, LLM3 updates response" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"gluten free only\"}"

# Test 4.4: Personal care complete flow
echo -e "${YELLOW}Testing complete flow: Personal care product${NC}"
NEW_USER_ID="test_user_pc_$(date +%s)"
make_request \
    "E2E: Personal care - All 3 LLMs for skincare" \
    "{\"user_id\": \"$NEW_USER_ID\", \"session_id\": \"$NEW_USER_ID\", \"message\": \"face cream for dry skin\"}"

sleep 1

make_request \
    "E2E: Personal care follow-up" \
    "{\"user_id\": \"$NEW_USER_ID\", \"session_id\": \"$NEW_USER_ID\", \"message\": \"under 500 rupees\"}"

# ============================================================================
# TEST GROUP 5: Edge Cases and Error Handling
# ============================================================================
echo -e "${GREEN}=========================================="
echo "TEST GROUP 5: Edge Cases"
echo "==========================================${NC}"
echo ""

# Test 5.1: Empty message
make_request \
    "Edge: Empty message" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"\"}" \
    400

# Test 5.2: Very long message
make_request \
    "Edge: Long message - should handle gracefully" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"$(head -c 1000 < /dev/urandom | base64)\"}" \
    200

# Test 5.3: Special characters
make_request \
    "Edge: Special characters - should handle unicode" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"मुझे नमकीन चाहिए\"}" \
    200

# Test 5.4: Ambiguous query
make_request \
    "Edge: Ambiguous query - should ask for clarification" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"something healthy\"}" \
    200

# Test 5.5: Multiple intents in one message
make_request \
    "Edge: Multiple intents - should prioritize product search" \
    "{\"user_id\": \"$USER_ID\", \"session_id\": \"$SESSION_ID\", \"message\": \"show me chips and also where is my order\"}" \
    200

echo ""
echo -e "${GREEN}=========================================="
echo "Test Suite Complete!"
echo "==========================================${NC}"
echo ""
echo "Summary:"
echo "- LLM1 (Classification): Tests 1.1 - 1.9"
echo "- LLM2 (ES Extraction): Tests 2.1 - 2.9"
echo "- LLM3 (Response Gen): Tests 3.1 - 3.5"
echo "- End-to-End: Tests 4.1 - 4.4"
echo "- Edge Cases: Tests 5.1 - 5.5"
echo ""
echo "Check the responses above to verify:"
echo "1. LLM1 correctly classifies routes, domains, and product intents"
echo "2. LLM2 correctly extracts ES parameters (q, category, filters, price)"
echo "3. LLM3 generates proper responses with summary, products, and UX"
echo "4. All 3 LLMs work together in the complete flow"
echo ""
