# Silver tier - standard production. Mirrors cdk.json context.tiers.silver,
# except rds_deletion_protection: the CDK turns it off because its
# snapshot-on-destroy persistence machinery (persistOnDestroy, DECISIONS 028)
# is the durability guard there. That machinery is not ported to Terraform,
# so deletion protection is the only data-loss guard for a tier holding real
# data - deliberately ON here (parity-deviation register, SFBL-384).
rds_instance_class         = "db.t4g.small"
rds_multi_az               = false
rds_allocated_storage      = 20
rds_backup_retention_days  = 7
rds_deletion_protection    = true
rds_skip_final_snapshot    = false
ecs_desired_count          = 2
ecs_task_cpu               = 512
ecs_task_memory            = 1024
log_retention_days         = 30
container_insights_enabled = true
input_retention_days       = 30
output_retention_days      = 90
use_fargate_spot           = false
