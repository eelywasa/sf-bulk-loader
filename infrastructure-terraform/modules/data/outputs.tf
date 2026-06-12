output "ecr_repository_url" {
  description = "ECR repository URI - mirror the published GHCR backend image here before applying the backend module."
  value       = aws_ecr_repository.backend.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN - for the backend execution role's pull policy."
  value       = aws_ecr_repository.backend.arn
}

output "rds_endpoint" {
  description = "RDS endpoint (host:port)."
  value       = aws_db_instance.main.endpoint
}

output "rds_credentials_secret_arn" {
  description = "Secrets Manager ARN of the generated RDS master credentials."
  value       = aws_secretsmanager_secret.rds_credentials.arn
}

output "injected_secret_arns" {
  description = "Map of env var name -> Secrets Manager ARN injected into every task (the five app secrets)."
  value       = local.injected_secret_arns
}

output "injected_ssm_arns" {
  description = "Map of env var name -> SSM parameter ARN injected into every task (the five config parameters)."
  value       = local.injected_ssm_arns
}

output "input_bucket_name" {
  description = "S3 bucket for input CSV files."
  value       = aws_s3_bucket.data["input"].id
}

output "output_bucket_name" {
  description = "S3 bucket for Bulk API result files."
  value       = aws_s3_bucket.data["output"].id
}

output "access_logs_bucket_name" {
  description = "S3 bucket receiving server-access logs for the data buckets."
  value       = aws_s3_bucket.access_logs.id
}

output "ses_identity_arn" {
  description = "SES email identity ARN - the backend task role's send permission is scoped to it."
  value       = local.ses_identity_arn
}

output "ses_dkim_tokens" {
  description = "DKIM CNAME tokens to add to DNS (token._domainkey.<domain> -> token.dkim.amazonses.com). Empty when adopting an existing identity."
  value       = local.ses_dkim_tokens
}

output "migration_cluster_name" {
  description = "Dedicated Fargate cluster for the one-shot migration task."
  value       = aws_ecs_cluster.migration.name
}

output "migration_task_definition_arn" {
  description = "Run with: aws ecs run-task --cluster <migration_cluster_name> --task-definition <this> --launch-type FARGATE ... (see the deployment guide)."
  value       = aws_ecs_task_definition.migration.arn
}

output "migration_log_group" {
  description = "CloudWatch log group the migration task writes to."
  value       = aws_cloudwatch_log_group.migration.name
}
