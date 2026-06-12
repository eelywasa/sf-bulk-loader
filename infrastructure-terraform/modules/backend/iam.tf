# IAM - two roles with deliberately different scopes (backend-stack.ts):
#
#   Execution role - what ECS itself uses: pull from ECR, write logs, and
#   read the secrets/SSM parameters it injects as env vars. This role, NOT
#   the task role, holds the secret/SSM reads.
#
#   Task role - the running container's identity. It has NO S3 grants: the
#   application reads/writes S3 via per-Connection access keys stored
#   encrypted in the DB (app/services/output_storage.py,
#   app/api/input_connections.py). Its only permissions are SES send
#   (scoped to this deployment's identity) and the account-wide SES reads
#   the health probe needs.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# --- Execution role ---

resource "aws_iam_role" "execution" {
  name_prefix        = "bl-${var.env_name}-backend-exec-"
  description        = "Bulk Loader backend task execution role (${var.env_name})"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_config_read" {
  statement {
    sid       = "ReadAppSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = values(var.injected_secret_arns)
  }

  statement {
    sid       = "ReadConfigParameters"
    actions   = ["ssm:GetParameters"]
    resources = values(var.injected_ssm_arns)
  }
}

resource "aws_iam_role_policy" "execution_config_read" {
  name_prefix = "config-read-"
  role        = aws_iam_role.execution.id
  policy      = data.aws_iam_policy_document.execution_config_read.json
}

# --- Task role ---

resource "aws_iam_role" "task" {
  name_prefix        = "bl-${var.env_name}-backend-task-"
  description        = "Bulk Loader ECS task role (${var.env_name})"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task_ses" {
  # Send actions restricted to the deployment's own identity - limits blast
  # radius if the role is ever compromised.
  statement {
    sid       = "SesSendScopedToIdentity"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = [var.ses_identity_arn]
  }

  # GetSendQuota / GetAccount are account-wide reads that don't accept a
  # resource ARN - the one justified wildcard (health probe at
  # /api/health/dependencies).
  statement {
    sid       = "SesAccountReadForHealthProbe"
    actions   = ["ses:GetSendQuota", "ses:GetAccount"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task_ses" {
  name_prefix = "ses-"
  role        = aws_iam_role.task.id
  policy      = data.aws_iam_policy_document.task_ses.json
}
