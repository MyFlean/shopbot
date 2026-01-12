#!/bin/bash
set -e

echo "=== Shopbot Lambda Deployment Script ==="
echo ""

# Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LAMBDA_DIR="$SCRIPT_DIR"
PACKAGE_NAME="shopbot.zip"

cd "$PROJECT_ROOT"

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed"
    exit 1
fi

if ! command -v terraform &> /dev/null; then
    echo "❌ Error: Terraform is not installed"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo "❌ Error: AWS CLI is not installed"
    exit 1
fi

echo "✅ Prerequisites check passed"
echo ""

# Step 1: Build Lambda package
echo "Step 1: Building Lambda package..."
"$LAMBDA_DIR/build-docker.sh"

if [ ! -f "$PROJECT_ROOT/$PACKAGE_NAME" ]; then
    echo "❌ Error: Package build failed"
    exit 1
fi

echo "✅ Package built successfully"
echo ""

# Step 2: Check Terraform configuration
echo "Step 2: Checking Terraform configuration..."
cd "$LAMBDA_DIR"

if [ ! -f "terraform.tfvars" ]; then
    echo "⚠️  Warning: terraform.tfvars not found"
    echo "   Please create terraform.tfvars from terraform.tfvars.example"
    echo "   Or run: cp terraform.tfvars.example terraform.tfvars"
    exit 1
fi

echo "✅ Terraform configuration found"
echo ""

# Step 3: Initialize Terraform (if needed)
if [ ! -d ".terraform" ]; then
    echo "Step 3: Initializing Terraform..."
    terraform init
    echo "✅ Terraform initialized"
    echo ""
else
    echo "Step 3: Terraform already initialized"
    echo ""
fi

# Step 4: Plan deployment
echo "Step 4: Planning Terraform deployment..."
terraform plan -out=tfplan

if [ $? -ne 0 ]; then
    echo "❌ Error: Terraform plan failed"
    exit 1
fi

echo "✅ Terraform plan created"
echo ""

# Step 5: Confirm deployment
echo "Review the plan above. Do you want to proceed with deployment? (yes/no)"
read -r response

if [ "$response" != "yes" ]; then
    echo "Deployment cancelled"
    exit 0
fi

# Step 6: Apply deployment
echo ""
echo "Step 5: Applying Terraform deployment..."
terraform apply tfplan

if [ $? -ne 0 ]; then
    echo "❌ Error: Terraform apply failed"
    exit 1
fi

echo ""
echo "✅ Deployment completed successfully!"
echo ""

# Step 7: Get outputs
echo "Step 6: Retrieving deployment outputs..."
echo ""
echo "API Gateway URL:"
terraform output -raw api_gateway_url 2>/dev/null || echo "  (Run 'terraform output api_gateway_url' to get the URL)"
echo ""
echo "Lambda Function Name:"
terraform output -raw lambda_function_name 2>/dev/null || echo "  (Run 'terraform output lambda_function_name' to get the name)"
echo ""

echo "=== Deployment Summary ==="
echo "✅ Lambda package built"
echo "✅ Infrastructure deployed"
echo ""
echo "Next steps:"
echo "1. Test the API Gateway URL:"
echo "   curl \$(terraform output -raw api_gateway_url)/rs/health"
echo ""
echo "2. Update CloudFront/Route53 to point to the new API Gateway URL"
echo ""
echo "3. Monitor CloudWatch Logs:"
echo "   aws logs tail /aws/lambda/shopbot-service --follow"
echo ""









