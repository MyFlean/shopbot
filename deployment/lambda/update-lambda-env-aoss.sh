#!/usr/bin/env bash
# Merge AOSS-related env vars into an existing Lambda (preserves other keys).
# Usage: ES_URL=https://....aoss.amazonaws.com ./update-lambda-env-aoss.sh
# Optional: SEARCH_AWS_REGION, LAMBDA_FUNCTION_NAME, AWS_REGION
set -euo pipefail

LAMBDA_FUNCTION_NAME="${LAMBDA_FUNCTION_NAME:-shopbot-service}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
SR="${SEARCH_AWS_REGION:-$AWS_REGION}"

if [ -z "${ES_URL:-}" ]; then
  echo "Error: set ES_URL to your OpenSearch Serverless collection HTTPS endpoint."
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required"
  exit 1
fi

echo "Reading current environment for $LAMBDA_FUNCTION_NAME ..."
CURRENT=$(aws lambda get-function-configuration \
  --function-name "$LAMBDA_FUNCTION_NAME" \
  --region "$AWS_REGION" \
  --query 'Environment.Variables' \
  --output json)

MERGED=$(echo "$CURRENT" | jq \
  --arg u "$ES_URL" \
  --arg r "$SR" \
  '. + {
    ES_URL: $u,
    ELASTIC_INDEX: "products_master",
    AOSS_ENABLED: "true",
    ES_USE_IAM: "true",
    SEARCH_AWS_REGION: $r
  }')

MERGED_COMPACT=$(echo "$MERGED" | jq -c .)
TMP=$(mktemp)
jq -n \
  --arg fn "$LAMBDA_FUNCTION_NAME" \
  --argjson vars "$MERGED_COMPACT" \
  '{FunctionName: $fn, Environment: {Variables: $vars}}' >"$TMP"

echo "Updating Lambda environment ..."
aws lambda update-function-configuration \
  --region "$AWS_REGION" \
  --cli-input-json "file://$TMP"

rm -f "$TMP"
echo "Done."
