#!/bin/bash

# Deploy script for ShopBot application to AWS ECS
# This script can be used locally or in CI/CD pipelines

set -e

# Configuration
AWS_REGION="ap-south-1"
ECR_REPOSITORY="shopbot"
ECS_SERVICE="shopbot-service"
ECS_CLUSTER="shopbot-cluster"
CONTAINER_NAME="shopbot"
HEALTH_URL="http://shopbot-alb-955379436.ap-south-1.elb.amazonaws.com/health"

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

# Check if required tools are installed
check_dependencies() {
    log_info "Checking dependencies..."
    
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI is not installed"
        exit 1
    fi
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi
    
    if ! command -v jq &> /dev/null; then
        log_warning "jq is not installed, JSON parsing may not work properly"
    fi
    
    log_success "All dependencies are available"
}

# Get AWS account ID
get_account_id() {
    aws sts get-caller-identity --query Account --output text
}

# Build and push Docker image
build_and_push_image() {
    local account_id=$(get_account_id)
    local ecr_registry="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com"
    local image_tag=${1:-latest}
    
    log_info "Building Docker image..."
    docker build -t ${ecr_registry}/${ECR_REPOSITORY}:${image_tag} .
    
    log_info "Pushing image to ECR..."
    aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ecr_registry}
    docker push ${ecr_registry}/${ECR_REPOSITORY}:${image_tag}
    
    log_success "Image pushed successfully: ${ecr_registry}/${ECR_REPOSITORY}:${image_tag}"
    echo "${ecr_registry}/${ECR_REPOSITORY}:${image_tag}"
}

# Update ECS task definition with new image
update_task_definition() {
    local image_uri=$1
    local task_def_file="ecs-task-definition.json"
    
    log_info "Updating task definition with new image: ${image_uri}"
    
    # Create a temporary file with updated image
    local temp_file=$(mktemp)
    jq --arg image "$image_uri" '.containerDefinitions[0].image = $image' ${task_def_file} > ${temp_file}
    
    # Register new task definition
    local new_task_def_arn=$(aws ecs register-task-definition \
        --cli-input-json file://${temp_file} \
        --query 'taskDefinition.taskDefinitionArn' \
        --output text)
    
    log_success "New task definition registered: ${new_task_def_arn}"
    echo "${new_task_def_arn}"
    
    # Clean up temporary file
    rm ${temp_file}
}

# Deploy to ECS
deploy_to_ecs() {
    local task_def_arn=$1
    
    log_info "Deploying to ECS..."
    
    # Update ECS service with new task definition
    aws ecs update-service \
        --cluster ${ECS_CLUSTER} \
        --service ${ECS_SERVICE} \
        --task-definition ${task_def_arn} \
        --query 'service.serviceName' \
        --output text
    
    log_success "ECS service update initiated"
}

# Wait for deployment to complete
wait_for_deployment() {
    log_info "Waiting for deployment to complete..."
    
    aws ecs wait services-stable \
        --cluster ${ECS_CLUSTER} \
        --services ${ECS_SERVICE}
    
    log_success "ECS service is stable"
}

# Verify deployment
verify_deployment() {
    log_info "Verifying deployment..."
    
    local max_attempts=10
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        log_info "Health check attempt $attempt/$max_attempts"
        
        if curl -f -s "${HEALTH_URL}" | grep -q "healthy"; then
            log_success "Deployment verification successful!"
            curl -s "${HEALTH_URL}" | jq . 2>/dev/null || curl -s "${HEALTH_URL}"
            return 0
        fi
        
        log_warning "Health check failed, retrying in 30 seconds..."
        sleep 30
        ((attempt++))
    done
    
    log_error "Deployment verification failed after $max_attempts attempts"
    return 1
}

# Main deployment function
deploy() {
    local image_tag=${1:-$(date +%Y%m%d-%H%M%S)}
    
    log_info "Starting deployment process..."
    log_info "Image tag: ${image_tag}"
    
    check_dependencies
    
    local image_uri=$(build_and_push_image ${image_tag})
    local task_def_arn=$(update_task_definition ${image_uri})
    
    deploy_to_ecs ${task_def_arn}
    wait_for_deployment
    verify_deployment
    
    log_success "🎉 Deployment completed successfully!"
    log_info "Application URL: ${HEALTH_URL}"
}

# Rollback function
rollback() {
    log_info "Rolling back to previous task definition..."
    
    # Get current service details
    local current_task_def=$(aws ecs describe-services \
        --cluster ${ECS_CLUSTER} \
        --services ${ECS_SERVICE} \
        --query 'services[0].taskDefinition' \
        --output text)
    
    log_info "Current task definition: ${current_task_def}"
    
    # Get previous task definition
    local previous_task_def=$(aws ecs list-task-definitions \
        --family-prefix ${ECS_TASK_DEFINITION} \
        --status ACTIVE \
        --sort DESC \
        --max-items 2 \
        --query 'taskDefinitionArns[1]' \
        --output text)
    
    if [ "${previous_task_def}" == "None" ] || [ "${previous_task_def}" == "null" ]; then
        log_error "No previous task definition found for rollback"
        exit 1
    fi
    
    log_info "Rolling back to: ${previous_task_def}"
    
    # Update service with previous task definition
    aws ecs update-service \
        --cluster ${ECS_CLUSTER} \
        --service ${ECS_SERVICE} \
        --task-definition ${previous_task_def}
    
    wait_for_deployment
    verify_deployment
    
    log_success "Rollback completed successfully!"
}

# Show usage
usage() {
    echo "Usage: $0 [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  deploy [TAG]    Deploy the application (default: timestamp)"
    echo "  rollback        Rollback to previous deployment"
    echo "  status          Show deployment status"
    echo "  health          Check application health"
    echo ""
    echo "Examples:"
    echo "  $0 deploy"
    echo "  $0 deploy v1.2.3"
    echo "  $0 rollback"
    echo "  $0 status"
}

# Show status
show_status() {
    log_info "ECS Service Status:"
    aws ecs describe-services \
        --cluster ${ECS_CLUSTER} \
        --services ${ECS_SERVICE} \
        --query 'services[0].{ServiceName:serviceName,Status:status,RunningCount:runningCount,DesiredCount:desiredCount,TaskDefinition:taskDefinition}' \
        --output table
    
    log_info "Recent Task Definitions:"
    aws ecs list-task-definitions \
        --family-prefix ${ECS_TASK_DEFINITION} \
        --status ACTIVE \
        --sort DESC \
        --max-items 5 \
        --query 'taskDefinitionArns' \
        --output table
}

# Check health
check_health() {
    log_info "Checking application health..."
    curl -s "${HEALTH_URL}" | jq . 2>/dev/null || curl -s "${HEALTH_URL}"
}

# Main script logic
case "${1:-deploy}" in
    deploy)
        deploy "$2"
        ;;
    rollback)
        rollback
        ;;
    status)
        show_status
        ;;
    health)
        check_health
        ;;
    *)
        usage
        exit 1
        ;;
esac