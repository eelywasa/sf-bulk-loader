# S3 buckets - input CSVs, Bulk API result output, and a dedicated
# access-logs bucket capturing who read/wrote which objects in the data
# buckets (an audit trail they otherwise lack). The access-logs bucket has
# no logging of its own, avoiding a delivery loop.
#
# All three: SSE-S3 encryption, all public access blocked, HTTPS-only
# bucket policy. Lifecycle expiry comes from the tier; 0 means retain
# forever (no rule emitted).

# --- Access-logs bucket ---

resource "aws_s3_bucket" "access_logs" {
  bucket        = "${local.bucket_prefix}-access-logs"
  force_destroy = !var.protect_data
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# HTTPS-only + the grant that lets the S3 server-access-logging service
# deliver log objects here (scoped to this account's source buckets).
data "aws_iam_policy_document" "access_logs" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.access_logs.arn,
      "${aws_s3_bucket.access_logs.arn}/*",
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
    sid       = "S3ServerAccessLogsPolicy"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.access_logs.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_s3_bucket_policy" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  policy = data.aws_iam_policy_document.access_logs.json

  depends_on = [aws_s3_bucket_public_access_block.access_logs]
}

# --- Input + output data buckets ---

locals {
  data_buckets = {
    input = {
      retention_days = var.input_retention_days
      log_prefix     = "input/"
    }
    output = {
      retention_days = var.output_retention_days
      log_prefix     = "output/"
    }
  }
}

resource "aws_s3_bucket" "data" {
  for_each = local.data_buckets

  bucket        = "${local.bucket_prefix}-${each.key}"
  force_destroy = !var.protect_data
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = local.data_buckets

  bucket = aws_s3_bucket.data[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each = local.data_buckets

  bucket = aws_s3_bucket.data[each.key].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "data_https_only" {
  for_each = local.data_buckets

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.data[each.key].arn,
      "${aws_s3_bucket.data[each.key].arn}/*",
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
}

resource "aws_s3_bucket_policy" "data" {
  for_each = local.data_buckets

  bucket = aws_s3_bucket.data[each.key].id
  policy = data.aws_iam_policy_document.data_https_only[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.data]
}

resource "aws_s3_bucket_logging" "data" {
  for_each = local.data_buckets

  bucket        = aws_s3_bucket.data[each.key].id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = each.value.log_prefix
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  # 0 = retain forever: no lifecycle configuration at all.
  for_each = { for k, v in local.data_buckets : k => v if v.retention_days > 0 }

  bucket = aws_s3_bucket.data[each.key].id

  rule {
    id     = "expire-after-${each.value.retention_days}-days"
    status = "Enabled"

    filter {}

    expiration {
      days = each.value.retention_days
    }
  }
}
