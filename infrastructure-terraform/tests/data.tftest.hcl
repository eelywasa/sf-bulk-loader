# Data module assertions (SFBL-381 ACs).

mock_provider "aws" {
  # The provider validates policy JSON even under mocks - pin a valid doc.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  # ARN-typed arguments (task/execution role ARNs) are validated under mocks.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::111122223333:role/mock-role"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "111122223333"
    }
  }

  mock_data "aws_region" {
    defaults = {
      region = "eu-west-1"
    }
  }
}

variables {
  env_name                          = "test"
  vpc_id                            = "vpc-0123456789abcdef0"
  isolated_subnet_ids               = ["subnet-aaa", "subnet-bbb"]
  backend_service_security_group_id = "sg-backend0123"
  protect_data                      = false
  skip_final_snapshot               = true
  image_tag                         = "latest"
  ecs_task_cpu                      = 512
  ecs_task_memory                   = 1024
  log_retention_days                = 7
  rds_instance_class                = "db.t4g.micro"
  rds_multi_az                      = false
  rds_allocated_storage             = 20
  rds_backup_retention_days         = 7
  input_retention_days              = 7
  output_retention_days             = 30
  domain_name                       = "bulk-loader.example.com"
  ses_identity_domain               = "example.com"
  ses_identity_adopt_existing       = false
}

run "rds_hardening" {
  command = apply

  module {
    source = "./modules/data"
  }

  # Server-enforced TLS: without rds.force_ssl=1 a VPC-resident attacker
  # could downgrade to plaintext.
  assert {
    condition     = anytrue([for p in aws_db_parameter_group.postgres.parameter : p.name == "rds.force_ssl" && p.value == "1"])
    error_message = "Parameter group must set rds.force_ssl = 1."
  }

  assert {
    condition     = aws_db_parameter_group.postgres.family == "postgres16" && aws_db_instance.main.engine_version == "16"
    error_message = "Engine must be Postgres major version 16."
  }

  assert {
    condition     = aws_db_instance.main.storage_encrypted && aws_db_instance.main.storage_type == "gp3"
    error_message = "RDS storage must be encrypted gp3."
  }

  assert {
    condition     = !aws_db_instance.main.publicly_accessible
    error_message = "The database must not be publicly accessible."
  }

  assert {
    condition     = aws_db_instance.main.db_name == "bulk_loader" && aws_db_instance.main.username == "bulk_loader_user"
    error_message = "DB name/user must match the CDK (bulk_loader / bulk_loader_user)."
  }

  assert {
    condition     = aws_db_instance.main.max_allocated_storage == 100 && aws_db_instance.main.auto_minor_version_upgrade
    error_message = "Storage autoscaling max(5x,100) and auto minor upgrades must match the CDK."
  }

  # DB reachable only from the backend SG - a CIDR-sourced rule fails.
  assert {
    condition     = aws_vpc_security_group_ingress_rule.db_from_backend.referenced_security_group_id == "sg-backend0123" && aws_vpc_security_group_ingress_rule.db_from_backend.cidr_ipv4 == null
    error_message = "Port 5432 must be sourced from the backend SG, never a CIDR."
  }

  assert {
    condition     = aws_db_subnet_group.main.subnet_ids == toset(["subnet-aaa", "subnet-bbb"])
    error_message = "The DB subnet group must span exactly the isolated subnets."
  }
}

# The protected-teardown scenario from the PR #105 Codex review: disabling
# deletion protection (the required first step of a real teardown) must NOT
# drop the final snapshot with it.
run "final_snapshot_survives_protection_flip" {
  command = apply

  module {
    source = "./modules/data"
  }

  variables {
    protect_data        = false
    skip_final_snapshot = false
  }

  assert {
    condition     = !aws_db_instance.main.skip_final_snapshot && aws_db_instance.main.final_snapshot_identifier == "bulk-loader-test-final"
    error_message = "With protection off but skip_final_snapshot=false, the destroy must still take the final snapshot - the last recovery point in a protected-tier teardown."
  }
}

