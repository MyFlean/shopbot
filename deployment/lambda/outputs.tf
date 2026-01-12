output "lambda_function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.shopbot.arn
}

output "lambda_function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.shopbot.function_name
}

output "lambda_function_invoke_arn" {
  description = "Invoke ARN of the Lambda function"
  value       = aws_lambda_function.shopbot.invoke_arn
}

output "api_gateway_url" {
  description = "API Gateway invoke URL"
  value       = aws_apigatewayv2_stage.shopbot.invoke_url
}

output "api_gateway_id" {
  description = "API Gateway ID"
  value       = aws_apigatewayv2_api.shopbot.id
}

output "api_gateway_execution_arn" {
  description = "API Gateway execution ARN"
  value       = aws_apigatewayv2_api.shopbot.execution_arn
}

output "lambda_role_arn" {
  description = "IAM role ARN for Lambda function"
  value       = aws_iam_role.lambda_role.arn
}

output "lambda_security_group_id" {
  description = "Security group ID for Lambda function"
  value       = aws_security_group.lambda.id
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch log group name for Lambda"
  value       = aws_cloudwatch_log_group.lambda_logs.name
}









