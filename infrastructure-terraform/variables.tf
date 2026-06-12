# Variable contract for the Terraform deployment flavour (SFBL-379).
#
# Two groups, supplied via two -var-file arguments:
#   1. Environment identity + DNS/TLS - copy environment.tfvars.example,
#      fill in, and keep out of version control.
#   2. Tier sizing - one of tiers/{bronze,silver,gold}.tfvars, committed.
#
# The contract mirrors the CDK env config (infrastructure/bin/app.ts +
# cdk.context.json.example) and tier presets (infrastructure/lib/tier-config.ts
# + cdk.json context.tiers). Sizing variables carry no defaults on purpose:
# the CDK fails at synth when an environment names no tier, and this contract
# fails at plan for the same reason - choosing a tier must be conscious.

# --- Environment identity ---

variable "env_name" {
  description = "Environment name (e.g. production, staging). Drives every resource name and the /<env>/bulk-loader/... Secrets Manager and SSM paths."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}$", var.env_name))
    error_message = "env_name must be lowercase alphanumeric/hyphen, 2-19 chars, starting with a letter - it is embedded in bucket names, which S3 caps at 63 chars including the account id."
  }
}

variable "aws_region" {
  description = "AWS region the stack deploys into. Pins the primary provider - the deploy never falls back to AWS_DEFAULT_REGION."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-\\d$", var.aws_region))
    error_message = "aws_region must be a valid AWS region identifier (e.g. eu-west-1)."
  }
}

# --- Networking ---

variable "vpc_cidr" {
  description = "CIDR block for the VPC (e.g. 10.20.0.0/16)."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

# --- DNS + TLS ---

variable "hosted_zone_domain" {
  description = "Route53 public hosted zone domain that backend_domain_name lives under (e.g. bulk-loader.example.com). Must already exist - prerequisite, not provisioned here."
  type        = string
}

variable "domain_name" {
  description = "Public FQDN the frontend is served from via CloudFront (e.g. bulk-loader.example.com)."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate ARN for the CloudFront distribution covering domain_name. MUST be issued in us-east-1 - a CloudFront constraint, regardless of aws_region."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:acm:us-east-1:\\d{12}:certificate/", var.certificate_arn))
    error_message = "certificate_arn must be an ACM certificate in us-east-1 (CloudFront requirement)."
  }
}

variable "backend_domain_name" {
  description = "FQDN for the backend API origin, pointed at the ALB (e.g. api.bulk-loader.example.com). CloudFront forwards /api/* and /ws/* here."
  type        = string
}

variable "backend_certificate_arn" {
  description = "ACM certificate ARN for the ALB HTTPS listener covering backend_domain_name. Must be issued in aws_region (the deploy region)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:acm:${var.aws_region}:\\d{12}:certificate/", var.backend_certificate_arn))
    error_message = "backend_certificate_arn must be an ACM certificate in the deploy region (aws_region) - it terminates TLS on the ALB."
  }
}

# --- Container image ---

variable "image_tag" {
  description = "Tag of the backend image in the ECR repository this stack creates. The published GHCR image must be mirrored into ECR under this tag before the backend module is applied (see the deployment guide)."
  type        = string
  default     = "latest"
}

# --- Email (SES) ---

variable "ses_identity_domain" {
  description = "Domain for the SES email identity. Defaults to hosted_zone_domain when null."
  type        = string
  default     = null
}

variable "ses_identity_adopt_existing" {
  description = "Set true when the SES domain identity already exists (verified via the console or an earlier deployment) - AWS refuses to create a duplicate. The existing identity is referenced, not modified."
  type        = bool
  default     = false
}

# --- Tier sizing (supplied via tiers/<tier>.tfvars; mirrors tier-config.ts) ---

variable "rds_instance_class" {
  description = "RDS instance class including the db. prefix (e.g. db.t4g.micro)."
  type        = string

  validation {
    condition     = can(regex("^db\\.", var.rds_instance_class))
    error_message = "rds_instance_class must include the db. prefix (e.g. db.t4g.micro)."
  }
}

variable "rds_multi_az" {
  description = "Whether the RDS instance spans two AZs (HA/cost decision)."
  type        = bool
}

variable "rds_allocated_storage" {
  description = "Initial RDS storage in GiB. Storage autoscaling extends to max(5x this value, 100)."
  type        = number
}

variable "rds_backup_retention_days" {
  description = "Automated RDS backup retention in days."
  type        = number
}

variable "rds_deletion_protection" {
  description = "Block DeleteDBInstance at the RDS level. The CDK's snapshot-on-destroy persistence machinery is not ported, so this is the only data-loss guard - keep it on for any tier holding real data (silver/gold)."
  type        = bool
}

variable "rds_skip_final_snapshot" {
  description = "Skip the RDS final snapshot on destroy. Deliberately separate from rds_deletion_protection: tearing down a protected environment means disabling protection first, and that flip must not silently drop the final snapshot too. Keep false on any tier holding real data."
  type        = bool
}

variable "ecs_desired_count" {
  description = "Number of backend Fargate tasks."
  type        = number
}

variable "ecs_task_cpu" {
  description = "Fargate task CPU units (1024 = 1 vCPU)."
  type        = number
}

variable "ecs_task_memory" {
  description = "Fargate task memory in MiB."
  type        = number
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days. Must be a value CloudWatch supports (7, 30, 365, ...)."
  type        = number

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.log_retention_days)
    error_message = "log_retention_days must be one of the retention values CloudWatch Logs supports."
  }
}

variable "container_insights_enabled" {
  description = "Enable ECS Container Insights and VPC flow logs (the enhanced-observability tier gate; carries CloudWatch ingestion cost)."
  type        = bool
}

variable "input_retention_days" {
  description = "Lifecycle expiry for the input CSV bucket in days. 0 = retain forever (no lifecycle rule is created)."
  type        = number
}

variable "output_retention_days" {
  description = "Lifecycle expiry for the output/results bucket in days. 0 = retain forever (no lifecycle rule is created)."
  type        = number
}

variable "use_fargate_spot" {
  description = "Run extra backend tasks on FARGATE_SPOT with one task kept on-demand (the CDK gold-tier hybrid strategy). Bronze/silver run 100% on-demand."
  type        = bool
  default     = false
}
