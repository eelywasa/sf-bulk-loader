import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export interface DataStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.Vpc;
  /** RDS instance class string, e.g. 'db.t3.medium'. */
  rdsInstanceClass: string;
}

/**
 * DataStack — persistent data layer for the aws_hosted distribution.
 *
 * Provisions:
 *   - RDS PostgreSQL instance in private subnets
 *   - S3 bucket for input CSV files (source data)
 *   - S3 bucket for output/results files
 *   - Secrets Manager secrets for all sensitive runtime configuration
 *
 * Secrets Manager mapping (injected into ECS task as environment variables):
 *   /{env}/bulk-loader/encryption-key  → ENCRYPTION_KEY
 *   /{env}/bulk-loader/jwt-secret-key  → JWT_SECRET_KEY
 *   /{env}/bulk-loader/database-url    → DATABASE_URL
 *   /{env}/bulk-loader/admin-password  → ADMIN_PASSWORD
 *
 * Provision actual secret values before first ECS deployment:
 *   aws secretsmanager put-secret-value \
 *     --secret-id /{env}/bulk-loader/encryption-key \
 *     --secret-string "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
 */
export class DataStack extends cdk.Stack {
  public readonly database: rds.DatabaseInstance;
  public readonly inputBucket: s3.Bucket;
  public readonly outputBucket: s3.Bucket;
  public readonly encryptionKeySecret: secretsmanager.Secret;
  public readonly jwtSecretKeySecret: secretsmanager.Secret;
  public readonly databaseUrlSecret: secretsmanager.Secret;
  public readonly adminPasswordSecret: secretsmanager.Secret;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);

    const env = props.envName;

    // --- RDS PostgreSQL ---
    // Placed in isolated subnets — no internet route, reachable from within the VPC only.
    // The aws_hosted profile requires a PostgreSQL DATABASE_URL — SQLite is rejected at startup.
    const dbSecurityGroup = new ec2.SecurityGroup(this, 'DbSecurityGroup', {
      vpc: props.vpc,
      description: 'Allow PostgreSQL access from ECS tasks',
      allowAllOutbound: false,
    });

    this.database = new rds.DatabaseInstance(this, 'Database', {
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_16,
      }),
      instanceType: new ec2.InstanceType(props.rdsInstanceClass),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [dbSecurityGroup],
      databaseName: 'bulk_loader',
      // Credentials auto-generated in Secrets Manager under:
      //   /rds-db-credentials/cluster-...  (managed by RDS)
      // The DATABASE_URL secret in /{env}/bulk-loader/database-url must reference this.
      credentials: rds.Credentials.fromGeneratedSecret('bulk_loader_user', {
        secretName: `/${env}/bulk-loader/rds-credentials`,
      }),
      multiAz: env === 'production',
      allocatedStorage: 20,
      maxAllocatedStorage: 100,
      deletionProtection: env === 'production',
      removalPolicy: env === 'production'
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      backupRetention: env === 'production'
        ? cdk.Duration.days(7)
        : cdk.Duration.days(1),
    });

    // --- S3 Buckets ---
    // Input bucket: source CSV files uploaded by users or pipelines.
    this.inputBucket = new s3.Bucket(this, 'InputBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      versioned: false,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      // TODO: add lifecycle rule to expire old input files after N days
    });

    // Output bucket: Bulk API result files downloaded by the orchestrator.
    this.outputBucket = new s3.Bucket(this, 'OutputBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      versioned: false,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [
        {
          // TODO: tune retention period per operational requirements
          expiration: cdk.Duration.days(90),
          id: 'expire-old-results',
        },
      ],
    });

    // --- Secrets Manager ---
    // These secrets are empty placeholders created by CDK.
    // Actual values must be provisioned before first ECS task start — see aws.md.
    // The ECS task definition (BackendStack) references these secrets by ARN
    // and injects them as environment variables into the container.

    this.encryptionKeySecret = new secretsmanager.Secret(this, 'EncryptionKeySecret', {
      secretName: `/${env}/bulk-loader/encryption-key`,
      description: 'Fernet encryption key for stored Salesforce connection secrets (ENCRYPTION_KEY)',
    });

    this.jwtSecretKeySecret = new secretsmanager.Secret(this, 'JwtSecretKeySecret', {
      secretName: `/${env}/bulk-loader/jwt-secret-key`,
      description: 'JWT signing secret for in-app bearer token authentication (JWT_SECRET_KEY)',
    });

    this.databaseUrlSecret = new secretsmanager.Secret(this, 'DatabaseUrlSecret', {
      secretName: `/${env}/bulk-loader/database-url`,
      description: 'Full PostgreSQL asyncpg connection URL including credentials (DATABASE_URL)',
      // Format: postgresql+asyncpg://user:password@rds-endpoint:5432/bulk_loader?ssl=require
    });

    this.adminPasswordSecret = new secretsmanager.Secret(this, 'AdminPasswordSecret', {
      secretName: `/${env}/bulk-loader/admin-password`,
      description: 'Bootstrap admin password for first-boot user seeding (ADMIN_PASSWORD)',
    });

    // --- Outputs ---
    new cdk.CfnOutput(this, 'InputBucketName', {
      value: this.inputBucket.bucketName,
      description: 'S3 bucket for input CSV files',
    });
    new cdk.CfnOutput(this, 'OutputBucketName', {
      value: this.outputBucket.bucketName,
      description: 'S3 bucket for Bulk API result files',
    });
    new cdk.CfnOutput(this, 'RdsEndpoint', {
      value: this.database.dbInstanceEndpointAddress,
      description: 'RDS PostgreSQL endpoint',
    });
  }
}
