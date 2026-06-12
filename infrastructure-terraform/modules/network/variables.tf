variable "env_name" {
  description = "Environment name; embedded in resource names and log group paths."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Four /24 subnets are carved from it (2 public + 2 isolated)."
  type        = string
}

variable "enable_flow_logs" {
  description = "Provision VPC flow logs to CloudWatch. Tied to the tier's enhanced-observability gate (container_insights_enabled) - they carry CloudWatch ingestion cost, so bronze stays off."
  type        = bool
}

variable "log_retention_days" {
  description = "CloudWatch retention for the flow-log group, from the tier preset."
  type        = number
}
