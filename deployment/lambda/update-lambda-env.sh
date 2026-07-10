#!/usr/bin/env bash
# Merge non-secret env vars from lambda-env.json into an existing Lambda function.
# Preserves all other keys (including SECRETS_MANAGER_SECRET, REDIS_SECRET_NAME).
#
# Usage:
#   ./update-lambda-env.sh
#   ES_URL=https://.... ./update-lambda-env.sh   # also merge OpenSearch keys
#
# Optional env: LAMBDA_FUNCTION_NAME, AWS_REGION, SEARCH_AWS_REGION, ES_URL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${SCRIPT_DIR}/lambda-env.json"
LAMBDA_FUNCTION_NAME="${LAMBDA_FUNCTION_NAME:-shopbot-service}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
SR="${SEARCH_AWS_REGION:-$AWS_REGION}"

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required"
  exit 1
fi

if [ ! -f "$MANIFEST" ]; then
  echo "Error: manifest not found: $MANIFEST"
  exit 1
fi

echo "Reading current environment for $LAMBDA_FUNCTION_NAME ..."
CURRENT=$(aws lambda get-function-configuration \
  --function-name "$LAMBDA_FUNCTION_NAME" \
  --region "$AWS_REGION" \
  --query 'Environment.Variables' \
  --output json)

echo "Merging manifest from $MANIFEST ..."
MERGED=$(jq -s '.[0] * .[1]' <(echo "$CURRENT") "$MANIFEST")

# Optional CI override — only apply when ES_URL looks like a real endpoint (not a doc placeholder).
_is_valid_es_url() {
  local url="$1"
  case "$url" in
    https://*.*.*) ;;
    *) return 1 ;;
  esac
  case "$url" in
    *....*) return 1 ;;
    *your-*) return 1 ;;
    *example.*) return 1 ;;
  esac
  return 0
}

if [ -n "${ES_URL:-}" ]; then
  if _is_valid_es_url "$ES_URL"; then
    echo "Merging OpenSearch keys from ES_URL ..."
    MERGED=$(echo "$MERGED" | jq \
      --arg u "$ES_URL" \
      --arg r "$SR" \
      '. + {
        ES_URL: $u,
        SEARCH_V2_ES_URL: $u,
        ELASTIC_INDEX: "products_master",
        AOSS_ENABLED: "true",
        ES_USE_IAM: "true",
        SEARCH_AWS_REGION: $r
      }')
  else
    echo "WARNING: ES_URL looks like a placeholder; keeping manifest/Terraform values instead."
    echo "         Fix GitHub secret ES_URL or omit it to use lambda-env.json."
  fi
fi

MERGED_COMPACT=$(echo "$MERGED" | jq -c .)
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

jq -n \
  --arg fn "$LAMBDA_FUNCTION_NAME" \
  --argjson vars "$MERGED_COMPACT" \
  '{FunctionName: $fn, Environment: {Variables: $vars}}' >"$TMP"

echo "Updating Lambda environment ..."
aws lambda update-function-configuration \
  --region "$AWS_REGION" \
  --cli-input-json "file://$TMP"

echo "Done."
