output "ecr_repository_url" {
  description = "ECR Repository URL"
  value       = aws_ecr_repository.app.repository_url
}

output "ecr_repository_arn" {
  description = "ECR Repository ARN"
  value       = aws_ecr_repository.app.arn
}

output "load_balancer_dns" {
  description = "Application Load Balancer DNS name"
  value       = aws_lb.app.dns_name
}

output "load_balancer_url" {
  description = "Application URL"
  value       = "https://${aws_lb.app.dns_name}"
}

output "load_balancer_zone_id" {
  description = "Application Load Balancer Zone ID"
  value       = aws_lb.app.zone_id
}

output "ecs_cluster_name" {
  description = "ECS Cluster Name"
  value       = aws_ecs_cluster.app.name
}

output "ecs_cluster_arn" {
  description = "ECS Cluster ARN"
  value       = aws_ecs_cluster.app.arn
}

output "ecs_service_name" {
  description = "ECS Service Name"
  value       = aws_ecs_service.app.name
}

output "ecs_service_arn" {
  description = "ECS Service ARN"
  value       = aws_ecs_service.app.id
}

output "target_group_arn" {
  description = "Target Group ARN"
  value       = aws_lb_target_group.app.arn
}

output "security_group_alb_id" {
  description = "ALB Security Group ID"
  value       = aws_security_group.alb.id
}

output "security_group_ecs_id" {
  description = "ECS Security Group ID"
  value       = aws_security_group.ecs.id
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch Log Group Name"
  value       = aws_cloudwatch_log_group.app.name
}

output "task_execution_role_arn" {
  description = "ECS Task Execution Role ARN"
  value       = aws_iam_role.task_execution.arn
}

output "task_role_arn" {
  description = "ECS Task Role ARN"
  value       = aws_iam_role.task.arn
}
