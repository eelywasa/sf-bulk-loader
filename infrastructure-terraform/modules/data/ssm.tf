# SSM Parameter Store - non-sensitive runtime config injected into ECS tasks
# at launch.
#
# Parity deviation (recorded in the SFBL-384 register): the CDK *imports*
# these five parameters and requires operators to create them before the
# first deploy. Here Terraform creates them with working defaults so a fresh
# account needs no pre-apply put-parameter step - and ignore_changes hands
# ownership to the operator after first apply, so edits via console/CLI are
# never reverted (the CDK comment block in backend-stack.ts documents the
# same edit-then-restart workflow).

locals {
  ssm_parameters = {
    "cors-origins" = {
      value       = jsonencode(["https://${var.domain_name}"])
      description = "Allowed CORS origins for the backend API (CORS_ORIGINS) - JSON array"
    }
    "log-level" = {
      value       = "INFO"
      description = "Backend log level (LOG_LEVEL)"
    }
    "admin-username" = {
      value       = "admin"
      description = "Bootstrap admin username for first-boot seeding (ADMIN_USERNAME)"
    }
    "email-from-address" = {
      value       = "noreply@${var.ses_identity_domain}"
      description = "From address for application email (EMAIL_FROM_ADDRESS)"
    }
    "email-ses-region" = {
      value       = local.region
      description = "AWS region the SES identity lives in (EMAIL_SES_REGION)"
    }
  }
}

resource "aws_ssm_parameter" "config" {
  for_each = local.ssm_parameters

  name        = "${local.secret_prefix}/${each.key}"
  description = each.value.description
  type        = "String"
  value       = each.value.value

  lifecycle {
    ignore_changes = [value]
  }
}
