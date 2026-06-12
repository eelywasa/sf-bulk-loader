# The deploy region comes from var.aws_region, never from the caller's
# shell (AWS_DEFAULT_REGION) - mirroring the CDK app, which pins region
# from env config so a deploy can't land in whatever region the operator's
# environment happens to point at (infrastructure/bin/app.ts).
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "sf-bulk-loader"
      Environment = var.env_name
      ManagedBy   = "terraform"
    }
  }
}

# CloudFront only accepts ACM certificates issued in us-east-1, regardless
# of where the rest of the stack deploys. Every CloudFront/ACM resource in
# the frontend module must use this alias; the ALB certificate stays in the
# deploy-region provider above.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "sf-bulk-loader"
      Environment = var.env_name
      ManagedBy   = "terraform"
    }
  }
}
