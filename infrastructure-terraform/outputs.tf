# Root outputs - the operator-facing handles for the staged first deploy
# (mirror image -> populate secrets -> run migration -> apply backend).

output "ecr_repository_url" {
  description = "Mirror the published GHCR backend image here before applying the backend module."
  value       = module.data.ecr_repository_url
}

output "rds_endpoint" {
  description = "RDS endpoint (host:port)."
  value       = module.data.rds_endpoint
}

output "input_bucket_name" {
  description = "S3 bucket for input CSV files."
  value       = module.data.input_bucket_name
}

output "output_bucket_name" {
  description = "S3 bucket for Bulk API result files."
  value       = module.data.output_bucket_name
}

output "ses_dkim_tokens" {
  description = "DKIM CNAME tokens to add to DNS to verify the SES identity (empty when adopting an existing identity)."
  value       = module.data.ses_dkim_tokens
}

output "migration_run_command" {
  description = "Template for the one-shot Alembic migration (fill in the subnet/SG ids from the network outputs; see the deployment guide)."
  value       = "aws ecs run-task --cluster ${module.data.migration_cluster_name} --task-definition ${module.data.migration_task_definition_arn} --launch-type FARGATE --network-configuration 'awsvpcConfiguration={subnets=[${join(",", module.network.public_subnet_ids)}],securityGroups=[${module.network.backend_service_security_group_id}],assignPublicIp=ENABLED}'"
}
