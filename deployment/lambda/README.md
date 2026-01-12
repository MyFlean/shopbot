# Shopbot Service Lambda Deployment

This directory contains Terraform configuration and build scripts for deploying Shopbot Service as AWS Lambda functions with API Gateway.

## Prerequisites

1. **AWS CLI** configured with appropriate credentials
2. **Terraform** >= 1.5.0
3. **Docker** (for building Lambda package)
4. **Python** 3.12 (for local development)

## Architecture

```
API Gateway (HTTP API)
    ↓
Lambda Function (Python 3.12)
    ↓
Redis (ElastiCache)
Elasticsearch (External API)
Anthropic API (External API)
```

## Quick Start

### 1. Build Lambda Package

```bash
cd /Users/anuj/shopbot
./deployment/lambda/build-docker.sh
```

This creates `shopbot.zip` in the project root.

### 2. Configure Variables

Copy the example file and update with your values:

```bash
cd deployment/lambda
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your specific values
```

**Required Configuration:**
- VPC ID and subnet IDs (same as other services)
- Redis endpoint and security group
- Secrets Manager secret name and ARN

### 3. Create Secrets Manager Secret

Before deploying, create the secret in AWS Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name flean-services/shopbot \
  --description "Shopbot service secrets" \
  --secret-string '{
    "ANTHROPIC_API_KEY": "sk-ant-...",
    "ES_API_KEY": "...",
    "REDIS_PASSWORD": "..."
  }'
```

Get the ARN and update `terraform.tfvars`:

```bash
aws secretsmanager describe-secret --secret-id flean-services/shopbot --query ARN --output text
```

### 4. Deploy Infrastructure

```bash
cd deployment/lambda
terraform init
terraform plan
terraform apply
```

### 5. Get API Gateway URL

```bash
terraform output api_gateway_url
```

Example output:
```
https://xxxxxxxxxx.execute-api.ap-south-1.amazonaws.com
```

### 6. Test the Deployment

```bash
# Test health endpoint
curl https://xxxxxxxxxx.execute-api.ap-south-1.amazonaws.com/rs/health

# Test chat endpoint
curl -X POST https://xxxxxxxxxx.execute-api.ap-south-1.amazonaws.com/rs/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user",
    "message": "show me protein bars"
  }'
```

## File Structure

```
deployment/lambda/
├── main.tf                  # Main Terraform configuration
├── variables.tf             # Variable definitions
├── outputs.tf               # Output values
├── terraform.tfvars.example # Example variable values
├── terraform.tfvars         # Your variable values (create this)
├── Dockerfile.build         # Dockerfile for building Lambda package
├── build-docker.sh          # Build script for Lambda package
└── README.md                # This file
```

## Configuration

### Lambda Function

- **Runtime**: Python 3.12
- **Memory**: 512 MB (configurable via `lambda_memory_size`)
- **Timeout**: 30 seconds (configurable via `lambda_timeout`)
- **Handler**: `lambda_handler.lambda_handler`

### API Gateway

- **Type**: HTTP API (v2.0)
- **Protocol**: HTTP/HTTPS
- **CORS**: Configurable via `cors_origins` variable
- **Throttling**: Configurable via `throttling_burst_limit` and `throttling_rate_limit`

### Environment Variables

The Lambda function receives these environment variables:

- `FLASK_ENV=lambda`
- `APP_ENV=lambda`
- `REDIS_HOST` (from Terraform variable)
- `REDIS_PORT` (from Terraform variable)
- `LOG_LEVEL` (from Terraform variable)
- `SECRETS_MANAGER_SECRET` (from Terraform variable)

Additional secrets are retrieved from AWS Secrets Manager at runtime.

## Secrets Manager

The Lambda function retrieves secrets from AWS Secrets Manager. The secret should contain:

```json
{
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "ES_API_KEY": "...",
  "ELASTIC_API_KEY": "...",
  "REDIS_PASSWORD": "..."
}
```

## Monitoring

### CloudWatch Logs

- Lambda function logs: `/aws/lambda/shopbot-service`
- API Gateway logs: `/aws/apigateway/shopbot-service`

### CloudWatch Metrics

- Lambda invocations
- Lambda errors
- Lambda duration
- API Gateway requests
- API Gateway 4xx/5xx errors

### AWS X-Ray

Distributed tracing is enabled via AWS Lambda Powertools. Traces include:
- API Gateway → Lambda
- Lambda → Redis
- Lambda → External APIs (Elasticsearch, Anthropic)

## Updating the Deployment

### Update Lambda Code

1. Make code changes
2. Rebuild package: `./deployment/lambda/build-docker.sh`
3. Apply Terraform: `terraform apply`

Terraform will detect the new package hash and update the Lambda function.

### Update Configuration

1. Edit `terraform.tfvars`
2. Run `terraform plan` to preview changes
3. Run `terraform apply` to apply changes

## Troubleshooting

### Lambda Timeout

If requests are timing out:
1. Increase `lambda_timeout` in `terraform.tfvars`
2. Check CloudWatch Logs for slow operations
3. Consider optimizing Redis queries or external API calls

### Cold Start Issues

Cold starts can take 2-5 seconds:
1. Lazy initialization is already implemented
2. Consider provisioned concurrency for production
3. Monitor cold start metrics in CloudWatch

### Redis Connection Issues

If Redis connection fails:
1. Verify security group allows Lambda → Redis (port 6379)
2. Check VPC configuration (subnets, route tables)
3. Verify Redis endpoint is correct
4. Check CloudWatch Logs for connection errors

### Secrets Manager Issues

If secrets retrieval fails:
1. Verify IAM role has `secretsmanager:GetSecretValue` permission
2. Check secret name matches `SECRETS_MANAGER_SECRET` environment variable
3. Verify secret ARN in Terraform configuration

## Cost Optimization

### Current Configuration
- Lambda: Pay per request (~$0.20 per 1M requests)
- API Gateway: ~$1.00 per 1M requests
- CloudWatch Logs: ~$0.50 per GB ingested

### Optimization Tips
1. Adjust log retention period (`log_retention_days`)
2. Use CloudWatch Logs Insights for querying (cheaper than full retention)
3. Monitor Lambda memory usage and adjust if needed
4. Consider Lambda Layers for shared dependencies

## Rollback

If you need to rollback:

1. Keep ECS service running in parallel initially
2. Update CloudFront/Route53 to point back to ALB
3. Investigate issues in CloudWatch Logs
4. Fix and redeploy

## Next Steps

After successful deployment:

1. ✅ Update CloudFront/Route53 to point to API Gateway
2. ✅ Monitor CloudWatch metrics and logs
3. ✅ Test all endpoints thoroughly
4. ✅ Update documentation with new API Gateway URL
5. ⏳ Consider decommissioning ECS service after validation period

## References

- [Lambda Migration Summary](../../LAMBDA-MIGRATION-SUMMARY.md)
- [UserService Lambda Deployment](../User-Service/deployment/lambda/README.md)
- [UI-Service Lambda Deployment](../UI-Service/deployment/lambda/README.md)
- [AWS Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [Serverless WSGI Documentation](https://github.com/logandk/serverless-wsgi)









