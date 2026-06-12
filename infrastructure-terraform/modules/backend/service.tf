# Task definition + Fargate service.

locals {
  # ECS resolves both Secrets Manager and SSM ARNs in valueFrom at task
  # launch, so a parameter edit + rolling restart picks up the new value
  # without a re-apply.
  injected_secrets = [
    for name, arn in merge(var.injected_secret_arns, var.injected_ssm_arns) :
    { name = name, valueFrom = arn }
  ]
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "bulk-loader-${var.env_name}-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_task_cpu
  memory                   = var.ecs_task_memory
  task_role_arn            = aws_iam_role.task.arn
  execution_role_arn       = aws_iam_role.execution.arn

  container_definitions = jsonencode([
    {
      name      = "backend"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true

      # All runtime-tunable config arrives via valueFrom so values resolve at
      # task launch, never at plan time. Sensitive values from Secrets
      # Manager; non-sensitive from SSM.
      secrets = local.injected_secrets

      # Plain env vars carry only static distribution policy and the
      # migration gate: service tasks never run migrations - the one-shot
      # task in the data module handles that before each rollout.
      environment = [
        { name = "APP_DISTRIBUTION", value = "aws_hosted" },
        { name = "RUN_MIGRATIONS", value = "false" },
      ]

      portMappings = [
        { containerPort = 8000, protocol = "tcp" }
      ]

      # Container health uses /api/health/live - a process-only check.
      # Deliberately NOT /api/health/ready: a transient DB blip must not make
      # ECS kill an otherwise-healthy task that just needs the DB to recover
      # (the ALB target check handles pulling it from rotation).
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/api/health/live || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.backend.name
          "awslogs-region"        = data.aws_region.current.region
          "awslogs-stream-prefix" = "backend"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "backend" {
  name            = "bulk-loader-${var.env_name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = var.ecs_desired_count

  # Capacity provider strategy:
  #   - Bronze/Silver: 100% on-demand FARGATE.
  #   - Gold (use_fargate_spot): hybrid - one task stays on-demand so the
  #     service survives a Spot reclaim; additional tasks ride FARGATE_SPOT.
  dynamic "capacity_provider_strategy" {
    for_each = var.use_fargate_spot ? [
      { provider = "FARGATE", weight = 1, base = 1 },
      { provider = "FARGATE_SPOT", weight = 1, base = 0 },
      ] : [
      { provider = "FARGATE", weight = 1, base = 0 },
    ]

    content {
      capacity_provider = capacity_provider_strategy.value.provider
      weight            = capacity_provider_strategy.value.weight
      base              = capacity_provider_strategy.value.base
    }
  }

  # Tasks run in public subnets with public IPs so they reach the Salesforce
  # API without a NAT Gateway; the SG restricts inbound to the ALB only.
  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [var.backend_service_security_group_id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }

  # Rolling deploy: always keep at least one healthy task during updates.
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  # A bad image rollout (e.g. a boot crashloop) rolls back to the last
  # known-good task set automatically instead of hanging IN_PROGRESS.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [
    # Providers must be associated before a strategy referencing them.
    aws_ecs_cluster_capacity_providers.main,
    # The target group must be attached to a listener before the service
    # can register into it.
    aws_lb_listener.https,
  ]
}
