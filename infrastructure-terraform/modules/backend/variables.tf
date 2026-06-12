variable "env_name" {
  description = "Environment name; drives resource names and log group paths."
  type        = string
}

variable "vpc_id" {
  description = "VPC the ALB target group lives in."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the ALB and the Fargate tasks (public IPs, no NAT)."
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "Security group for the ALB."
  type        = string
}

variable "backend_service_security_group_id" {
  description = "Security group for the ECS tasks (8000 from ALB only)."
  type        = string
}

variable "ecr_repository_url" {
  description = "ECR repository URI holding the mirrored backend image."
  type        = string
}

variable "image_tag" {
  description = "Backend image tag in the ECR repository."
  type        = string
}

variable "injected_secret_arns" {
  description = "Env var name -> Secrets Manager ARN for the five sensitive values (from the data module)."
  type        = map(string)
}

variable "injected_ssm_arns" {
  description = "Env var name -> SSM parameter ARN for the five non-sensitive config values (from the data module)."
  type        = map(string)
}

variable "ses_identity_arn" {
  description = "SES identity ARN the task role's send permission is scoped to."
  type        = string
}

variable "backend_domain_name" {
  description = "FQDN for the backend origin - the Route53 alias created here."
  type        = string
}

variable "backend_certificate_arn" {
  description = "ACM certificate (deploy region) for the ALB HTTPS listener."
  type        = string
}

variable "hosted_zone_domain" {
  description = "Route53 hosted zone the backend record is created in."
  type        = string
}

variable "ecs_desired_count" {
  description = "Number of backend tasks."
  type        = number
}

variable "ecs_task_cpu" {
  description = "Task CPU units."
  type        = number
}

variable "ecs_task_memory" {
  description = "Task memory in MiB."
  type        = number
}

variable "log_retention_days" {
  description = "CloudWatch retention for the backend log group."
  type        = number
}

variable "container_insights_enabled" {
  description = "Enable ECS Container Insights on the cluster (tier observability gate)."
  type        = bool
}

variable "use_fargate_spot" {
  description = "Gold-tier hybrid strategy: keep one task on-demand, run the rest on FARGATE_SPOT."
  type        = bool
}
