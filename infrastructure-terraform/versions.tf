# Version floor is shared by Terraform and OpenTofu (both version their
# releases past 1.10): 1.10 is the minimum that supports native S3 state
# locking (use_lockfile) on both runtimes, which this configuration uses
# instead of a DynamoDB lock table. Do not raise the floor without checking
# the current OpenTofu release still satisfies it (SFBL-379 dual-CLI AC).
terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
