#!/bin/bash

# Elasticsearch Search Curl Command
# Replace these variables with your actual values:
ES_BASE_URL="${ES_URL:-${ELASTIC_BASE}}"
ES_INDEX="${ELASTIC_INDEX:-products-v2}"
ES_API_KEY="${ES_API_KEY:-${ELASTIC_API_KEY}}"
PRODUCT_KEYWORD="${1:-chips}"

# Simple curl command to search for products
curl -X POST "${ES_BASE_URL}/${ES_INDEX}/_search" \
  -H "Content-Type: application/json" \
  -H "Authorization: ApiKey ${ES_API_KEY}" \
  -d '{
    "size": 10,
    "_source": {
      "includes": [
        "id",
        "name",
        "brand",
        "price",
        "mrp",
        "description",
        "category_group",
        "category_paths",
        "hero_image.*"
      ]
    },
    "query": {
      "multi_match": {
        "query": "'"${PRODUCT_KEYWORD}"'",
        "fields": ["name^4", "description^2"],
        "type": "best_fields",
        "fuzziness": "AUTO"
      }
    }
  }' | jq '.'

# Usage: 
# ./elasticsearch_curl_example.sh chips
# Or set environment variables:
# export ES_URL="https://your-es-cluster.es.amazonaws.com"
# export ELASTIC_INDEX="products-v2"
# export ES_API_KEY="your-api-key"
# ./elasticsearch_curl_example.sh "protein powder"

