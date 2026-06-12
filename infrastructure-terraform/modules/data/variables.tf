variable "env_name" {
  description = "Environment name; drives resource names and the /<env>/bulk-loader/... secret and SSM paths."
  type        = string
}

variable "vpc_id" {
  description = "VPC the data layer lives in (from the network module)."
  type        = string
}

variable "isolated_subnet_ids" {
  description = "Isolated subnets for the RDS subnet group - no internet route."
  type        = list(string)
}

variable "backend_service_security_group_id" {
  description = "Security group of the ECS tasks - the only allowed source for PostgreSQL connections."
  type        = string
}

variable "protect_data" {
  description = "Whether this environment holds real data (tier rds_deletion_protection). Drives RDS deletion protection, the Secrets Manager recovery window, and whether ECR images can be force-deleted."
  type        = bool
}

variable "skip_final_snapshot" {
  description = "Skip the RDS final snapshot on destroy. Deliberately decoupled from protect_data: disabling deletion protection to tear an environment down must not also drop the final snapshot."
  type        = bool
}

variable "image_tag" {
  description = "Backend image tag in the ECR repository - used by the one-shot migration task definition."
  type        = string
}

variable "ecs_task_cpu" {
  description = "Migration task CPU units (matches the service task shape)."
  type        = number
}

variable "ecs_task_memory" {
  description = "Migration task memory in MiB (matches the service task shape)."
  type        = number
}

variable "log_retention_days" {
  description = "CloudWatch retention for the migration log group."
  type        = number
}

variable "rds_instance_class" {
  description = "RDS instance class including the db. prefix."
  type        = string
}

variable "rds_multi_az" {
  description = "Whether RDS spans two AZs."
  type        = bool
}

variable "rds_allocated_storage" {
  description = "Initial RDS storage in GiB."
  type        = number
}

variable "rds_backup_retention_days" {
  description = "Automated backup retention in days."
  type        = number
}

variable "input_retention_days" {
  description = "Input bucket lifecycle expiry in days; 0 = no lifecycle rule."
  type        = number
}

variable "output_retention_days" {
  description = "Output bucket lifecycle expiry in days; 0 = no lifecycle rule."
  type        = number
}

variable "domain_name" {
  description = "Frontend FQDN - seeds the cors-origins SSM parameter default."
  type        = string
}

variable "ses_identity_domain" {
  description = "Domain for the SES email identity (already resolved against hosted_zone_domain by the root)."
  type        = string
}

variable "ses_identity_adopt_existing" {
  description = "Reference an already-verified SES domain identity instead of creating one."
  type        = bool
}
