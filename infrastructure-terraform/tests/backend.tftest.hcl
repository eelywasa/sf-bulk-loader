# Backend module assertions (SFBL-382 ACs), including the review-driven
# falsification clauses: IAM direction (execution role reads config, task
# role does not), gold Fargate Spot hybrid, health-check paths.
#
# SFBL-388 modifies the SFBL-382 posture: the task role now carries scoped
# first-party S3 grants (object RW + ListBucket on exactly the two bucket
# ARNs). The previous "task role has no S3 grants" structural absence check is
# REPLACED below by a positive, scoped assertion plus an execution-role-no-S3
# falsification clause - so the grant cannot silently widen to a wildcard and
# cannot drift onto the execution role.

mock_provider "aws" {
  # The provider validates policy JSON even under mocks - pin a valid doc.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  # ARN-typed arguments are validated even under mocks - pin well-formed ARNs.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::111122223333:role/mock-role"
    }
  }

  mock_resource "aws_lb" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:eu-west-1:111122223333:loadbalancer/app/mock/0123456789abcdef"
    }
  }

  mock_resource "aws_lb_target_group" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:eu-west-1:111122223333:targetgroup/mock/0123456789abcdef"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:eu-west-1:111122223333:log-group:/mock"
    }
  }

  mock_data "aws_region" {
    defaults = {
      region = "eu-west-1"
    }
  }

  mock_data "aws_route53_zone" {
    defaults = {
      zone_id = "Z0123456789ABC"
    }
  }
}

variables {
  env_name                          = "test"
  vpc_id                            = "vpc-0123456789abcdef0"
  public_subnet_ids                 = ["subnet-pub-a", "subnet-pub-b"]
  alb_security_group_id             = "sg-alb0123"
  backend_service_security_group_id = "sg-backend0123"
  ecr_repository_url                = "111122223333.dkr.ecr.eu-west-1.amazonaws.com/bulk-loader-backend-test"
  image_tag                         = "stable"
  injected_secret_arns = {
    ENCRYPTION_KEY = "arn:aws:secretsmanager:eu-west-1:111122223333:secret:/test/bulk-loader/encryption-key-AbCdEf"
    JWT_SECRET_KEY = "arn:aws:secretsmanager:eu-west-1:111122223333:secret:/test/bulk-loader/jwt-secret-key-AbCdEf"
    DATABASE_URL   = "arn:aws:secretsmanager:eu-west-1:111122223333:secret:/test/bulk-loader/database-url-AbCdEf"
    ADMIN_EMAIL    = "arn:aws:secretsmanager:eu-west-1:111122223333:secret:/test/bulk-loader/admin-email-AbCdEf"
    ADMIN_PASSWORD = "arn:aws:secretsmanager:eu-west-1:111122223333:secret:/test/bulk-loader/admin-password-AbCdEf"
  }
  injected_ssm_arns = {
    CORS_ORIGINS       = "arn:aws:ssm:eu-west-1:111122223333:parameter/test/bulk-loader/cors-origins"
    LOG_LEVEL          = "arn:aws:ssm:eu-west-1:111122223333:parameter/test/bulk-loader/log-level"
    ADMIN_USERNAME     = "arn:aws:ssm:eu-west-1:111122223333:parameter/test/bulk-loader/admin-username"
    EMAIL_FROM_ADDRESS = "arn:aws:ssm:eu-west-1:111122223333:parameter/test/bulk-loader/email-from-address"
    EMAIL_SES_REGION   = "arn:aws:ssm:eu-west-1:111122223333:parameter/test/bulk-loader/email-ses-region"
  }
  ses_identity_arn           = "arn:aws:ses:eu-west-1:111122223333:identity/example.com"
  input_bucket_name          = "bulk-loader-test-input"
  output_bucket_name         = "bulk-loader-test-output"
  input_bucket_arn           = "arn:aws:s3:::bulk-loader-test-input"
  output_bucket_arn          = "arn:aws:s3:::bulk-loader-test-output"
  backend_domain_name        = "api.bulk-loader.example.com"
  backend_certificate_arn    = "arn:aws:acm:eu-west-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  hosted_zone_domain         = "example.com"
  ecs_desired_count          = 1
  ecs_task_cpu               = 512
  ecs_task_memory            = 1024
  log_retention_days         = 7
  container_insights_enabled = false
  use_fargate_spot           = false
}

