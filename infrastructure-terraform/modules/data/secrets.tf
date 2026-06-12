# Secrets Manager - canonical /<env>/bulk-loader/* paths the ECS tasks
# inject from (docs/deployment/aws.md).
#
# Recovery window follows the data-protection signal: protected tiers keep
# the 30-day undelete window; disposable tiers delete immediately so a
# destroy/recreate cycle doesn't hit "secret name is scheduled for deletion".

locals {
  secret_recovery_window = var.protect_data ? 30 : 0

  app_secrets = {
    "encryption-key" = "Fernet encryption key for stored Salesforce connection secrets (ENCRYPTION_KEY)"
    "jwt-secret-key" = "JWT signing secret for in-app bearer token authentication (JWT_SECRET_KEY)"
    "admin-email"    = "Bootstrap admin email / login identifier for first-boot user seeding (ADMIN_EMAIL)"
    "admin-password" = "Bootstrap admin password for first-boot user seeding (ADMIN_PASSWORD)"
  }
}

# RDS master credentials - generated, not operator-supplied. JSON shape
# matches the CDK Credentials.fromGeneratedSecret secret so existing runbooks
# (docs/deployment/aws.md) read both flavours identically.
resource "aws_secretsmanager_secret" "rds_credentials" {
  name                    = "${local.secret_prefix}/rds-credentials"
  description             = "Auto-generated master credentials for the Bulk Loader RDS instance"
  recovery_window_in_days = local.secret_recovery_window
}

resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    engine   = "postgres"
    host     = aws_db_instance.main.address
    port     = 5432
    dbname   = "bulk_loader"
    username = "bulk_loader_user"
    password = random_password.db_master.result
  })
}

# Four empty placeholders - the operator provisions real values before the
# first ECS task start (aws secretsmanager put-secret-value ...; see the
# deployment guide). Creating them empty mirrors the CDK exactly.
resource "aws_secretsmanager_secret" "app" {
  for_each = local.app_secrets

  name                    = "${local.secret_prefix}/${each.key}"
  description             = each.value
  recovery_window_in_days = local.secret_recovery_window
}

# DATABASE_URL - composed automatically from the RDS endpoint + generated
# credentials (parity deviation from the CDK, which leaves this for the
# operator to assemble; recorded in the SFBL-384 register). Format per
# docs/deployment/aws.md - the aws_hosted profile validates the
# postgresql+asyncpg:// scheme at boot.
resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.secret_prefix}/database-url"
  description             = "Full PostgreSQL asyncpg connection URL including credentials (DATABASE_URL)"
  recovery_window_in_days = local.secret_recovery_window
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+asyncpg://bulk_loader_user:${random_password.db_master.result}@${aws_db_instance.main.endpoint}/bulk_loader?ssl=require"

  # Initial value only - operators own the secret after first apply (endpoint
  # moves, password rotation) and Terraform must never revert their edits.
  lifecycle {
    ignore_changes = [secret_string]
  }
}
