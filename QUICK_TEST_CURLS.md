# Quick Test Curls - Recommendation Engine

Quick reference for testing the 3 LLMs in the recommendation engine.

## Setup

```bash
export BASE_URL="http://localhost:5000"
export USER_ID="test_$(date +%s)"
export SESSION_ID="$USER_ID"
```

## Quick Tests

### LLM1: Classification

**Food Product:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","message":"I want chips"}' | jq '.response_type, .route'
```

**Personal Care:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","message":"show me shampoo"}' | jq '.response_type, .route'
```

**Support:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","message":"where is my order"}' | jq '.response_type'
```

---

### LLM2: ES Parameter Extraction

**Basic Query:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","message":"organic chips"}' | jq '.metadata, .content'
```

**With Dietary:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","message":"gluten free noodles"}' | jq '.content'
```

**With Price:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","message":"chips under 100"}' | jq '.content'
```

---

### LLM3: Response Generation

**Multiple Products (MPM):**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","message":"show me healthy snacks"}' | jq '.content.final_answer.summary_message, .content.final_answer.product_ids'
```

**Single Product (SPM):**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","message":"is Veeba ketchup good?"}' | jq '.content.final_answer'
```

---

### End-to-End Flow

**Step 1 - Initial:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","session_id":"'$USER_ID'","message":"I want chips"}' | jq
```

**Step 2 - Follow-up:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","session_id":"'$USER_ID'","message":"under 100"}' | jq
```

**Step 3 - Refine:**
```bash
curl -X POST "$BASE_URL/chat" -H "Content-Type: application/json" \
  -d '{"user_id":"'$USER_ID'","session_id":"'$USER_ID'","message":"gluten free"}' | jq
```

---

## All-in-One Test

Run all tests at once:
```bash
bash test_recommendation_engine.sh
```
