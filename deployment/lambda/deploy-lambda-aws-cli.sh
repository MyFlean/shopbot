#!/usr/bin/env bash
# Deploy Shopbot Lambda function code using AWS CLI (no Terraform).
# Prerequisites: aws CLI configured, Docker (if building), shopbot.zip at repo root or run build first.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LAMBDA_FUNCTION_NAME="${LAMBDA_FUNCTION_NAME:-shopbot-service}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
PACKAGE_NAME="${PACKAGE_NAME:-shopbot.zip}"
ZIP_PATH="${1:-$PROJECT_ROOT/$PACKAGE_NAME}"

echo "=== Shopbot Lambda deploy (AWS CLI) ==="
echo "Function: $LAMBDA_FUNCTION_NAME"
echo "Region:   $AWS_REGION"
echo "Zip:      $ZIP_PATH"
echo ""

if ! command -v aws &>/dev/null; then
  echo "Error: aws CLI not found"
  exit 1
fi

if [ ! -f "$ZIP_PATH" ]; then
  echo "Zip not found; building with deployment/lambda/build-docker.sh ..."
  chmod +x "$SCRIPT_DIR/build-docker.sh"
  "$SCRIPT_DIR/build-docker.sh"
  ZIP_PATH="$PROJECT_ROOT/$PACKAGE_NAME"
fi

if [ ! -f "$ZIP_PATH" ]; then
  echo "Error: $ZIP_PATH still missing after build"
  exit 1
fi

# fileb:// requires absolute path for reliability
ZIP_ABS="$(cd "$(dirname "$ZIP_PATH")" && pwd)/$(basename "$ZIP_PATH")"

echo "Uploading to Lambda..."
aws lambda update-function-code \
  --function-name "$LAMBDA_FUNCTION_NAME" \
  --region "$AWS_REGION" \
  --zip-file "fileb://$ZIP_ABS" \
  --output json \
  --query '{FunctionName:FunctionName,LastModified:LastModified,CodeSize:CodeSize,Runtime:Runtime}'

echo ""
echo "Done. Tail logs: aws logs tail /aws/lambda/$LAMBDA_FUNCTION_NAME --follow --region $AWS_REGION"
