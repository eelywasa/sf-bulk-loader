# Root variable-contract falsification tests (SFBL-379 ACs): the two-region
# ACM split must be enforced by validation, not convention. Each run supplies
# an otherwise-valid variable set with one wrong-region certificate and
# expects exactly that variable's validation to fail.

# The full root configuration plans under these mocks, so every data source
# and validated ARN argument across all four modules needs a sane default.
mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["eu-west-1a", "eu-west-1b", "eu-west-1c"]
    }
  }

  mock_data "aws_region" {
    defaults = {
      region = "eu-west-1"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "111122223333"
    }
  }

  mock_data "aws_route53_zone" {
    defaults = {
      zone_id = "Z0123456789ABC"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::111122223333:role/mock-role"
    }
  }

  mock_resource "aws_lb" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:eu-west-1:111122223333:loadbalancer/app/mock/0123456789abcdef"
    }
  }

  mock_resource "aws_lb_target_group" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:eu-west-1:111122223333:targetgroup/mock/0123456789abcdef"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:eu-west-1:111122223333:log-group:/mock"
    }
  }
}

mock_provider "aws" {
  alias = "us_east_1"

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

variables {
  env_name                   = "test"
  aws_region                 = "eu-west-1"
  vpc_cidr                   = "10.0.0.0/16"
  hosted_zone_domain         = "example.com"
  domain_name                = "bulk-loader.example.com"
  certificate_arn            = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  backend_domain_name        = "api.bulk-loader.example.com"
  backend_certificate_arn    = "arn:aws:acm:eu-west-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  image_tag                  = "latest"
  rds_instance_class         = "db.t4g.micro"
  rds_multi_az               = false
  rds_allocated_storage      = 20
  rds_backup_retention_days  = 7
  rds_deletion_protection    = false
  rds_skip_final_snapshot    = true
  ecs_desired_count          = 1
  ecs_task_cpu               = 512
  ecs_task_memory            = 1024
  log_retention_days         = 7
  container_insights_enabled = false
  input_retention_days       = 7
  output_retention_days      = 30
}

# CloudFront only accepts us-east-1 certificates: a deploy-region cert for
# the frontend must fail validation.
run "cloudfront_cert_must_be_us_east_1" {
  command = plan

  variables {
    certificate_arn = "arn:aws:acm:eu-west-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  }

  expect_failures = [var.certificate_arn]
}

# The ALB certificate must be in the deploy region: a us-east-1 cert for the
# backend (when deploying to eu-west-1) must fail validation.
run "alb_cert_must_be_in_deploy_region" {
  command = plan

  variables {
    backend_certificate_arn = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  }

  expect_failures = [var.backend_certificate_arn]
}

# Sanity: env_name length cap (S3 63-char bucket-name budget).
run "env_name_length_capped" {
  command = plan

  variables {
    env_name = "this-env-name-is-far-too-long-for-bucket-names"
  }

  expect_failures = [var.env_name]
}

# SFBL-390: with manage_frontend_dns at its default (true), the root emits
# exactly one A-alias for domain_name to the CloudFront zone Z2FDTNDATAQYW2,
# with allow_overwrite so it adopts a pre-existing manual record and repoints.
run "frontend_dns_managed_by_default" {
  command = plan

  assert {
    condition = (
      length(aws_route53_record.frontend) == 1 &&
      aws_route53_record.frontend[0].name == var.domain_name &&
      aws_route53_record.frontend[0].type == "A" &&
      aws_route53_record.frontend[0].alias[0].zone_id == "Z2FDTNDATAQYW2" &&
      aws_route53_record.frontend[0].allow_overwrite == true
    )
    error_message = "manage_frontend_dns=true must emit one A-alias for domain_name to CloudFront zone Z2FDTNDATAQYW2, with allow_overwrite."
  }
}

# SFBL-390 falsification: with manage_frontend_dns = false the plan must contain
# NO Route53 record (and no zone lookup) for domain_name - proving external-DNS
# and staged-migration deployments are unaffected.
run "frontend_dns_opt_out_emits_nothing" {
  command = plan

  variables {
    manage_frontend_dns = false
  }

  assert {
    condition     = length(aws_route53_record.frontend) == 0
    error_message = "manage_frontend_dns=false must emit no Route53 record for domain_name."
  }

  assert {
    condition     = length(data.aws_route53_zone.frontend) == 0
    error_message = "manage_frontend_dns=false must perform no hosted-zone lookup."
  }
}
