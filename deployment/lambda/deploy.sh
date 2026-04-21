#!/usr/bin/env bash
# Deploy Shopbot Lambda **function code** using AWS CLI (no Terraform).
# For initial infrastructure (API Gateway, IAM, etc.), use Terraform once or create resources manually.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Shopbot Lambda deployment (AWS CLI) ==="
echo ""

if ! command -v docker &>/dev/null; then
  echo "Error: Docker is required to build the deployment package"
  exit 1
fi

if ! command -v aws &>/dev/null; then
  echo "Error: AWS CLI is required"
  exit 1
fi

chmod +x "$SCRIPT_DIR/build-docker.sh" "$SCRIPT_DIR/deploy-lambda-aws-cli.sh"
"$SCRIPT_DIR/build-docker.sh"
"$SCRIPT_DIR/deploy-lambda-aws-cli.sh"

echo ""
echo "=== Done ==="
echo "Optional: set AOSS ES_URL on the function with:"
echo "  ES_URL='https://<id>.<region>.aoss.amazonaws.com' $SCRIPT_DIR/update-lambda-env-aoss.sh"
echo "Terraform (optional, infra only): cd $SCRIPT_DIR && terraform apply"
