# One-shot Alembic migration task (mirrors data-stack.ts SFBL-298).
#
# Lives in the data layer so it exists as soon as RDS + secrets + ECR are up,
# before the backend service - closing the first-deploy chicken-and-egg where
# the service booted against an empty schema and crashlooped.
#
# Fresh-account deploy order:
#   1. apply network + data            - RDS + secrets + ECR + this task def
#   2. mirror the backend image to ECR
#   3. aws ecs run-task ...            - schema reaches head (see the guide)
#   4. apply backend                   - service boots against a populated schema
#
# The task runs on its own bare Fargate cluster: on a fresh first deploy the
# backend cluster does not exist yet - and that is precisely when the
# migration must run. A Fargate-only cluster has no instance cost and no
# capacity-provider association (the task is invoked with
# --launch-type FARGATE).

resource "aws_ecs_cluster" "migration" {
  name = "bulk-loader-${var.env_name}-migration"
}

resource "aws_cloudwatch_log_group" "migration" {
  name              = "/bulk-loader/${var.env_name}/migration"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Container-side identity. The migration talks to the database via the injected
# DATABASE_URL; it also carries the same scoped first-party S3 grant as the
# backend service task role (SFBL-388) so the storage posture is identical
# across both task definitions and the config validator's env contract matches.
resource "aws_iam_role" "migration_task" {
  name_prefix        = "bl-${var.env_name}-migration-task-"
  description        = "Bulk Loader one-shot migration task role (${var.env_name})"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# Scoped first-party S3 access on the migration TASK role (not the execution
# role): object RW on the two key-spaces + ListBucket on the two bucket ARNs,
# no wildcard resource. Mirrors modules/backend/iam.tf.
data "aws_iam_policy_document" "migration_task_s3" {
  statement {
    sid = "FirstPartyBucketObjectReadWrite"
    # AbortMultipartUpload mirrors the backend module (S3OutputStorage aborts a
    # streamed multipart upload on failure).
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.data["input"].arn}/*", "${aws_s3_bucket.data["output"].arn}/*"]
  }

  statement {
    sid       = "FirstPartyBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data["input"].arn, aws_s3_bucket.data["output"].arn]
  }
}

resource "aws_iam_role_policy" "migration_task_s3" {
  name_prefix = "s3-"
  role        = aws_iam_role.migration_task.id
  policy      = data.aws_iam_policy_document.migration_task_s3.json
}

# Execution role - what ECS itself uses to pull the image, write logs, and
# inject the secrets/SSM values below. Reads are scoped to the five app
# secrets and five parameters, never *.
resource "aws_iam_role" "migration_execution" {
  name_prefix        = "bl-${var.env_name}-migration-exec-"
  description        = "Bulk Loader migration task execution role (${var.env_name})"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "migration_execution_managed" {
  role       = aws_iam_role.migration_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

locals {
  # The five app secrets (Secrets Manager) and five config params (SSM) every
  # task injects - shared with the backend module via outputs.
  injected_secret_arns = {
    ENCRYPTION_KEY = aws_secretsmanager_secret.app["encryption-key"].arn
    JWT_SECRET_KEY = aws_secretsmanager_secret.app["jwt-secret-key"].arn
    DATABASE_URL   = aws_secretsmanager_secret.database_url.arn
    ADMIN_EMAIL    = aws_secretsmanager_secret.app["admin-email"].arn
    ADMIN_PASSWORD = aws_secretsmanager_secret.app["admin-password"].arn
  }

  injected_ssm_arns = {
    CORS_ORIGINS       = aws_ssm_parameter.config["cors-origins"].arn
    LOG_LEVEL          = aws_ssm_parameter.config["log-level"].arn
    ADMIN_USERNAME     = aws_ssm_parameter.config["admin-username"].arn
    EMAIL_FROM_ADDRESS = aws_ssm_parameter.config["email-from-address"].arn
    EMAIL_SES_REGION   = aws_ssm_parameter.config["email-ses-region"].arn
  }

  # ECS container-definition secrets block: Secrets Manager ARNs and SSM
  # parameter ARNs both go in valueFrom; ECS resolves the live value at task
  # launch, so a parameter edit + rerun picks up the new value with no
  # re-apply.
  injected_secrets = [
    for name, arn in merge(local.injected_secret_arns, local.injected_ssm_arns) :
    { name = name, valueFrom = arn }
  ]
}

data "aws_iam_policy_document" "task_config_read" {
  statement {
    sid       = "ReadAppSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = values(local.injected_secret_arns)
  }

  statement {
    sid       = "ReadConfigParameters"
    actions   = ["ssm:GetParameters"]
    resources = values(local.injected_ssm_arns)
  }
}

resource "aws_iam_role_policy" "migration_execution_config_read" {
  name_prefix = "config-read-"
  role        = aws_iam_role.migration_execution.id
  policy      = data.aws_iam_policy_document.task_config_read.json
}

resource "aws_ecs_task_definition" "migration" {
  family                   = "bulk-loader-${var.env_name}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_task_cpu
  memory                   = var.ecs_task_memory
  task_role_arn            = aws_iam_role.migration_task.arn
  execution_role_arn       = aws_iam_role.migration_execution.arn

  container_definitions = jsonencode([
    {
      name      = "migration"
      image     = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
      essential = true

      # Same secret/SSM injection as the service task - the migration runs
      # the full app config validator at boot.
      secrets = local.injected_secrets

      environment = [
        { name = "APP_DISTRIBUTION", value = "aws_hosted" },
        { name = "RUN_MIGRATIONS", value = "true" },
        # SFBL-385: keep the storage env contract identical to the service task
        # so the migration boots through the same config validator (which now
        # requires these on input_storage_mode=s3).
        { name = "S3_INPUT_BUCKET", value = aws_s3_bucket.data["input"].id },
        { name = "S3_OUTPUT_BUCKET", value = aws_s3_bucket.data["output"].id },
        { name = "S3_BUCKET_REGION", value = local.region },
      ]

      # Override the image CMD: run the migration then exit cleanly.
      command = ["sh", "-c", "alembic upgrade head"]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.migration.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "migration"
        }
      }
    }
  ])
}
