import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as ses from 'aws-cdk-lib/aws-ses';
import { Construct } from 'constructs';
import { TierConfig } from './tier-config';

export interface DataStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.Vpc;
  backendServiceSecurityGroup: ec2.SecurityGroup;
  /** Bronze/Silver/Gold tier preset - drives RDS sizing, backups, S3 lifecycle. */
  tier: TierConfig;
  /**
   * Route53 hosted zone domain (e.g. "your-domain.example"). Used for both
   * the backend ALB alias record (consumed by BackendStack) and the SES
   * EmailIdentity DKIM/MAIL-FROM records provisioned in this stack.
   */
  hostedZoneDomain: string;
  /**
   * SES identity domain. Defaults to hostedZoneDomain. Override only if the
   * sender domain differs from the application domain - e.g. emails go from
   * "mail.example.com" but the app runs at "bulk.example.com".
   */
  sesIdentityDomain?: string;
  /**
   * If true, adopt an existing verified SES identity for sesIdentityDomain
   * rather than creating a new one. Use this when the SES identity is
   * already verified (e.g. via the SES console or a prior deployment) and
   * CloudFormation would otherwise refuse to create a duplicate. Defaults
   * to false (CDK creates a new identity, which is the right move for a
   * fresh AWS account).
   */
  sesIdentityAdoptExisting?: boolean;
}

/**
 * DataStack - persistent data layer for the aws_hosted distribution.
 *
 * Provisions:
 *   - ECR repository for the backend Docker image (must exist before BackendStack
 *     deploys, so the ECS service can pull a real tag - see SFBL-276)
 *   - RDS PostgreSQL instance in private subnets
 *   - S3 bucket for input CSV files (source data)
 *   - S3 bucket for output/results files
 *   - Secrets Manager secrets for all sensitive runtime configuration
 *
 * Secrets Manager mapping (injected into ECS task as environment variables):
 *   /{env}/bulk-loader/encryption-key  → ENCRYPTION_KEY
 *   /{env}/bulk-loader/jwt-secret-key  → JWT_SECRET_KEY
 *   /{env}/bulk-loader/database-url    → DATABASE_URL
 *   /{env}/bulk-loader/admin-email     → ADMIN_EMAIL    (used on first boot only)
 *   /{env}/bulk-loader/admin-password  → ADMIN_PASSWORD (used on first boot only)
 *
 * Provision actual secret values before first ECS deployment:
 *   aws secretsmanager put-secret-value \
 *     --secret-id /{env}/bulk-loader/encryption-key \
 *     --secret-string "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
 *
 * Deploy order (per SFBL-276 first-deploy fix):
 *   1. NetworkStack   - VPC + security groups
 *   2. DataStack      - ECR + RDS + S3 + Secrets Manager  ← (this stack)
 *   3. (operator)     - push the initial backend image to ECR
 *   4. BackendStack   - ECS service consumes the existing repository
 *   5. FrontendStack  - CloudFront + S3 + BucketDeployment
 */
