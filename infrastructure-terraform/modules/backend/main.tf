# Backend module - ECS Fargate service + ALB for the aws_hosted distribution.
# Mirrors infrastructure/lib/backend-stack.ts.
#
# TLS terminates at the ALB; the container listens on plain HTTP :8000.
# CloudFront connects to the ALB via the backend origin hostname so the
# certificate matches the origin request. WebSockets pass through (wss:// at
# the client, ws:// ALB -> container).

data "aws_region" "current" {}

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/bulk-loader/${var.env_name}/backend"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "main" {
  name = "bulk-loader-${var.env_name}"

  setting {
    name  = "containerInsights"
    value = var.container_insights_enabled ? "enabled" : "disabled"
  }
}

# Explicit capacity-provider association (the CDK does the same, SFBL-300):
# the service's capacity_provider_strategy requires the providers to be
# associated with the cluster first, and the service depends on this resource
# so create/destroy ordering is never racy.
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
  # No cluster-level default strategy - the service sets its own.
}