run "task_definition_runtime_contract" {
  command = apply

  module {
    source = "./modules/backend"
  }

  assert {
    condition     = length(jsondecode(aws_ecs_task_definition.backend.container_definitions)[0].secrets) == 10
    error_message = "All ten secret/SSM values must be injected - a missing one breaks boot or CORS/email."
  }

  assert {
    condition = alltrue([
      anytrue([for e in jsondecode(aws_ecs_task_definition.backend.container_definitions)[0].environment : e.name == "APP_DISTRIBUTION" && e.value == "aws_hosted"]),
      anytrue([for e in jsondecode(aws_ecs_task_definition.backend.container_definitions)[0].environment : e.name == "RUN_MIGRATIONS" && e.value == "false"]),
    ])
    error_message = "Service tasks must set APP_DISTRIBUTION=aws_hosted and RUN_MIGRATIONS=false."
  }

  # SFBL-388: the three first-party S3 bucket env vars are injected, bound to
  # the buckets and the deploy region.
  assert {
    condition = alltrue([
      anytrue([for e in jsondecode(aws_ecs_task_definition.backend.container_definitions)[0].environment : e.name == "S3_INPUT_BUCKET" && e.value == var.input_bucket_name]),
      anytrue([for e in jsondecode(aws_ecs_task_definition.backend.container_definitions)[0].environment : e.name == "S3_OUTPUT_BUCKET" && e.value == var.output_bucket_name]),
      anytrue([for e in jsondecode(aws_ecs_task_definition.backend.container_definitions)[0].environment : e.name == "S3_BUCKET_REGION" && e.value == "eu-west-1"]),
    ])
    error_message = "Backend task must inject S3_INPUT_BUCKET/S3_OUTPUT_BUCKET/S3_BUCKET_REGION bound to the first-party buckets."
  }

  # Container liveness uses /live - /ready here would let a DB blip kill
  # healthy tasks.
  assert {
    condition     = strcontains(join(" ", jsondecode(aws_ecs_task_definition.backend.container_definitions)[0].healthCheck.command), "/api/health/live")
    error_message = "Container health check must hit /api/health/live."
  }

  assert {
    condition     = jsondecode(aws_ecs_task_definition.backend.container_definitions)[0].image == "111122223333.dkr.ecr.eu-west-1.amazonaws.com/bulk-loader-backend-test:stable"
    error_message = "Image must come from the ECR repository at the configured tag."
  }
}

run "iam_direction" {
  command = apply

  module {
    source = "./modules/backend"
  }

  # Execution role reads exactly the five secrets + five params.
  assert {
    condition = (
      toset(data.aws_iam_policy_document.execution_config_read.statement[0].resources) == toset(values(var.injected_secret_arns)) &&
      toset(data.aws_iam_policy_document.execution_config_read.statement[1].resources) == toset(values(var.injected_ssm_arns))
    )
    error_message = "Execution-role reads must be scoped to exactly the ten config ARNs - no wildcards, nothing extra."
  }

  # Task role: SES send scoped to THIS identity; only the account-read
  # statement may use *.
  assert {
    condition     = data.aws_iam_policy_document.task_ses.statement[0].resources == toset([var.ses_identity_arn])
    error_message = "ses:SendEmail/SendRawEmail must be scoped to the deployment's SES identity ARN."
  }

  assert {
    condition     = data.aws_iam_policy_document.task_ses.statement[1].actions == toset(["ses:GetSendQuota", "ses:GetAccount"])
    error_message = "The only wildcard-resource statement on the task role is the SES account-read pair."
  }

  # SFBL-388 (replaces the old "task role has no S3 grants" absence check):
  # the task role carries scoped object RW on the two key-spaces + ListBucket
  # on the two bucket ARNs - exactly, nothing extra.
  assert {
    condition = (
      data.aws_iam_policy_document.task_s3.statement[0].actions == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]) &&
      data.aws_iam_policy_document.task_s3.statement[0].resources == toset(["${var.input_bucket_arn}/*", "${var.output_bucket_arn}/*"]) &&
      data.aws_iam_policy_document.task_s3.statement[1].actions == toset(["s3:ListBucket"]) &&
      data.aws_iam_policy_document.task_s3.statement[1].resources == toset([var.input_bucket_arn, var.output_bucket_arn])
    )
    error_message = "Task-role S3 grant must be object RW on bucket/* + ListBucket on exactly the two first-party bucket ARNs."
  }

  # Falsification: no S3 statement on the task role may use Resource:"*".
  assert {
    condition = (
      !contains(data.aws_iam_policy_document.task_s3.statement[0].resources, "*") &&
      !contains(data.aws_iam_policy_document.task_s3.statement[1].resources, "*")
    )
    error_message = "Task-role S3 statements must never use a wildcard resource."
  }

  # QA #6: S3 lives on the TASK role only - the execution role carries no s3:*.
  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.execution_config_read.statement :
      !anytrue([for a in s.actions : strcontains(a, "s3:")])
    ])
    error_message = "The backend execution role must carry no s3:* actions - S3 belongs on the task role only."
  }
}

