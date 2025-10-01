#!/bin/bash

# CI/CD Testing Script
# This script tests the CI/CD pipeline setup

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Configuration
AWS_REGION="ap-south-1"
ECR_REPOSITORY="shopbot"
ECS_SERVICE="shopbot-service"
ECS_CLUSTER="shopbot-cluster"
ECS_TASK_DEFINITION="shopbot"
CONTAINER_NAME="shopbot"

# Test functions
test_required_tools() {
    log_info "Testing required tools..."
    
    local tools=("docker" "aws" "git" "curl")
    local all_good=true
    
    for tool in "${tools[@]}"; do
        if command -v "$tool" &> /dev/null; then
            log_success "✅ $tool is installed"
        else
            log_error "❌ $tool is not installed"
            all_good=false
        fi
    done
    
    if [ "$all_good" = true ]; then
        log_success "All required tools are installed"
        return 0
    else
        log_error "Some required tools are missing"
        return 1
    fi
}

test_git_status() {
    log_info "Testing Git status..."
    
    if [ -d ".git" ]; then
        log_success "✅ Git repository initialized"
        
        local branch=$(git branch --show-current)
        log_info "Current branch: $branch"
        
        local status=$(git status --porcelain)
        if [ -z "$status" ]; then
            log_success "✅ Working directory is clean"
        else
            log_warning "⚠️  Working directory has uncommitted changes"
            echo "$status"
        fi
    else
        log_error "❌ Not a Git repository"
        return 1
    fi
}

test_github_workflow() {
    log_info "Testing GitHub Actions workflow..."
    
    if [ -f ".github/workflows/deploy.yml" ]; then
        log_success "✅ GitHub Actions workflow file exists"
        
        # Check workflow syntax
        if command -v yq &> /dev/null; then
            if yq eval '.jobs.deploy.steps' .github/workflows/deploy.yml &> /dev/null; then
                log_success "✅ Workflow syntax is valid"
            else
                log_warning "⚠️  Workflow syntax may have issues"
            fi
        else
            log_warning "⚠️  yq not installed, cannot validate workflow syntax"
        fi
    else
        log_error "❌ GitHub Actions workflow file not found"
        return 1
    fi
}

test_deployment_script() {
    log_info "Testing deployment script..."
    
    if [ -f "deploy.sh" ]; then
        log_success "✅ Deployment script exists"
        
        if [ -x "deploy.sh" ]; then
            log_success "✅ Deployment script is executable"
        else
            log_warning "⚠️  Deployment script is not executable"
            chmod +x deploy.sh
            log_info "Made deployment script executable"
        fi
        
        # Test script help
        if ./deploy.sh help &> /dev/null; then
            log_success "✅ Deployment script is functional"
        else
            log_warning "⚠️  Deployment script may have issues"
        fi
    else
        log_error "❌ Deployment script not found"
        return 1
    fi
}

test_ecs_task_definition() {
    log_info "Testing ECS task definition..."
    
    if [ -f "ecs-task-definition.json" ]; then
        log_success "✅ ECS task definition exists"
        
        # Validate JSON syntax
        if jq empty ecs-task-definition.json 2>/dev/null; then
            log_success "✅ ECS task definition JSON is valid"
        else
            log_error "❌ ECS task definition JSON is invalid"
            return 1
        fi
        
        # Check required fields
        local family=$(jq -r '.family' ecs-task-definition.json)
        local container_name=$(jq -r '.containerDefinitions[0].name' ecs-task-definition.json)
        
        if [ "$family" = "$ECS_TASK_DEFINITION" ]; then
            log_success "✅ Task definition family is correct: $family"
        else
            log_warning "⚠️  Task definition family mismatch: expected $ECS_TASK_DEFINITION, got $family"
        fi
        
        if [ "$container_name" = "$CONTAINER_NAME" ]; then
            log_success "✅ Container name is correct: $container_name"
        else
            log_warning "⚠️  Container name mismatch: expected $CONTAINER_NAME, got $container_name"
        fi
    else
        log_error "❌ ECS task definition not found"
        return 1
    fi
}

