output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnets - ALB and Fargate tasks."
  value       = aws_subnet.public[*].id
}

output "isolated_subnet_ids" {
  description = "Isolated subnets - RDS only, no internet route."
  value       = aws_subnet.isolated[*].id
}

output "alb_security_group_id" {
  description = "Security group for the ALB (443/80 from anywhere)."
  value       = aws_security_group.alb.id
}

output "backend_service_security_group_id" {
  description = "Security group shared by all Bulk Loader ECS tasks (8000 from ALB only)."
  value       = aws_security_group.backend_service.id
}
