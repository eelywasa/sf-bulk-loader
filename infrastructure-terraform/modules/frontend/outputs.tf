output "distribution_domain_name" {
  description = "CloudFront distribution domain (point your DNS at this, or use the custom domain alias)."
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "distribution_id" {
  description = "CloudFront distribution ID - needed for cache invalidation on deploy."
  value       = aws_cloudfront_distribution.frontend.id
}

output "frontend_bucket_name" {
  description = "S3 bucket for frontend static assets."
  value       = aws_s3_bucket.frontend.id
}

output "deploy_command" {
  description = "Upload the Vite build and invalidate the cache. --delete prunes files removed from dist/ (parity with the CDK BucketDeployment prune)."
  value       = "cd frontend && npm run build && aws s3 sync dist/ s3://${aws_s3_bucket.frontend.id}/ --delete && aws cloudfront create-invalidation --distribution-id ${aws_cloudfront_distribution.frontend.id} --paths '/*'"
}
