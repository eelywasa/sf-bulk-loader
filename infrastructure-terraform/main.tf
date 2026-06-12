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
