# RDS PostgreSQL - placed in isolated subnets (no internet route, reachable
# from within the VPC only). The aws_hosted profile requires a PostgreSQL
# DATABASE_URL - SQLite is rejected at startup.

# Outbound deliberately absent (CDK allowAllOutbound: false) - the DB never
# initiates connections.
resource "aws_security_group" "db" {
  name_prefix = "bulk-loader-${var.env_name}-db-"
  description = "Allow PostgreSQL access from ECS tasks"
  vpc_id      = var.vpc_id

  tags = {
    Name = "bulk-loader-${var.env_name}-db"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Sourced from the backend service SG, never a CIDR.
resource "aws_vpc_security_group_ingress_rule" "db_from_backend" {
  security_group_id            = aws_security_group.db.id
  description                  = "Allow PostgreSQL from ECS tasks"
  referenced_security_group_id = var.backend_service_security_group_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_db_subnet_group" "main" {
  name_prefix = "bulk-loader-${var.env_name}-"
  description = "Isolated subnets for the Bulk Loader database"
  subnet_ids  = var.isolated_subnet_ids
}

# Custom parameter group - enforces TLS at the server (rds.force_ssl=1).
# Without this, Postgres accepts non-SSL connections even though the
# application connects with ?ssl=require; an attacker who reaches the VPC
# could downgrade the connection.
resource "aws_db_parameter_group" "postgres" {
  name_prefix = "bulk-loader-${var.env_name}-pg16-"
  family      = "postgres16"
  description = "Bulk Loader Postgres 16 parameter group (${var.env_name}) - force_ssl enforced"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Master password generated here and stored at the canonical
# /<env>/bulk-loader/rds-credentials path (secrets.tf), mirroring the CDK's
# Credentials.fromGeneratedSecret. Alphanumeric-only so it embeds in the
# composed DATABASE_URL without percent-encoding (and satisfies RDS's
# forbidden-character rules). The value necessarily lives in Terraform
# state - keep the state bucket encrypted and access-controlled.
resource "random_password" "db_master" {
  length  = 32
  special = false
}

resource "aws_db_instance" "main" {
  identifier_prefix = "bulk-loader-${var.env_name}-"

  engine         = "postgres"
  engine_version = "16"
  instance_class = var.rds_instance_class

  db_name  = "bulk_loader"
  username = "bulk_loader_user"
  password = random_password.db_master.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.postgres.name
  publicly_accessible    = false

  # gp3 over the default gp2 - cheaper per GB with a higher baseline IOPS
  # floor; minor engine patches applied in the maintenance window so the
  # instance doesn't drift onto an unpatched Postgres 16.x.
  storage_encrypted          = true
  storage_type               = "gp3"
  allocated_storage          = var.rds_allocated_storage
  max_allocated_storage      = max(var.rds_allocated_storage * 5, 100)
  auto_minor_version_upgrade = true

  multi_az                = var.rds_multi_az
  backup_retention_period = var.rds_backup_retention_days

  # The CDK persistOnDestroy snapshot machinery is not ported, so for tiers
  # holding real data deletion protection stays on. The final-snapshot
  # decision is deliberately a SEPARATE variable: tearing down a protected
  # environment requires flipping protect_data off first, and if that same
  # flip disabled the final snapshot, the teardown would delete the database
  # with no recovery point exactly when one matters (Codex review, PR #105).
  deletion_protection       = var.protect_data
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = "bulk-loader-${var.env_name}-final"
}
