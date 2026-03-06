# Custom domain for API Gateway to support api.flean.ai/rs/*
# This allows API Gateway to accept requests routed from CloudFront
# Set enable_custom_domain = false when CI user lacks Route53 permissions

# Use the regional ACM certificate ARN directly
# This is the certificate created in the infra/terraform directory
locals {
  regional_cert_arn = "arn:aws:acm:ap-south-1:637607366584:certificate/21817ada-9b22-4039-8f48-cf1935eddf7b"
}

# API Gateway Domain Name
resource "aws_apigatewayv2_domain_name" "shopbot" {
  count       = var.enable_custom_domain ? 1 : 0
  domain_name = "api-rs.flean.ai"

  domain_name_configuration {
    certificate_arn = local.regional_cert_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }

  tags = {
    Name = "${var.project_name}-api-domain"
  }
}

# API Mapping - maps the custom domain to the API Gateway stage
resource "aws_apigatewayv2_api_mapping" "shopbot" {
  count       = var.enable_custom_domain ? 1 : 0
  api_id      = aws_apigatewayv2_api.shopbot.id
  domain_name = aws_apigatewayv2_domain_name.shopbot[0].id
  stage       = aws_apigatewayv2_stage.shopbot.id
}

# Route53 record for the custom domain
data "aws_route53_zone" "flean_ai" {
  count        = var.enable_custom_domain ? 1 : 0
  name         = "flean.ai"
  private_zone = false
}

resource "aws_route53_record" "api_rs_domain" {
  count   = var.enable_custom_domain ? 1 : 0
  zone_id = data.aws_route53_zone.flean_ai[0].zone_id
  name    = aws_apigatewayv2_domain_name.shopbot[0].domain_name
  type    = "A"

  alias {
    name                   = aws_apigatewayv2_domain_name.shopbot[0].domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.shopbot[0].domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}

output "api_gateway_custom_domain" {
  description = "Custom domain for API Gateway"
  value       = var.enable_custom_domain ? aws_apigatewayv2_domain_name.shopbot[0].domain_name : ""
}

output "api_gateway_domain_target" {
  description = "Target domain name for CloudFront origin"
  value       = var.enable_custom_domain ? aws_apigatewayv2_domain_name.shopbot[0].domain_name_configuration[0].target_domain_name : ""
}

















