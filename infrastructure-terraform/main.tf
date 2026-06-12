# Root composition - mirrors the CDK stack ordering in infrastructure/bin/app.ts:
#   network -> data -> (operator mirrors image + runs migration task) -> backend -> frontend

locals {
  # Every named resource shares this prefix, mirroring the CDK's
  # bulk-loader-${env} convention.
  name_prefix = "bulk-loader-${var.env_name}"

  # SES identity defaults to the hosted zone domain, like the CDK
  # (data-stack.ts: sesIdentityDomain ?? hostedZoneDomain).
  ses_identity_domain = coalesce(var.ses_identity_domain, var.hosted_zone_domain)
}

# Stack 1: VPC and network topology
module "network" {
  source = "./modules/network"

  env_name           = var.env_name
  vpc_cidr           = var.vpc_cidr
  enable_flow_logs   = var.container_insights_enabled
  log_retention_days = var.log_retention_days
}

# Stack 2: ECR, RDS, S3, Secrets Manager, SSM, SES, migration task
module "data" {
  source = "./modules/data"

  env_name                          = var.env_name
  vpc_id                            = module.network.vpc_id
  isolated_subnet_ids               = module.network.isolated_subnet_ids
  backend_service_security_group_id = module.network.backend_service_security_group_id

  protect_data = var.rds_deletion_protection
  image_tag    = var.image_tag

  rds_instance_class        = var.rds_instance_class
  rds_multi_az              = var.rds_multi_az
  rds_allocated_storage     = var.rds_allocated_storage
  rds_backup_retention_days = var.rds_backup_retention_days

  ecs_task_cpu       = var.ecs_task_cpu
  ecs_task_memory    = var.ecs_task_memory
  log_retention_days = var.log_retention_days

  input_retention_days  = var.input_retention_days
  output_retention_days = var.output_retention_days

  domain_name                 = var.domain_name
  ses_identity_domain         = local.ses_identity_domain
  ses_identity_adopt_existing = var.ses_identity_adopt_existing
}