export class DataStack extends cdk.Stack {
  public readonly backendRepository: ecr.Repository;
  public readonly database: rds.DatabaseInstance;
  public readonly inputBucket: s3.Bucket;
  public readonly outputBucket: s3.Bucket;
  public readonly encryptionKeySecret: secretsmanager.Secret;
  public readonly jwtSecretKeySecret: secretsmanager.Secret;
  public readonly databaseUrlSecret: secretsmanager.Secret;
  public readonly adminEmailSecret: secretsmanager.Secret;
  public readonly adminPasswordSecret: secretsmanager.Secret;
  /** SES domain identity ARN. Consumed by BackendStack for IAM scoping of ses:SendEmail. */
  public readonly sesIdentityArn: string;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);

    const env = props.envName;

    // --- ECR Repository ---
    // Must exist before BackendStack so the ECS service can pull a real tag.
    // Operators push the initial image between DataStack and BackendStack
    // deploys; subsequent deploys use the migration-task pattern from SFBL-277.
    this.backendRepository = new ecr.Repository(this, 'BackendRepository', {
      repositoryName: `bulk-loader-backend-${env}`,
      removalPolicy: env === 'production'
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      lifecycleRules: [
        {
          // Retain only the 10 most recent images to control storage costs.
          maxImageCount: 10,
          description: 'Keep last 10 images',
        },
      ],
    });

    // --- RDS PostgreSQL ---
    // Placed in isolated subnets - no internet route, reachable from within the VPC only.
    // The aws_hosted profile requires a PostgreSQL DATABASE_URL - SQLite is rejected at startup.
    const dbSecurityGroup = new ec2.SecurityGroup(this, 'DbSecurityGroup', {
      vpc: props.vpc,
      description: 'Allow PostgreSQL access from ECS tasks',
      allowAllOutbound: false,
    });
    dbSecurityGroup.addIngressRule(
      props.backendServiceSecurityGroup,
      ec2.Port.tcp(5432),
      'Allow PostgreSQL from ECS tasks',
    );

    // Custom parameter group - enforces TLS at the server (rds.force_ssl=1).
    // Without this, Postgres accepts non-SSL connections even though the
    // application connects with ?ssl=require; an attacker who reaches the
    // VPC could downgrade the connection. The parameter group also gives us
    // a lever for future tuning (shared_buffers, log_min_duration_statement,
    // etc.) without recreating the DB.
    const dbEngine = rds.DatabaseInstanceEngine.postgres({
      version: rds.PostgresEngineVersion.VER_16,
    });
    const dbParameterGroup = new rds.ParameterGroup(this, 'DbParameterGroup', {
      engine: dbEngine,
      description: `Bulk Loader Postgres 16 parameter group (${env}) - force_ssl enforced`,
      parameters: {
        // Reject any connection that doesn't negotiate TLS. Application
        // already connects with sslmode=require; this closes the loophole
        // where a misconfigured client could connect in plaintext.
        'rds.force_ssl': '1',
      },
    });

    // RDS shape (instance class, Multi-AZ, allocated storage, backup retention)
    // and the deletion-protection / retention safety controls are all driven
    // by the tier preset, but they are independent fields:
    //
    //   rdsMultiAz             - HA/cost decision (does the DB span 2 AZs?)
    //   rdsDeletionProtection  - data-loss guard (block DeleteDBInstance and
    //                            keep the DB on stack destroy)
    //
    // Coupling these would mean Silver (single-AZ but real production data)
    // loses its protection, which would be a regression from the previous
    // env==='production' check. Bronze opts out for cheap dev teardown.
    // Strip the leading "db." that operators naturally write in tier presets
    // (matching the form RDS APIs and the AWS console use). ec2.InstanceType
    // expects the bare class+size and CDK adds the "db." prefix itself when
    // synthesising RDS DBInstanceClass — without the strip we'd ship
    // "db.db.t4g.micro" and RDS rejects with InvalidParameter.
    const rdsInstanceClass = props.tier.rdsInstanceClass.replace(/^db\./, '');
    this.database = new rds.DatabaseInstance(this, 'Database', {
      engine: dbEngine,
      parameterGroup: dbParameterGroup,
      instanceType: new ec2.InstanceType(rdsInstanceClass),
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
      // Always encrypt storage at rest. CDK defaults to encryption-on for
      // most engines but we set it explicitly to make the security posture
      // visible in the synthesised template.
      storageEncrypted: true,
      multiAz: props.tier.rdsMultiAz,
      allocatedStorage: props.tier.rdsAllocatedStorage,
      maxAllocatedStorage: Math.max(props.tier.rdsAllocatedStorage * 5, 100),
      deletionProtection: props.tier.rdsDeletionProtection,
      removalPolicy: props.tier.rdsDeletionProtection
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      backupRetention: cdk.Duration.days(props.tier.rdsBackupRetentionDays),
    });

    // --- S3 Buckets ---
    // Lifecycle expiration is driven by the tier preset. A value of 0 means
    // "retain forever" (no lifecycle rule emitted). Defaults: Bronze 7d / 30d
    // input/output, Silver 30d / 90d, Gold 30d / 90d. See
    // docs/deployment/aws.md "Sizing and cost".
    const inputLifecycle: s3.LifecycleRule[] =
      props.tier.inputRetentionDays > 0
        ? [
            {
              id: 'expire-old-input',
              expiration: cdk.Duration.days(props.tier.inputRetentionDays),
            },
          ]
        : [];
    const outputLifecycle: s3.LifecycleRule[] =
      props.tier.outputRetentionDays > 0
        ? [
            {
              id: 'expire-old-output',
              expiration: cdk.Duration.days(props.tier.outputRetentionDays),
            },
          ]
        : [];

    // Input + output buckets: production retains data on stack destroy; non-prod
    // tiers (staging, dev) clean up to avoid orphaned buckets accumulating across
    // deploy/destroy cycles. Mirrors the frontend bucket and ECR repo policy.
    const dataBucketRemoval =
      env === 'production' ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY;
    const dataBucketAutoDelete = env !== 'production';

    // Input bucket: source CSV files uploaded by users or pipelines.
    this.inputBucket = new s3.Bucket(this, 'InputBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      versioned: false,
      removalPolicy: dataBucketRemoval,
      autoDeleteObjects: dataBucketAutoDelete,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: inputLifecycle,
    });

    // Output bucket: Bulk API result files downloaded by the orchestrator.
    this.outputBucket = new s3.Bucket(this, 'OutputBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      versioned: false,
      removalPolicy: dataBucketRemoval,
      autoDeleteObjects: dataBucketAutoDelete,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: outputLifecycle,
    });

    // --- Secrets Manager ---
    // These secrets are empty placeholders created by CDK.
    // Actual values must be provisioned before first ECS task start - see aws.md.
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

    this.adminEmailSecret = new secretsmanager.Secret(this, 'AdminEmailSecret', {
      secretName: `/${env}/bulk-loader/admin-email`,
      description: 'Bootstrap admin email / login identifier for first-boot user seeding (ADMIN_EMAIL)',
    });

    this.adminPasswordSecret = new secretsmanager.Secret(this, 'AdminPasswordSecret', {
      secretName: `/${env}/bulk-loader/admin-password`,
      description: 'Bootstrap admin password for first-boot user seeding (ADMIN_PASSWORD)',
    });

    // --- SES - domain identity for application-sent email ---
    // The SES backend (app/services/email/backends/ses.py) sends via
    // SES v2 SendEmail using credentials from the boto3 default chain
    // (the ECS task role). Without a verified identity SES rejects with
    // MailFromDomainNotVerifiedException / MessageRejected.
    //
    // Two paths:
    //
    // 1. Fresh account (sesIdentityAdoptExisting=false / unset, default).
    //    CDK creates a new ses.EmailIdentity for the configured domain
    //    with DKIM enabled and a mail.<domain> MAIL FROM. Operator adds
    //    the DKIM CNAMEs (surfaced via the SesDkimRecord1/2/3 outputs)
    //    to DNS to complete verification.
    //
    // 2. Pre-existing verified identity (sesIdentityAdoptExisting=true).
    //    CDK references the existing identity by name without trying to
    //    create or modify it. CloudFormation otherwise refuses to create
    //    a duplicate identity for the same domain - this branch is the
    //    answer for accounts that have already verified the domain via
    //    the SES console or an earlier deployment. No DKIM outputs in
    //    this branch - operator already configured DNS when they verified
    //    the identity.
    //
    // We use ses.Identity.domain (not Identity.publicHostedZone) so the
    // synth step doesn't require Route53 hosted-zone lookup - that lookup
    // runs against the deploying account during synth and fails in CI/dev
    // with placeholder zone names.
    const sesDomain = props.sesIdentityDomain ?? props.hostedZoneDomain;
    let sesIdentity: ses.IEmailIdentity;
    let sesIdentityCreated = false;
    if (props.sesIdentityAdoptExisting) {
      sesIdentity = ses.EmailIdentity.fromEmailIdentityName(this, 'SesIdentity', sesDomain);
    } else {
      const created = new ses.EmailIdentity(this, 'SesIdentity', {
        identity: ses.Identity.domain(sesDomain),
        mailFromDomain: `mail.${sesDomain}`,
      });
      sesIdentity = created;
      sesIdentityCreated = true;
    }
    this.sesIdentityArn = sesIdentity.emailIdentityArn;

    // --- Outputs ---
    new cdk.CfnOutput(this, 'EcrRepositoryUri', {
      value: this.backendRepository.repositoryUri,
      description: 'ECR repository URI - push the backend image here before deploying BackendStack',
      exportName: `${this.stackName}-EcrRepositoryUri`,
    });
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
    // Output the parameter group's CFN ref (resolves to the generated group name)
    // so operators can verify the DB is using the force_ssl group post-deploy.
    new cdk.CfnOutput(this, 'RdsParameterGroupName', {
      value: (dbParameterGroup.node.defaultChild as rds.CfnDBParameterGroup).ref,
      description: 'RDS parameter group name (force_ssl=1 - server-enforced TLS)',
    });
    new cdk.CfnOutput(this, 'SesIdentityArn', {
      value: this.sesIdentityArn,
      description: 'SES domain identity ARN (sender identity for application email)',
      exportName: `${this.stackName}-SesIdentityArn`,
    });
    new cdk.CfnOutput(this, 'SesIdentityDomain', {
      value: sesDomain,
      description: sesIdentityCreated
        ? 'SES domain identity name - add the DKIM CNAMEs below to DNS to verify'
        : 'SES domain identity name (adopted, already verified)',
    });
    // DKIM CNAMEs are only meaningful when CDK creates the identity. When
    // adopting an existing identity, the DNS records are already in place
    // and the construct doesn't expose the DKIM tokens.
    if (sesIdentityCreated) {
      const created = sesIdentity as ses.EmailIdentity;
      // Surface the three DKIM CNAMEs the operator must add to DNS to
      // complete identity verification. Token shape:
      //   <token>._domainkey.<domain>  CNAME  <token>.dkim.amazonses.com
      new cdk.CfnOutput(this, 'SesDkimRecord1', {
        value: `${created.dkimDnsTokenName1}._domainkey.${sesDomain} CNAME ${created.dkimDnsTokenValue1}`,
        description: 'SES DKIM record 1 of 3 - add to DNS as CNAME',
      });
      new cdk.CfnOutput(this, 'SesDkimRecord2', {
        value: `${created.dkimDnsTokenName2}._domainkey.${sesDomain} CNAME ${created.dkimDnsTokenValue2}`,
        description: 'SES DKIM record 2 of 3 - add to DNS as CNAME',
      });
      new cdk.CfnOutput(this, 'SesDkimRecord3', {
        value: `${created.dkimDnsTokenName3}._domainkey.${sesDomain} CNAME ${created.dkimDnsTokenValue3}`,
        description: 'SES DKIM record 3 of 3 - add to DNS as CNAME',
      });
    }
  }
}
