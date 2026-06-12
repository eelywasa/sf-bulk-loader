output "alb_dns_name" {
  description = "ALB DNS name (the backend_domain_name alias points here)."
  value       = aws_lb.main.dns_name
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name - target for force-new-deployment rollouts."
  value       = aws_ecs_service.backend.name
}
