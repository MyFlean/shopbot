terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "shopbot"
      ManagedBy   = "Terraform"
      Environment = var.environment
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Get ES_URL from Secrets Manager (skipped when es_url var is provided for CI)
data "aws_secretsmanager_secret" "es_url" {
  count = var.es_url != "" ? 0 : 1
  name  = "shopping-bot/es-url"
}

data "aws_secretsmanager_secret_version" "es_url" {
  count     = var.es_url != "" ? 0 : 1
  secret_id = data.aws_secretsmanager_secret.es_url[0].id
}

# Get ES_API_KEY from shopbot secret (skipped when es_api_key var is provided for CI)
data "aws_secretsmanager_secret_version" "shopbot_secrets" {
  count     = var.es_api_key != "" ? 0 : 1
  secret_id = var.secrets_manager_secret_arn
}

locals {
  es_url     = var.es_url != "" ? var.es_url : data.aws_secretsmanager_secret_version.es_url[0].secret_string
  es_api_key = var.es_api_key != "" ? var.es_api_key : jsondecode(data.aws_secretsmanager_secret_version.shopbot_secrets[0].secret_string)["ES_API_KEY"]
}

# Lambda function
resource "aws_lambda_function" "shopbot" {
  filename         = "${path.module}/../../shopbot.zip"
  function_name    = "${var.project_name}-service"
  role            = aws_iam_role.lambda_role.arn
  handler         = "lambda_handler.lambda_handler"
  runtime         = "python3.12"
  timeout         = var.lambda_timeout
  memory_size     = var.lambda_memory_size

  source_code_hash = filebase64sha256("${path.module}/../../shopbot.zip")

  # Enable SnapStart for faster cold starts (Python 3.12+)
  snap_start {
    apply_on = "PublishedVersions"
  }

  # Reserved concurrency to prevent uncontrolled fan-out
  # Limits concurrent executions to prevent NAT Gateway traffic spikes
  reserved_concurrent_executions = var.lambda_reserved_concurrency

  environment {
    variables = {
      FLASK_ENV              = "lambda"
      APP_ENV                = "lambda"
      # AWS_REGION is automatically available in Lambda, don't set it
      # REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB are now loaded from Secrets Manager
      # Do not set REDIS_HOST here - it will override secrets from Secrets Manager
      LOG_LEVEL             = var.log_level
      BOT_LOG_LEVEL         = var.log_level  # Smart logger system uses BOT_LOG_LEVEL
      # Secrets retrieved via Secrets Manager in code
      SECRETS_MANAGER_SECRET = var.secrets_manager_secret_name
      REDIS_SECRET_NAME     = "flean-services/redis"
      # Elasticsearch - set as environment variables for module import time
      # These are also loaded from secrets, but need to be available when modules are imported
      ES_URL                = local.es_url
      ES_API_KEY            = local.es_api_key
      ELASTIC_INDEX         = "products-v2"
      ELASTIC_TIMEOUT_SECONDS = "10"
      # AWS SDK retry configuration to prevent retry storms
      AWS_MAX_ATTEMPTS = "3"
      AWS_RETRY_MODE = "standard"
      # HTTP client timeout configuration for external APIs
      HTTP_TIMEOUT = "30"  # 30 seconds timeout for external HTTP calls
      HTTP_MAX_RETRIES = "2"  # Limit retries to prevent NAT traffic amplification
    }
  }

  # VPC config removed - Lambda runs outside VPC
  # Uses public AWS endpoints (Secrets Manager, STS, CloudWatch Logs)
  # This eliminates VPC endpoint costs: ~$14.60/month savings
  # Redis access via public endpoint (3.6.236.100)

  depends_on = [
    aws_cloudwatch_log_group.lambda_logs
  ]

  tags = {
    Name = "${var.project_name}-service"
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${var.project_name}-service"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${var.project_name}-lambda-logs"
  }
}

# Lambda permission for API Gateway
resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.shopbot.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.shopbot.execution_arn}/*/*"
}

# API Gateway HTTP API
resource "aws_apigatewayv2_api" "shopbot" {
  name          = "${var.project_name}-service-api"
  protocol_type = "HTTP"
  description   = "Shopbot Service API Gateway"

  # CORS configuration - allow all origins for CloudFront compatibility
  cors_configuration {
    allow_origins     = ["*"]
    allow_methods     = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
    allow_headers     = ["*"]
    allow_credentials = false
    max_age           = 300
  }

  tags = {
    Name = "${var.project_name}-service-api"
  }
}

# API Gateway Integration
resource "aws_apigatewayv2_integration" "shopbot" {
  api_id                 = aws_apigatewayv2_api.shopbot.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.shopbot.invoke_arn
  payload_format_version = "2.0"
  integration_method     = "POST"
}

# Catch-all route (Lambda handles routing via Flask)
resource "aws_apigatewayv2_route" "shopbot" {
  api_id    = aws_apigatewayv2_api.shopbot.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.shopbot.id}"
}

# API Gateway Stage
resource "aws_apigatewayv2_stage" "shopbot" {
  api_id      = aws_apigatewayv2_api.shopbot.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    detailed_metrics_enabled = true
    logging_level            = "INFO"  # API Gateway: ERROR, INFO, or OFF (DEBUG not available)
    throttling_burst_limit   = var.throttling_burst_limit
    throttling_rate_limit    = var.throttling_rate_limit
  }

  dynamic "access_log_settings" {
    for_each = var.enable_api_gateway_access_logs ? [1] : []
    content {
      destination_arn = aws_cloudwatch_log_group.apigw_logs.arn
      format = jsonencode({
        requestId      = "$context.requestId"
        ip             = "$context.identity.sourceIp"
        requestTime    = "$context.requestTime"
        httpMethod     = "$context.httpMethod"
        routeKey       = "$context.routeKey"
        status         = "$context.status"
        protocol       = "$context.protocol"
        responseLength = "$context.responseLength"
      })
    }
  }

  tags = {
    Name = "${var.project_name}-service-stage"
  }
}

# CloudWatch Log Group for API Gateway
resource "aws_cloudwatch_log_group" "apigw_logs" {
  name              = "/aws/apigateway/${var.project_name}-service"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${var.project_name}-apigw-logs"
  }
}

# IAM Role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-service-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = {
    Name = "${var.project_name}-lambda-role"
  }
}

# Basic execution role
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# VPC access role - REMOVED (Lambda runs outside VPC)
# resource "aws_iam_role_policy_attachment" "lambda_vpc" { ... }

# Secrets Manager access
resource "aws_iam_role_policy" "lambda_secrets" {
  name = "${var.project_name}-lambda-secrets"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ]
      Resource = concat(
        [var.secrets_manager_secret_arn],
        ["arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:flean-services/redis-*"]
      )
    }]
  })
}

# Security Group - REMOVED (Lambda runs outside VPC, no security group needed)

# Outputs are defined in outputs.tf