test_dockerfile() {
    log_info "Testing Dockerfile..."
    
    if [ -f "Dockerfile" ]; then
        log_success "✅ Dockerfile exists"
        
        # Check Dockerfile syntax
        if docker build --dry-run . &> /dev/null; then
            log_success "✅ Dockerfile syntax is valid"
        else
            log_warning "⚠️  Dockerfile may have syntax issues"
        fi
        
        # Check for required instructions
        if grep -q "FROM" Dockerfile; then
            log_success "✅ Dockerfile has FROM instruction"
        else
            log_error "❌ Dockerfile missing FROM instruction"
            return 1
        fi
        
        if grep -q "EXPOSE" Dockerfile; then
            log_success "✅ Dockerfile has EXPOSE instruction"
        else
            log_warning "⚠️  Dockerfile missing EXPOSE instruction"
        fi
    else
        log_error "❌ Dockerfile not found"
        return 1
    fi
}

test_aws_credentials() {
    log_info "Testing AWS credentials..."
    
    if aws sts get-caller-identity &> /dev/null; then
        log_success "✅ AWS credentials are configured"
        
        local account_id=$(aws sts get-caller-identity --query 'Account' --output text)
        local user_arn=$(aws sts get-caller-identity --query 'Arn' --output text)
        
        log_info "AWS Account ID: $account_id"
        log_info "AWS User: $user_arn"
    else
        log_error "❌ AWS credentials not configured or invalid"
        return 1
    fi
}

test_aws_resources() {
    log_info "Testing AWS resources..."
    
    # Test ECR repository
    if aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" --region "$AWS_REGION" &> /dev/null; then
        log_success "✅ ECR repository '$ECR_REPOSITORY' exists"
    else
        log_error "❌ ECR repository '$ECR_REPOSITORY' not found"
        return 1
    fi
    
    # Test ECS cluster
    if aws ecs describe-clusters --clusters "$ECS_CLUSTER" --region "$AWS_REGION" &> /dev/null; then
        log_success "✅ ECS cluster '$ECS_CLUSTER' exists"
    else
        log_error "❌ ECS cluster '$ECS_CLUSTER' not found"
        return 1
    fi
    
    # Test ECS service
    if aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --region "$AWS_REGION" &> /dev/null; then
        log_success "✅ ECS service '$ECS_SERVICE' exists"
    else
        log_error "❌ ECS service '$ECS_SERVICE' not found"
        return 1
    fi
}

test_application_health() {
    log_info "Testing application health..."
    
    # Get ALB URL from Terraform outputs
    local alb_url=$(cd terraform && terraform output -raw alb_url 2>/dev/null || echo "")
    
    if [ -n "$alb_url" ]; then
        log_info "ALB URL: $alb_url"
        
        if curl -f -s "$alb_url/health" &> /dev/null; then
            log_success "✅ Application health check passed"
        else
            log_warning "⚠️  Application health check failed"
        fi
    else
        log_warning "⚠️  Could not determine ALB URL"
    fi
}

# Main test function
run_tests() {
    log_info "🧪 Starting CI/CD pipeline tests..."
    echo ""
    
    local tests=(
        "test_required_tools"
        "test_git_status"
        "test_github_workflow"
        "test_deployment_script"
        "test_ecs_task_definition"
        "test_dockerfile"
        "test_aws_credentials"
        "test_aws_resources"
        "test_application_health"
    )
    
    local passed=0
    local failed=0
    
    for test in "${tests[@]}"; do
        echo "Running $test..."
        if $test; then
            ((passed++))
        else
            ((failed++))
        fi
        echo ""
    done
    
    echo "=========================================="
    log_info "Test Results:"
    log_success "Passed: $passed"
    if [ $failed -gt 0 ]; then
        log_error "Failed: $failed"
    else
        log_success "Failed: $failed"
    fi
    echo "=========================================="
    
    if [ $failed -eq 0 ]; then
        log_success "🎉 All tests passed! CI/CD pipeline is ready."
        return 0
    else
        log_error "❌ Some tests failed. Please fix the issues above."
        return 1
    fi
}

# Quick test function
run_quick_tests() {
    log_info "🚀 Running quick CI/CD tests..."
    echo ""
    
    test_required_tools
    test_github_workflow
    test_deployment_script
    test_ecs_task_definition
    test_dockerfile
    
    log_success "✅ Quick tests completed!"
}

# Show usage
usage() {
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  test     Run all CI/CD tests"
    echo "  quick    Run quick tests only"
    echo "  help     Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 test     # Run all tests"
    echo "  $0 quick    # Run quick tests"
}

# Parse command line arguments
case "${1:-test}" in
    test)
        run_tests
        ;;
    quick)
        run_quick_tests
        ;;
    help|--help|-h)
        usage
        exit 0
        ;;
    *)
        echo "Unknown command: $1"
        usage
        exit 1
        ;;
esac
