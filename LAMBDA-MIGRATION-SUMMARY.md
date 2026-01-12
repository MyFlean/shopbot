# Shopbot Service Lambda Migration - Complete Summary

## Overview

This document summarizes all changes made to migrate the Shopbot Service from ECS/ALB deployment to AWS Lambda with API Gateway. The migration enables serverless architecture with automatic scaling and reduced operational overhead, following the successful migration patterns from UserService and UI-Service.

## Migration Date
January 2025

## Architecture Changes

### Before (ECS/ALB)
```
CloudFront → ALB → ECS Fargate → Redis
                              → Elasticsearch
                              → Anthropic API
```

### After (Lambda)
```
CloudFront → API Gateway HTTP API → Lambda Function → Redis
                                                  → Elasticsearch
                                                  → Anthropic API
```

## Key Changes Summary

### 1. Application Code Changes

#### `lambda_handler.py` (NEW)
- Main entry point for Lambda function
- Handles API Gateway HTTP API events
- Retrieves secrets from AWS Secrets Manager
- Uses `serverless-wsgi` to adapt Flask app for Lambda
- Configures AWS Lambda Powertools for logging, tracing, and metrics

**Key Features:**
- Lazy secret loading (only when needed)
- Environment variable setup from Secrets Manager
- X-Ray tracing support
- Proper error handling
- Request/response logging and metrics

#### `shopping_bot/__init__.py` (MODIFIED)
- Added Lambda-specific configuration support
- Set `instance_path` to `/tmp` for Lambda's writable filesystem
- Made Redis connection lazy to avoid cold start timeouts
- Made bot_core initialization lazy in Lambda
- Added `before_request` handler to ensure components are initialized

**Key Changes:**
```python
# Lambda-specific instance path
if config_name == 'lambda':
    app = Flask(__name__, instance_path=tempfile.gettempdir())

# Lazy Redis connection in Lambda
if config_name == 'lambda':
    app.extensions["ctx_mgr"] = None  # Will be initialized on first access
    app.extensions["bot_core"] = None  # Will be initialized on first access
```

**Helper Functions:**
- `_get_or_init_redis(app)`: Lazy Redis initialization
- `_get_or_init_bot_core(app)`: Lazy bot_core initialization

#### `shopping_bot/config.py` (MODIFIED)
- Added `LambdaConfig` class with Lambda-specific settings
- Updated `get_config()` to support 'lambda' configuration name
- Lambda config inherits from `ProductionConfig`

