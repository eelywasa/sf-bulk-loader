variable "env_name" {
  description = "Environment name; embedded in resource names and comments."
  type        = string
}

variable "domain_name" {
  description = "Public FQDN the frontend serves from (CloudFront alias)."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate for domain_name - must be in us-east-1 (validated at the root)."
  type        = string
}

variable "backend_origin_domain_name" {
  description = "Backend origin hostname covered by the ALB certificate - CloudFront forwards /api/* and /ws/* here."
  type        = string
}
