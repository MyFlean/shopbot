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

# Get ES_URL from Secrets Manager
data "aws_secretsmanager_secret" "es_url" {
  name = "shopping-bot/es-url"
}

data "aws_secretsmanager_secret_version" "es_url" {
  secret_id = data.aws_secretsmanager_secret.es_url.id
}

# Get ES_API_KEY from shopbot secret
data "aws_secretsmanager_secret_version" "shopbot_secrets" {
  secret_id = var.secrets_manager_secret_arn
}

locals {
  # ES_URL is stored as a plain string, not JSON
  es_url = data.aws_secretsmanager_secret_version.es_url.secret_string
  shopbot_secrets = jsondecode(data.aws_secretsmanager_secret_version.shopbot_secrets.secret_string)
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
      REDIS_HOST            = var.redis_endpoint
      REDIS_PORT            = var.redis_port
      LOG_LEVEL             = var.log_level
      BOT_LOG_LEVEL         = var.log_level  # Smart logger system uses BOT_LOG_LEVEL
      # Secrets retrieved via Secrets Manager in code
      SECRETS_MANAGER_SECRET = var.secrets_manager_secret_name
      # Elasticsearch - set as environment variables for module import time
      # These are also loaded from secrets, but need to be available when modules are imported
      ES_URL                = local.es_url
      ES_API_KEY            = local.shopbot_secrets["ES_API_KEY"]
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

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_vpc,
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

  access_log_settings {
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

# VPC access role
resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

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
      Resource = [
        var.secrets_manager_secret_arn
      ]
    }]
  })
}

# Security Group for Lambda
resource "aws_security_group" "lambda" {
  name_prefix = "${var.project_name}-lambda-"
  description = "Security group for Shopbot Service Lambda"
  vpc_id      = var.vpc_id

  egress {
    description     = "Allow outbound to Redis"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = var.redis_security_group_ids
  }

  # Allow HTTPS to VPC Interface Endpoints
  # This eliminates NAT Gateway traffic for AWS service calls (Secrets Manager, STS, CloudWatch Logs)
  egress {
    description     = "HTTPS to VPC Interface Endpoints (Secrets Manager, STS, CloudWatch Logs, Lambda)"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [data.aws_security_group.vpc_endpoints.id]
  }

  # Allow HTTPS to external services (Elasticsearch, Anthropic API)
  # This will still use NAT Gateway but is necessary for functionality
  egress {
    description = "HTTPS for external APIs (Elasticsearch, Anthropic)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow HTTP for Elasticsearch (if not using HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-lambda-sg"
  }
}

# Outputs are defined in outputs.tf

