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

  protect_data        = var.rds_deletion_protection
  skip_final_snapshot = var.rds_skip_final_snapshot
  image_tag           = var.image_tag

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

# Stack 3: ECS/Fargate backend service + ALB
# First-deploy ordering: the backend image must be mirrored into ECR and the
# migration task run BEFORE this module's service can start (see the guide).
module "backend" {
  source = "./modules/backend"

  env_name = var.env_name

  vpc_id                            = module.network.vpc_id
  public_subnet_ids                 = module.network.public_subnet_ids
  alb_security_group_id             = module.network.alb_security_group_id
  backend_service_security_group_id = module.network.backend_service_security_group_id

  ecr_repository_url   = module.data.ecr_repository_url
  image_tag            = var.image_tag
  injected_secret_arns = module.data.injected_secret_arns
  injected_ssm_arns    = module.data.injected_ssm_arns
  ses_identity_arn     = module.data.ses_identity_arn

  backend_domain_name     = var.backend_domain_name
  backend_certificate_arn = var.backend_certificate_arn
  hosted_zone_domain      = var.hosted_zone_domain

  ecs_desired_count          = var.ecs_desired_count
  ecs_task_cpu               = var.ecs_task_cpu
  ecs_task_memory            = var.ecs_task_memory
  log_retention_days         = var.log_retention_days
  container_insights_enabled = var.container_insights_enabled
  use_fargate_spot           = var.use_fargate_spot
}

# Stack 4: CloudFront + S3 static frontend
module "frontend" {
  source = "./modules/frontend"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  env_name                   = var.env_name
  domain_name                = var.domain_name
  certificate_arn            = var.certificate_arn
  backend_origin_domain_name = var.backend_domain_name
}