**Key Changes:**
- Lambda config uses production settings with lazy initialization
- No database connection pooling needed (shopbot doesn't use RDS)

#### `shopping_bot/routes/chat.py` (MODIFIED)
- Updated to handle lazy initialization in Lambda
- Added fallback initialization if components are None
- Improved error handling for missing extensions

#### `requirements.txt` (MODIFIED)
- Added `serverless-wsgi>=0.8.2` for WSGI adapter
- Added `aws-lambda-powertools>=2.40.0` for Lambda utilities
- Added `boto3>=1.34.0` for Secrets Manager access
- Kept `gunicorn` and `hypercorn` for local development

### 2. Deployment Infrastructure

#### `deployment/lambda/` (NEW DIRECTORY)

**Terraform Configuration:**
- `main.tf`: Lambda function, API Gateway, IAM roles, security groups
- `variables.tf`: Input variable definitions
- `outputs.tf`: Output values (API Gateway URL, Lambda ARN, etc.)
- `Dockerfile.build`: Dockerfile for building Lambda package in Linux environment

**Key Infrastructure Components:**
1. **Lambda Function**
   - Runtime: Python 3.12
   - Memory: 512 MB (configurable)
   - Timeout: 30 seconds (configurable)
   - VPC configuration for Redis access
   - Environment variables for configuration
   - Secrets Manager integration

2. **API Gateway HTTP API**
   - HTTP API (v2.0) for better performance and lower cost
   - CORS configuration for frontend access
   - Catch-all route (`$default`) - Lambda handles routing via Flask
   - Auto-deploy enabled
   - Throttling configured

3. **IAM Role & Policies**
   - Basic execution role (CloudWatch Logs)
   - VPC access role (for Redis connectivity)
   - Secrets Manager read access
   - No RDS access needed (shopbot doesn't use RDS)

4. **Security Group**
   - Egress to Redis (port 6379)
   - Egress to HTTPS (port 443) for external APIs (Elasticsearch, Anthropic)
   - Egress to HTTP (port 80) for Elasticsearch fallback

5. **CloudWatch Logs**
   - Lambda function logs
   - API Gateway access logs
   - Configurable retention period

## Key Differences from UserService/UI-Service

### Framework
- **Shopbot**: Flask (WSGI) → Uses `serverless-wsgi` (same as UserService)
- **UI-Service**: FastAPI (ASGI) → Uses `mangum`

### Dependencies
- **Shopbot**: No database (RDS), uses Redis and Elasticsearch
- **UserService**: Uses RDS MySQL and Redis
- **UI-Service**: Uses MongoDB and Redis

### Initialization Strategy
- **Shopbot**: Lazy initialization for both Redis and bot_core to minimize cold starts
- **UserService**: Lazy Redis, immediate database connection
- **UI-Service**: Lazy Redis, immediate MongoDB connection

### External Services
- **Shopbot**: Elasticsearch (external API), Anthropic API
- **UserService**: RDS (VPC), Redis (VPC)
- **UI-Service**: MongoDB (external), Redis (VPC)

## Environment Variables

### Lambda Environment Variables
- `FLASK_ENV=lambda`
- `APP_ENV=lambda`
- `REDIS_HOST` (from Terraform variable)
- `REDIS_PORT` (from Terraform variable)
- `LOG_LEVEL` (from Terraform variable)
- `SECRETS_MANAGER_SECRET` (from Terraform variable)

### Secrets Manager
The following secrets should be stored in AWS Secrets Manager:
- `ANTHROPIC_API_KEY`: Anthropic API key for Claude
- `ES_API_KEY` or `ELASTIC_API_KEY`: Elasticsearch API key (if required)
- `REDIS_PASSWORD`: Redis password (if required)
- Any other API keys or sensitive configuration

## Deployment Steps

### 1. Build Lambda Package

```bash
cd /Users/anuj/shopbot
docker build -f deployment/lambda/Dockerfile.build -t shopbot-lambda-build .
docker run --rm -v $(pwd):/output shopbot-lambda-build cp /build/shopbot.zip /output/
```

This creates `shopbot.zip` in the project root.

### 2. Configure Terraform Variables

Create `deployment/lambda/terraform.tfvars`:

```hcl
aws_region = "ap-south-1"
project_name = "shopbot"
environment = "production"

vpc_id = "vpc-xxxxx"
private_subnet_ids = ["subnet-xxxxx", "subnet-yyyyy"]

redis_endpoint = "your-redis-endpoint.cache.amazonaws.com"
redis_port = 6379
redis_security_group_ids = ["sg-xxxxx"]

secrets_manager_secret_name = "flean-services/shopbot"
secrets_manager_secret_arn = "arn:aws:secretsmanager:ap-south-1:xxxxx:secret:flean-services/shopbot-xxxxx"

log_level = "INFO"
log_retention_days = 14
lambda_timeout = 30
lambda_memory_size = 512
```

### 3. Deploy Infrastructure

```bash
cd deployment/lambda
terraform init
terraform plan
terraform apply
```

### 4. Update Secrets Manager

Ensure the secret `flean-services/shopbot` contains:
```json
{
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "ES_API_KEY": "...",
  "REDIS_PASSWORD": "..."
}
```

### 5. Update CloudFront/Route53

Update your CloudFront distribution or Route53 records to point to the new API Gateway URL (available in Terraform outputs).

## Testing

### Local Testing
```bash
# Test with SAM Local or Lambda runtime interface emulator
# Or test Flask app directly
export FLASK_ENV=lambda
export APP_ENV=lambda
export REDIS_HOST=your-redis-host
python run.py
```

### Lambda Testing
1. Use API Gateway test console
2. Use `aws lambda invoke` command
3. Test via CloudFront/API Gateway URL

## Monitoring

### CloudWatch Metrics
- Lambda invocations
- Lambda errors
- Lambda duration
- API Gateway requests
- API Gateway 4xx/5xx errors

### CloudWatch Logs
- Lambda function logs: `/aws/lambda/shopbot-service`
- API Gateway logs: `/aws/apigateway/shopbot-service`

### AWS X-Ray
- Distributed tracing enabled via Lambda Powertools
- Trace requests through API Gateway → Lambda → External APIs

## Cost Comparison

### Before (ECS)
- ECS Fargate: ~$30-40/month per task (24/7)
- ALB: ~$20/month
- **Total**: ~$50-60/month minimum

### After (Lambda)
- Lambda: Pay per request (~$0.20 per 1M requests)
- API Gateway: ~$1.00 per 1M requests
- **Total**: ~$0-5/month for low-medium traffic

**Savings**: ~90% cost reduction for low-medium traffic workloads

## Rollback Plan

If issues occur:
1. Keep ECS service running in parallel initially
2. Route traffic back to ALB via CloudFront/Route53
3. Investigate Lambda logs and metrics
4. Fix issues and redeploy

## Known Limitations

1. **Cold Starts**: First request after idle period may take 2-5 seconds
   - Mitigation: Lazy initialization, provisioned concurrency (if needed)

2. **Timeout**: 30 seconds default (configurable up to 15 minutes)
   - For long-running operations, consider async processing

3. **Memory**: 512 MB default (configurable up to 10 GB)
   - Monitor memory usage and adjust as needed

4. **VPC**: Lambda in VPC has additional cold start penalty
   - Consider VPC endpoints for AWS services if needed

## Next Steps

1. ✅ Lambda handler created
2. ✅ Application code updated for Lambda
3. ✅ Terraform infrastructure created
4. ✅ Dockerfile.build created
5. ⏳ Deploy to staging environment
6. ⏳ Test thoroughly
7. ⏳ Deploy to production
8. ⏳ Monitor and optimize

## References

- [UserService Lambda Migration](./../User-Service/LAMBDA-MIGRATION-SUMMARY.md)
- [UI-Service Lambda Migration](./../UI-Service/LAMBDA-MIGRATION-SUMMARY.md)
- [AWS Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [Serverless WSGI Documentation](https://github.com/logandk/serverless-wsgi)
- [AWS Lambda Powertools](https://docs.aws.amazon.com/lambda/latest/dg/python-powertools.html)









