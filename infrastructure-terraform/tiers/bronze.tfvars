# Bronze tier - disposable/dev environments. Mirrors cdk.json context.tiers.bronze.
rds_instance_class         = "db.t4g.micro"
rds_multi_az               = false
rds_allocated_storage      = 20
rds_backup_retention_days  = 7
rds_deletion_protection    = false
ecs_desired_count          = 1
ecs_task_cpu               = 512
ecs_task_memory            = 1024
log_retention_days         = 7
container_insights_enabled = false
input_retention_days       = 7
output_retention_days      = 30
use_fargate_spot           = false
