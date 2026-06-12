# Gold tier - HA production. Mirrors cdk.json context.tiers.gold, with the
# same rds_deletion_protection deviation as silver (no snapshot-on-destroy
# machinery in the Terraform flavour, so protection stays ON - see
# parity-deviation register, SFBL-384). The CDK gold flags workerEnabled /
# redisEnabled / wafEnabled are not provisioned by the CDK yet (SFBL-295)
# and are therefore absent here.
rds_instance_class         = "db.t4g.medium"
rds_multi_az               = true
rds_allocated_storage      = 100
rds_backup_retention_days  = 30
rds_deletion_protection    = true
ecs_desired_count          = 2
ecs_task_cpu               = 1024
ecs_task_memory            = 2048
log_retention_days         = 365
container_insights_enabled = true
input_retention_days       = 30
output_retention_days      = 90
use_fargate_spot           = true
