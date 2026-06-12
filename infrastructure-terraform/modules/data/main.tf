# Data module - persistence layer for the aws_hosted distribution.
# Mirrors infrastructure/lib/data-stack.ts minus the persistOnDestroy /
# snapshot-restore machinery (operational tooling for the first-party
# staging environment; customer deployments are create-fresh).
#
# Split by concern: ecr.tf, rds.tf, s3.tf, secrets.tf, ssm.tf, ses.tf,
# migration.tf.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # Canonical config paths - the backend module and operators rely on these
  # exact names (docs/deployment/aws.md).
  secret_prefix = "/${var.env_name}/bulk-loader"

  # Account-scoped bucket names: the CDK's deterministic bulk-loader-<env>-*
  # names collide across customer accounts in S3's global namespace, so the
  # account id is appended (parity-deviation register, SFBL-384).
  bucket_prefix = "bulk-loader-${var.env_name}-${local.account_id}"
}