run "secrets_and_ssm_contract" {
  command = apply

  module {
    source = "./modules/data"
  }

  # Canonical paths: 4 empty app secrets + database-url + rds-credentials.
  assert {
    condition = length(aws_secretsmanager_secret.app) == 4 && alltrue([
      for k in ["encryption-key", "jwt-secret-key", "admin-email", "admin-password"] :
      aws_secretsmanager_secret.app[k].name == "/test/bulk-loader/${k}"
    ])
    error_message = "The four app secrets must exist at /<env>/bulk-loader/<name>."
  }

  assert {
    condition     = aws_secretsmanager_secret.database_url.name == "/test/bulk-loader/database-url" && aws_secretsmanager_secret.rds_credentials.name == "/test/bulk-loader/rds-credentials"
    error_message = "database-url and rds-credentials secrets must use their canonical names."
  }

  # The composed DATABASE_URL must use the asyncpg scheme + TLS the app
  # validates at boot.
  assert {
    condition     = startswith(aws_secretsmanager_secret_version.database_url.secret_string, "postgresql+asyncpg://bulk_loader_user:") && endswith(aws_secretsmanager_secret_version.database_url.secret_string, "/bulk_loader?ssl=require")
    error_message = "Composed DATABASE_URL must be postgresql+asyncpg://...?ssl=require."
  }

  assert {
    condition     = length(aws_ssm_parameter.config) == 5 && aws_ssm_parameter.config["cors-origins"].value == jsonencode(["https://bulk-loader.example.com"])
    error_message = "Five SSM parameters must exist; cors-origins must default to the frontend origin."
  }
}

run "buckets_lockdown_and_logging" {
  command = apply

  module {
    source = "./modules/data"
  }

  assert {
    condition = alltrue([
      for b in ["input", "output"] :
      aws_s3_bucket_public_access_block.data[b].block_public_acls &&
      aws_s3_bucket_public_access_block.data[b].block_public_policy &&
      aws_s3_bucket_public_access_block.data[b].ignore_public_acls &&
      aws_s3_bucket_public_access_block.data[b].restrict_public_buckets
    ])
    error_message = "Data buckets must block all public access."
  }

  assert {
    condition     = aws_s3_bucket_logging.data["input"].target_prefix == "input/" && aws_s3_bucket_logging.data["output"].target_prefix == "output/" && aws_s3_bucket_logging.data["input"].target_bucket == aws_s3_bucket.access_logs.id
    error_message = "Input/output buckets must access-log into the access-logs bucket with their prefixes."
  }

  # retention > 0 on both in the default vars -> two lifecycle configs.
  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.data) == 2
    error_message = "Lifecycle rules expected on both data buckets when retention > 0."
  }
}

run "buckets_zero_retention_means_no_lifecycle" {
  command = apply

  module {
    source = "./modules/data"
  }

  variables {
    input_retention_days  = 0
    output_retention_days = 0
  }

  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.data) == 0
    error_message = "retention 0 means retain forever - no lifecycle rule may be emitted."
  }
}

run "migration_task_contract" {
  command = apply

  module {
    source = "./modules/data"
  }

  assert {
    condition     = aws_ecs_cluster.migration.name == "bulk-loader-test-migration"
    error_message = "Migration task must run on its own dedicated cluster."
  }

  assert {
    condition = anytrue([
      for e in jsondecode(aws_ecs_task_definition.migration.container_definitions)[0].environment :
      e.name == "RUN_MIGRATIONS" && e.value == "true"
    ])
    error_message = "Migration task must set RUN_MIGRATIONS=true."
  }

  assert {
    condition     = jsondecode(aws_ecs_task_definition.migration.container_definitions)[0].command == ["sh", "-c", "alembic upgrade head"]
    error_message = "Migration task must run alembic upgrade head and exit."
  }

  assert {
    condition     = length(jsondecode(aws_ecs_task_definition.migration.container_definitions)[0].secrets) == 10
    error_message = "Migration task must inject all ten secret/SSM values (full config validator runs at boot)."
  }
}

run "ses_adopt_existing_creates_nothing" {
  command = apply

  module {
    source = "./modules/data"
  }

  variables {
    ses_identity_adopt_existing = true
  }

  assert {
    condition     = length(aws_sesv2_email_identity.main) == 0
    error_message = "Adopting an existing SES identity must not create one (AWS refuses duplicates)."
  }
}

run "ses_create_branch_sets_mail_from" {
  command = apply

  module {
    source = "./modules/data"
  }

  assert {
    condition     = length(aws_sesv2_email_identity.main) == 1 && aws_sesv2_email_identity_mail_from_attributes.main[0].mail_from_domain == "mail.example.com"
    error_message = "Created SES identity must carry the mail.<domain> MAIL FROM."
  }
}
