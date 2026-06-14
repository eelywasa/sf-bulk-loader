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

variable "hosted_zone_domain" {
  description = "Public Route53 hosted zone (in this account) that domain_name lives under (e.g. bulk-loader.example.com). Used to create the frontend apex alias when manage_frontend_dns is true. Must already exist."
  type        = string
}

variable "manage_frontend_dns" {
  description = "SFBL-390: manage the frontend domain_name Route53 A-alias in this module (default true) so it is created/destroyed/repointed with the stack. Set false when this account should not own the domain_name record during apply: (1) external-DNS deployments whose domain_name lives in DNS this account does not control, or (2) staged same-account migrations/cutovers where the live record still points at the old system and must only be flipped after smoke-testing (NB allow_overwrite means a default-on apply would clobber that live record immediately). When false, no Route53 record is emitted for domain_name."
  type        = bool
  default     = true
}
