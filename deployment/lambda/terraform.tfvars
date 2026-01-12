# AWS Configuration
aws_region = "ap-south-1"
project_name = "shopbot"
environment = "production"

# VPC Configuration
# Same VPC as other services
vpc_id = "vpc-00b61ec91e47bf8eb"
private_subnet_ids = [
  "subnet-07728af7d9160441b",  # flean-services-private-subnet-1 (ap-south-1a)
  "subnet-0a523b7f1141d7e92"   # flean-services-private-subnet-2 (ap-south-1b)
]

# Redis Configuration
# Using the same Redis cluster as other services
redis_endpoint = "flean-services-redis.qdocji.0001.aps1.cache.amazonaws.com"
redis_port = 6379
redis_security_group_ids = [
  "sg-0fd67b4a5cb7c58a0"  # Redis security group
]

# Secrets Manager
# TODO: Create the secret first using: ./setup-secrets.sh
# Then update the ARN below after creation
# The secret should contain:
# - ANTHROPIC_API_KEY
# - ES_API_KEY (or ELASTIC_API_KEY)
# - REDIS_PASSWORD (if required)
secrets_manager_secret_name = "flean-services/shopbot"
secrets_manager_secret_arn = "arn:aws:secretsmanager:ap-south-1:637607366584:secret:flean-services/shopbot-NyNAhj"

# CORS Configuration
cors_origins = [
  "*"  # Allow all origins (adjust as needed)
]

# Logging
log_level = "DEBUG"
log_retention_days = 14

# Lambda Configuration
lambda_timeout = 60  # Increased for cold starts with VPC networking
lambda_memory_size = 512

# API Gateway Throttling
throttling_burst_limit = 5000
throttling_rate_limit = 2000

