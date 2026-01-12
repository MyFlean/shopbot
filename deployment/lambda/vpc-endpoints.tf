# VPC Endpoints Configuration
# ───────────────────────────────────────────────────────────────
# This file configures access to existing VPC endpoints to eliminate NAT Gateway traffic
# for AWS service calls from Lambda functions in private subnets.
#
# Note: The Secrets Manager VPC endpoint already exists in this VPC.
# This configuration ensures the Lambda security group can access it.

# ───────────────────────────────────────────────────────────────
# Data Source: Existing Secrets Manager VPC Endpoint
# ───────────────────────────────────────────────────────────────
data "aws_vpc_endpoint" "secretsmanager" {
  vpc_id       = var.vpc_id
  service_name = "com.amazonaws.${data.aws_region.current.name}.secretsmanager"
  state        = "available"
}

# ───────────────────────────────────────────────────────────────
# Data Source: Security Group for VPC Endpoint
# ───────────────────────────────────────────────────────────────
# Get the security group from the VPC endpoint's network interfaces
data "aws_network_interfaces" "vpc_endpoint" {
  filter {
    name   = "description"
    values = ["*${data.aws_vpc_endpoint.secretsmanager.id}*"]
  }
}

data "aws_network_interface" "vpc_endpoint" {
  id = tolist(data.aws_network_interfaces.vpc_endpoint.ids)[0]
}

data "aws_security_group" "vpc_endpoints" {
  id = tolist(data.aws_network_interface.vpc_endpoint.security_groups)[0]
}

# ───────────────────────────────────────────────────────────────
# Security Group Rule: Allow Lambda to access existing VPC endpoint
# ───────────────────────────────────────────────────────────────
# Add an ingress rule to allow the Lambda security group to access the VPC endpoint
resource "aws_security_group_rule" "vpc_endpoints_ingress_from_lambda" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.lambda.id
  security_group_id        = data.aws_security_group.vpc_endpoints.id
  description              = "HTTPS from Shopbot Service Lambda"
}

# ───────────────────────────────────────────────────────────────
# Outputs
# ───────────────────────────────────────────────────────────────
output "vpc_endpoint_secretsmanager_id" {
  description = "Secrets Manager VPC Endpoint ID"
  value       = data.aws_vpc_endpoint.secretsmanager.id
}

output "vpc_endpoint_secretsmanager_security_group_id" {
  description = "Security Group ID for VPC Endpoints"
  value       = data.aws_security_group.vpc_endpoints.id
}

