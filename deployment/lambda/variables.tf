variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  description = "Project name prefix for resources"
  type        = string
  default     = "shopbot"
}

variable "environment" {
  description = "Environment name (dev, staging, production)"
  type        = string
  default     = "production"
}

variable "vpc_id" {
  description = "VPC ID where Lambda will be deployed"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for Lambda"
  type        = list(string)
}

variable "redis_endpoint" {
  description = "Redis endpoint"
  type        = string
}

variable "redis_port" {
  description = "Redis port"
  type        = number
  default     = 6379
}

variable "redis_security_group_ids" {
  description = "Security group IDs for Redis"
  type        = list(string)
}

variable "secrets_manager_secret_name" {
  description = "Name of Secrets Manager secret containing API keys and passwords"
  type        = string
}

variable "secrets_manager_secret_arn" {
  description = "ARN of Secrets Manager secret"
  type        = string
}

variable "cors_origins" {
  description = "Allowed CORS origins"
  type        = list(string)
  default     = ["*"]
}

variable "log_level" {
  description = "Log level for Lambda function"
  type        = string
  default     = "INFO"
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 14
}

variable "throttling_burst_limit" {
  description = "API Gateway throttling burst limit"
  type        = number
  default     = 5000
}

variable "throttling_rate_limit" {
  description = "API Gateway throttling rate limit"
  type        = number
  default     = 2000
}

variable "lambda_timeout" {
  description = "Lambda function timeout in seconds"
  type        = number
  default     = 30
}

variable "lambda_memory_size" {
  description = "Lambda function memory size in MB"
  type        = number
  default     = 512
}

variable "lambda_reserved_concurrency" {
  description = "Reserved concurrency for Lambda function (limits concurrent executions to prevent NAT Gateway traffic spikes)"
  type        = number
  default     = 30
}

variable "vpc_endpoints_security_group_id" {
  description = "Security group ID for VPC Interface Endpoints (optional - for NAT Gateway optimization)"
  type        = string
  default     = ""
}

variable "enable_custom_domain" {
  description = "Enable custom domain (api-rs.flean.ai) and Route53."
  type        = bool
  default     = true
}

variable "es_url" {
  description = "Elasticsearch URL. When set, skips Secrets Manager lookup (for CI)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "es_api_key" {
  description = "Elasticsearch API key. When set, skips Secrets Manager lookup (for CI)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "enable_api_gateway_access_logs" {
  description = "Enable API Gateway access logging to CloudWatch. Set false for CI to avoid logs:CreateLogDelivery permission."
  type        = bool
  default     = true
}

variable "api_gateway_execution_log_level" {
  description = "HTTP API stage default_route_settings.logging_level (OFF, ERROR, or INFO). INFO triggers CloudWatch log delivery and requires logs:CreateLogDelivery; use OFF for restricted CI IAM users."
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["OFF", "ERROR", "INFO"], var.api_gateway_execution_log_level)
    error_message = "api_gateway_execution_log_level must be OFF, ERROR, or INFO."
  }
}









