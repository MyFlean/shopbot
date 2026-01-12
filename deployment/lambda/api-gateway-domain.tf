# Custom domain for API Gateway to support api.flean.ai/rs/*
# This allows API Gateway to accept requests routed from CloudFront

# Use the regional ACM certificate ARN directly
# This is the certificate created in the infra/terraform directory
locals {
  regional_cert_arn = "arn:aws:acm:ap-south-1:637607366584:certificate/21817ada-9b22-4039-8f48-cf1935eddf7b"
}

# API Gateway Domain Name
resource "aws_apigatewayv2_domain_name" "shopbot" {
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
  api_id      = aws_apigatewayv2_api.shopbot.id
  domain_name = aws_apigatewayv2_domain_name.shopbot.id
  stage       = aws_apigatewayv2_stage.shopbot.id
}

# Route53 record for the custom domain
data "aws_route53_zone" "flean_ai" {
  name         = "flean.ai"
  private_zone = false
}

resource "aws_route53_record" "api_rs_domain" {
  zone_id = data.aws_route53_zone.flean_ai.zone_id
  name    = aws_apigatewayv2_domain_name.shopbot.domain_name
  type    = "A"

  alias {
    name                   = aws_apigatewayv2_domain_name.shopbot.domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.shopbot.domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}

output "api_gateway_custom_domain" {
  description = "Custom domain for API Gateway"
  value       = aws_apigatewayv2_domain_name.shopbot.domain_name
}

output "api_gateway_domain_target" {
  description = "Target domain name for CloudFront origin"
  value       = aws_apigatewayv2_domain_name.shopbot.domain_name_configuration[0].target_domain_name
}









