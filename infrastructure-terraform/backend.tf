# Remote state is customer-supplied: copy backend.hcl.example to backend.hcl,
# fill in your bucket/key/region, then run
#
#   terraform init -backend-config=backend.hcl
#
# For validation-only workflows (fmt/validate, CI syntax checks) no backend
# is needed: terraform init -backend=false.
#
# State locking uses the native S3 lock file (use_lockfile in backend.hcl),
# supported by both Terraform >= 1.10 and OpenTofu >= 1.10 - no DynamoDB
# lock table required.
terraform {
  backend "s3" {}
}
