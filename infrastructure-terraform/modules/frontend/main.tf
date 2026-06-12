# Frontend module - CloudFront + S3 static hosting for the aws_hosted
# distribution. Mirrors infrastructure/lib/frontend-stack.ts:
#
#   Browser -> CloudFront -> /api/* -> backend origin hostname -> ALB -> Fargate
#                          -> /ws/*  -> backend origin hostname -> ALB -> Fargate
#                          -> /*     -> S3 -> React SPA
#
# TLS is terminated at CloudFront (wss:// at the client, ws:// at the ALB
# internally). The certificate must be in us-east-1 regardless of the deploy
# region - hence the aws.us_east_1 provider on the distribution.
#
# The Vite build upload is an operator/CI step, not Terraform-managed (the
# CDK uses BucketDeployment): sync frontend/dist with --delete so removed
# files are pruned, then invalidate /*. The exact commands are emitted as
# the frontend_deploy_command output.

# --- S3 bucket - static frontend assets (deploy region) ---
# Not public - CloudFront reaches it via Origin Access Control only.

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "frontend" {
  bucket        = "bulk-loader-${var.env_name}-${data.aws_caller_identity.current.account_id}-frontend"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- CloudFront Origin Access Control ---
# OAC replaces the legacy Origin Access Identity (OAI) pattern.

resource "aws_cloudfront_origin_access_control" "frontend" {
  provider = aws.us_east_1

  name                              = "bulk-loader-${var.env_name}-frontend"
  description                       = "Bulk Loader frontend OAC (${var.env_name})"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# --- Distribution ---

locals {
  # AWS-managed CloudFront policy ids - global, stable constants published in
  # the CloudFront docs (identical in every account and region). Referenced
  # directly rather than via data lookups so the native test suite can assert
  # behaviour wiring from plan values.
  cache_policy_caching_optimized        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # Managed-CachingOptimized
  cache_policy_caching_disabled         = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
  origin_request_all_viewer_except_host = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # Managed-AllViewerExceptHostHeader
  s3_origin_id                          = "frontend-s3"
  alb_origin_id                         = "backend-alb"

  # /api/* and /ws/* share identical behaviour settings: never cached, all
  # methods, every viewer header except Host forwarded (the origin must see
  # its own hostname so the ALB certificate matches).
  backend_path_patterns = ["/api/*", "/ws/*"]
}

resource "aws_cloudfront_distribution" "frontend" {
  provider = aws.us_east_1

  comment             = "Salesforce Bulk Loader - ${var.env_name}"
  enabled             = true
  default_root_object = "index.html"
  aliases             = [var.domain_name]

  origin {
    origin_id                = local.s3_origin_id
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  # CloudFront connects to the ALB using a hostname covered by the ALB
  # certificate - HTTPS only.
  origin {
    origin_id   = local.alb_origin_id
    domain_name = var.backend_origin_domain_name

    custom_origin_config {
      origin_protocol_policy = "https-only"
      http_port              = 80
      https_port             = 443
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # Default: serve the React SPA from S3.
  default_cache_behavior {
    target_origin_id       = local.s3_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = local.cache_policy_caching_optimized
  }

  # /api/* and /ws/* -> ALB -> Fargate. Not cached; WebSocket connections
  # are proxied through, not terminated.
  dynamic "ordered_cache_behavior" {
    for_each = local.backend_path_patterns

    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = local.alb_origin_id
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods           = ["GET", "HEAD", "OPTIONS"]
      cache_policy_id          = local.cache_policy_caching_disabled
      origin_request_policy_id = local.origin_request_all_viewer_except_host
    }
  }

  # SPA routing: 403/404 from S3 rewrite to /index.html so React Router
  # handles deep links.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  viewer_certificate {
    acm_certificate_arn      = var.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
}

# --- Bucket policy ---
# GetObject restricted to THIS distribution via AWS:SourceArn - not all of
# CloudFront - plus the HTTPS-only deny. Applied after both bucket and
# distribution exist (the policy references the distribution ARN).

data "aws_iam_policy_document" "frontend" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.frontend.arn,
      "${aws_s3_bucket.frontend.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "AllowCloudFrontServicePrincipal"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.frontend.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend.json

  depends_on = [aws_s3_bucket_public_access_block.frontend]
}