run "alb_and_service_behaviour" {
  command = apply

  module {
    source = "./modules/backend"
  }

  assert {
    condition     = aws_lb_target_group.backend.health_check[0].path == "/api/health/ready" && aws_lb_target_group.backend.health_check[0].healthy_threshold == 2 && aws_lb_target_group.backend.health_check[0].unhealthy_threshold == 3
    error_message = "ALB target health must use /api/health/ready (2 healthy / 3 unhealthy)."
  }

  assert {
    condition     = aws_lb_listener.http_redirect.default_action[0].redirect[0].status_code == "HTTP_301" && aws_lb_listener.http_redirect.default_action[0].redirect[0].port == "443"
    error_message = "Port 80 must permanently redirect to HTTPS."
  }

  assert {
    condition     = aws_lb_listener.https.certificate_arn == var.backend_certificate_arn
    error_message = "The HTTPS listener must use the deploy-region ALB certificate."
  }

  assert {
    condition     = aws_ecs_service.backend.deployment_minimum_healthy_percent == 50 && aws_ecs_service.backend.deployment_maximum_percent == 200
    error_message = "Rolling deploys must keep 50/200 healthy percent."
  }

  assert {
    condition     = aws_ecs_service.backend.deployment_circuit_breaker[0].enable && aws_ecs_service.backend.deployment_circuit_breaker[0].rollback
    error_message = "Deployment circuit breaker with rollback must be enabled."
  }

  assert {
    condition     = aws_ecs_service.backend.network_configuration[0].assign_public_ip
    error_message = "Tasks need public IPs (no NAT) - inbound is still ALB-only via the SG."
  }

  # Bronze/silver: 100% on-demand, no Spot.
  assert {
    condition     = !anytrue([for s in aws_ecs_service.backend.capacity_provider_strategy : s.capacity_provider == "FARGATE_SPOT"])
    error_message = "Non-gold tiers must not use FARGATE_SPOT."
  }
}

run "gold_fargate_spot_hybrid" {
  command = apply

  module {
    source = "./modules/backend"
  }

  variables {
    use_fargate_spot  = true
    ecs_desired_count = 2
  }

  assert {
    condition     = anytrue([for s in aws_ecs_service.backend.capacity_provider_strategy : s.capacity_provider == "FARGATE_SPOT"])
    error_message = "Gold must include the FARGATE_SPOT capacity provider."
  }

  # base=1 on FARGATE keeps one task on-demand if Spot is reclaimed.
  assert {
    condition     = anytrue([for s in aws_ecs_service.backend.capacity_provider_strategy : s.capacity_provider == "FARGATE" && s.base == 1])
    error_message = "Gold hybrid must keep one on-demand FARGATE task (base = 1)."
  }
}
