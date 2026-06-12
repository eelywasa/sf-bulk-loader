# CloudFront's ACM certificate must live in us-east-1 regardless of the
# deploy region, so this module takes a second, us-east-1-pinned provider
# from the root and creates the CloudFront-side resources through it.
terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1]
    }
  }
}
