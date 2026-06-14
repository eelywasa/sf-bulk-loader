# Frontend module assertions (SFBL-383 ACs).
#
# Two runs with different commands on purpose:
#   - behaviour_structure (plan): configuration-derived values. The mocked
#     APPLY returns null for optional+computed attributes like
#     cache_policy_id, discarding configured references, so the cache/origin
#     policy assertions read planned values and compare against the global
#     AWS-managed policy id constants the module pins.
#   - wiring_and_policy (apply): cross-resource references (OAC id,
#     distribution SourceArn, outputs) that only materialise in state.

mock_provider "aws" {
  # The provider validates bucket-policy JSON even under mocks - pin a valid doc.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

mock_provider "aws" {
  alias = "us_east_1"
}

variables {
  env_name                   = "test"
  domain_name                = "bulk-loader.example.com"
  certificate_arn            = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  backend_origin_domain_name = "api.bulk-loader.example.com"
}

run "behaviour_structure" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  # /api/* and /ws/* both present, uncached, all-methods, with the
  # AllViewerExceptHostHeader origin-request policy. A cached or missing
  # /ws/* breaks live run-status streaming.
  assert {
    condition = alltrue([
      for pattern in ["/api/*", "/ws/*"] :
      anytrue([
        for b in aws_cloudfront_distribution.frontend.ordered_cache_behavior :
        b.path_pattern == pattern &&
        b.cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" &&          # Managed-CachingDisabled
        b.origin_request_policy_id == "b689b0a8-53d0-40ab-baf2-68738e2966ac" && # Managed-AllViewerExceptHostHeader
        b.viewer_protocol_policy == "redirect-to-https" &&
        contains(b.allowed_methods, "POST") && contains(b.allowed_methods, "DELETE")
      ])
    ])
    error_message = "/api/* and /ws/* must target the backend origin uncached, all methods, AllViewerExceptHostHeader."
  }

  assert {
    # 658327ea... = Managed-CachingOptimized (global AWS-managed constant).
    condition     = aws_cloudfront_distribution.frontend.default_cache_behavior[0].cache_policy_id == "658327ea-f89d-4fab-a63d-7e88639e58f6" && aws_cloudfront_distribution.frontend.default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https"
    error_message = "The SPA default behaviour must be caching-optimized with HTTPS redirect."
  }

  # The backend origin must be HTTPS-only (TLS between CloudFront and ALB).
  assert {
    condition = anytrue([
      for o in aws_cloudfront_distribution.frontend.origin :
      o.domain_name == "api.bulk-loader.example.com" &&
      try(o.custom_origin_config[0].origin_protocol_policy, "") == "https-only"
    ])
    error_message = "The backend origin must use origin_protocol_policy https-only."
  }

  # SPA deep links: both 403 and 404 rewrite to /index.html with 200.
  assert {
    condition = alltrue([
      for code in [403, 404] :
      anytrue([
        for r in aws_cloudfront_distribution.frontend.custom_error_response :
        r.error_code == code && r.response_code == 200 && r.response_page_path == "/index.html"
      ])
    ])
    error_message = "403 and 404 must rewrite to /index.html with HTTP 200."
  }

  assert {
    condition     = aws_cloudfront_distribution.frontend.viewer_certificate[0].acm_certificate_arn == var.certificate_arn && aws_cloudfront_distribution.frontend.viewer_certificate[0].ssl_support_method == "sni-only"
    error_message = "The distribution must serve domain_name on the us-east-1 certificate (SNI)."
  }

  assert {
    condition     = contains(aws_cloudfront_distribution.frontend.aliases, "bulk-loader.example.com")
    error_message = "domain_name must be a distribution alias."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.frontend.block_public_acls &&
      aws_s3_bucket_public_access_block.frontend.block_public_policy &&
      aws_s3_bucket_public_access_block.frontend.ignore_public_acls &&
      aws_s3_bucket_public_access_block.frontend.restrict_public_buckets
    )
    error_message = "The frontend bucket must block all public access."
  }

  # NB the frontend domain_name Route53 alias (SFBL-390) lives in the ROOT
  # module (it needs depends_on = [module.backend]); its assertions are in
  # root_contract.tftest.hcl, not here.
}

run "wiring_and_policy" {
  command = apply

  module {
    source = "./modules/frontend"
  }

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  # The S3 origin must go through OAC (no legacy OAI, no public origin).
  assert {
    condition = anytrue([
      for o in aws_cloudfront_distribution.frontend.origin :
      o.domain_name == aws_s3_bucket.frontend.bucket_regional_domain_name &&
      o.origin_access_control_id == aws_cloudfront_origin_access_control.frontend.id
    ])
    error_message = "The S3 origin must be wired through the Origin Access Control."
  }

  # GetObject only for cloudfront.amazonaws.com scoped by SourceArn to THIS
  # distribution - not all of CloudFront.
  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.frontend.statement :
      s.sid == "AllowCloudFrontServicePrincipal" &&
      anytrue([for c in s.condition : c.variable == "AWS:SourceArn" && contains(c.values, aws_cloudfront_distribution.frontend.arn) && length(c.values) == 1])
    ])
    error_message = "The OAC bucket policy must scope GetObject to this distribution's SourceArn."
  }

  # The documented deploy must prune and invalidate.
  assert {
    condition     = strcontains(output.deploy_command, "--delete") && strcontains(output.deploy_command, "create-invalidation")
    error_message = "The deploy command must prune removed files and invalidate the distribution."
  }
}
