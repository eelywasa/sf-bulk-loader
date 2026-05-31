import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as ses from 'aws-cdk-lib/aws-ses';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { TierConfig, logRetentionFromDays } from './tier-config';

export interface DataStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.Vpc;
  backendServiceSecurityGroup: ec2.SecurityGroup;
  /** Bronze/Silver/Gold tier preset - drives RDS sizing, backups, S3 lifecycle. */
  tier: TierConfig;
  /**
   * ECR image tag the one-shot migration task runs (SFBL-298). Mirrors the tag
   * BackendStack's service task uses; defaults to "latest" in bin/app.ts.
   */
  ecrImageTag: string;
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
  // IDatabaseInstance, not DatabaseInstance: on restore the construct is an
  // rds.DatabaseInstanceFromSnapshot (SFBL-297), which shares the interface
  // but is a different concrete class.
  public readonly database: rds.IDatabaseInstance;
  public readonly inputBucket: s3.Bucket;
  public readonly outputBucket: s3.Bucket;
  // ISecret, not Secret: on restore (persistOnDestroy + restoreFromSnapshot)
  // these are imported by name via Secret.fromSecretNameV2 rather than created
  // (a retained secret name cannot be recreated). See SFBL-297.
  public readonly encryptionKeySecret: secretsmanager.ISecret;
  public readonly jwtSecretKeySecret: secretsmanager.ISecret;
  public readonly databaseUrlSecret: secretsmanager.ISecret;
  public readonly adminEmailSecret: secretsmanager.ISecret;
  public readonly adminPasswordSecret: secretsmanager.ISecret;
  /** SES domain identity ARN. Consumed by BackendStack for IAM scoping of ses:SendEmail. */
  public readonly sesIdentityArn: string;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);

    const env = props.envName;

    // SFBL-297: persistence controls.
    // `persist` - tier opts into snapshot-on-destroy + retained secrets/buckets.
    // `restoreFromSnapshot` - when an operator passes `-c restoreFromSnapshot=<id>`,
    // the RDS instance is rebuilt from that snapshot and the retained secrets are
    // imported by name rather than created (a retained secret name can't be
    // recreated). Only meaningful on a persist tier; see DECISIONS 028.
    const persist = props.tier.persistOnDestroy ?? false;
    const restoreFromSnapshot = this.node.tryGetContext('restoreFromSnapshot') as
      | string
      | undefined;

    // --- ECR Repository ---
    // Must exist before BackendStack so the ECS service can pull a real tag.
    // Operators push the initial image between DataStack and BackendStack
    // deploys; subsequent deploys use the migration-task pattern from SFBL-277.
    this.backendRepository = new ecr.Repository(this, 'BackendRepository', {
      repositoryName: `bulk-loader-backend-${env}`,
      removalPolicy: env === 'production'
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      // SFBL-300: without this, `cdk destroy` on a non-prod tier fails with
      // "repository ... cannot be deleted because it still contains images"
      // because the first-deploy runbook pushes the backend image here.
      // emptyOnDelete only takes effect under RemovalPolicy.DESTROY, so
      // production (RETAIN) is unaffected and its images are never purged.
      emptyOnDelete: env !== 'production',
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
    // and the destroy-time data controls are driven by the tier preset.
    //
    //   rdsMultiAz             - HA/cost decision (does the DB span 2 AZs?)
    //   rdsDeletionProtection  - block DeleteDBInstance + RETAIN the DB on destroy
    //   persistOnDestroy       - SNAPSHOT the DB on destroy (SFBL-297)
    //
    // Removal-policy precedence (see DECISIONS 028):
    //   persist            -> SNAPSHOT  (final snapshot taken, instance deleted)
    //   else protected     -> RETAIN    (instance orphaned, never auto-deleted)
    //   else               -> DESTROY   (instance wiped - disposable bronze)
    //
    // A SNAPSHOT removal policy cannot run while RDS deletion protection is on
    // (it blocks the DeleteDBInstance call), so persist forces deletionProtection
    // off; the final snapshot + retained secrets are the durability guard there.
    const dbRemovalPolicy = persist
      ? cdk.RemovalPolicy.SNAPSHOT
      : props.tier.rdsDeletionProtection
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY;
    const dbDeletionProtection = props.tier.rdsDeletionProtection && !persist;

    // Strip the leading "db." that operators naturally write in tier presets
    // (matching the form RDS APIs and the AWS console use). ec2.InstanceType
    // expects the bare class+size and CDK adds the "db." prefix itself when
    // synthesising RDS DBInstanceClass — without the strip we'd ship
    // "db.db.t4g.micro" and RDS rejects with InvalidParameter.
    const rdsInstanceClass = props.tier.rdsInstanceClass.replace(/^db\./, '');
    // Properties common to both the fresh-create and restore-from-snapshot
    // construct branches.
    const commonDbProps = {
      engine: dbEngine,
      parameterGroup: dbParameterGroup,
      instanceType: new ec2.InstanceType(rdsInstanceClass),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [dbSecurityGroup],
      // Always encrypt storage at rest. On restore this matches the snapshot's
      // own (encrypted) storage, so it is consistent rather than a conflict.
      storageEncrypted: true,
      // SFBL-355: gp3 over the CDK default gp2 - cheaper per GB and a higher
      // baseline IOPS floor; and apply minor engine patches in the maintenance
      // window so the instance doesn't drift onto an unpatched Postgres 16.x.
      storageType: rds.StorageType.GP3,
      autoMinorVersionUpgrade: true,
      multiAz: props.tier.rdsMultiAz,
      allocatedStorage: props.tier.rdsAllocatedStorage,
      maxAllocatedStorage: Math.max(props.tier.rdsAllocatedStorage * 5, 100),
      deletionProtection: dbDeletionProtection,
      removalPolicy: dbRemovalPolicy,
      backupRetention: cdk.Duration.days(props.tier.rdsBackupRetentionDays),
    };

    if (restoreFromSnapshot) {
      // SFBL-297 restore: rebuild the instance from the named snapshot. The
      // master username can't change on restore, so generate a fresh password
      // (stored in a new Secrets Manager secret) and reset it on the restored
      // instance. The operator repoints DATABASE_URL at the new endpoint +
      // password (see aws.md "Restore from snapshot"). NOTE: once restored, the
      // same snapshotIdentifier must keep being supplied on every later deploy
      // or CloudFormation recreates the DB empty.
      this.database = new rds.DatabaseInstanceFromSnapshot(this, 'Database', {
        ...commonDbProps,
        snapshotIdentifier: restoreFromSnapshot,
        credentials: rds.SnapshotCredentials.fromGeneratedSecret('bulk_loader_user'),
      });
    } else {
      this.database = new rds.DatabaseInstance(this, 'Database', {
        ...commonDbProps,
        databaseName: 'bulk_loader',
        // Credentials auto-generated in Secrets Manager under
        //   /{env}/bulk-loader/rds-credentials
        // The DATABASE_URL secret must reference this.
        credentials: rds.Credentials.fromGeneratedSecret('bulk_loader_user', {
          secretName: `/${env}/bulk-loader/rds-credentials`,
        }),
      });
    }

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

    // Input + output buckets: retain data on destroy for production AND any
    // persistOnDestroy tier (SFBL-297) so previously-uploaded CSVs survive a
    // teardown/restore cycle; disposable tiers clean up to avoid orphaned
    // buckets accumulating. Mirrors the frontend bucket and ECR repo policy.
    const dataBucketRetain = persist || env === 'production';
    const dataBucketRemoval = dataBucketRetain
      ? cdk.RemovalPolicy.RETAIN
      : cdk.RemovalPolicy.DESTROY;
    const dataBucketAutoDelete = !dataBucketRetain;

    // SFBL-355: S3 server access logging for the input/output buckets. A
    // dedicated log bucket captures who read/wrote which objects - an audit
    // trail the data buckets otherwise lack. It follows the same retention as
    // the data buckets and has no access logging of its own (avoiding a
    // delivery loop). CDK adds the bucket policy that lets the S3 logging
    // service deliver into it.
    const accessLogsBucket = new s3.Bucket(this, 'DataAccessLogsBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: dataBucketRemoval,
      autoDeleteObjects: dataBucketAutoDelete,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
    });

    // Input bucket: source CSV files uploaded by users or pipelines.
    this.inputBucket = new s3.Bucket(this, 'InputBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      versioned: false,
      removalPolicy: dataBucketRemoval,
      autoDeleteObjects: dataBucketAutoDelete,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: 'input/',
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
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: 'output/',
      lifecycleRules: outputLifecycle,
    });

    // --- Secrets Manager ---
    // These secrets are empty placeholders created by CDK.
    // Actual values must be provisioned before first ECS task start - see aws.md.
    // The ECS task definition (BackendStack) references these secrets by ARN
    // and injects them as environment variables into the container.
    //
    // SFBL-297 persistence: on a persistOnDestroy tier these carry
    // RemovalPolicy.RETAIN, so they survive `cdk destroy` - critical for the
    // EncryptionKey, without which a restored DB's Fernet-encrypted columns are
    // unrecoverable. On restore (`-c restoreFromSnapshot=<id>`) they are imported
    // by name rather than created, because a retained secret name cannot be
    // recreated by CloudFormation.
    const secretRemovalPolicy = persist
      ? cdk.RemovalPolicy.RETAIN
      : cdk.RemovalPolicy.DESTROY;
    const appSecret = (
      id: string,
      name: string,
      description: string,
    ): secretsmanager.ISecret => {
      if (restoreFromSnapshot) {
        return secretsmanager.Secret.fromSecretNameV2(this, id, name);
      }
      return new secretsmanager.Secret(this, id, {
        secretName: name,
        description,
        removalPolicy: secretRemovalPolicy,
      });
    };

    this.encryptionKeySecret = appSecret(
      'EncryptionKeySecret',
      `/${env}/bulk-loader/encryption-key`,
      'Fernet encryption key for stored Salesforce connection secrets (ENCRYPTION_KEY)',
    );
    this.jwtSecretKeySecret = appSecret(
      'JwtSecretKeySecret',
      `/${env}/bulk-loader/jwt-secret-key`,
      'JWT signing secret for in-app bearer token authentication (JWT_SECRET_KEY)',
    );
    // DATABASE_URL embeds the RDS endpoint, which changes on restore - so even
    // when retained, the operator must rewrite this to the new endpoint +
    // password after a snapshot restore (see aws.md "Restore from snapshot").
    this.databaseUrlSecret = appSecret(
      'DatabaseUrlSecret',
      `/${env}/bulk-loader/database-url`,
      'Full PostgreSQL asyncpg connection URL including credentials (DATABASE_URL)',
    );
    this.adminEmailSecret = appSecret(
      'AdminEmailSecret',
      `/${env}/bulk-loader/admin-email`,
      'Bootstrap admin email / login identifier for first-boot user seeding (ADMIN_EMAIL)',
    );
    this.adminPasswordSecret = appSecret(
      'AdminPasswordSecret',
      `/${env}/bulk-loader/admin-password`,
      'Bootstrap admin password for first-boot user seeding (ADMIN_PASSWORD)',
    );

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

    // --- SFBL-298: One-shot Alembic migration task definition ---
    // Relocated here from BackendStack so the migration task exists as soon as
    // the data layer (RDS + secrets + ECR repo) is up - before BackendStack is
    // synthesised. This closes the first-deploy chicken-and-egg where the
    // service task booted against an empty schema, crashlooped in
    // lifespan()/seed_admin, and hung BackendStack in CREATE_IN_PROGRESS (see
    // DECISIONS 027, now superseded).
    //
    // Fresh-account deploy order:
    //   1. cdk deploy Network Data   - RDS + secrets + ECR + this task def
    //   2. push the backend image to ECR
    //   3. aws ecs run-task <MigrationTaskDefinitionArn>  - schema reaches head
    //   4. cdk deploy Backend        - service boots against a populated schema
    //
    // The task shares the backend image, secrets, and SSM config of the service
    // task but sets RUN_MIGRATIONS=true and overrides the command to run
    // `alembic upgrade head` then exit. It is invoked out-of-band via
    // `aws ecs run-task` against the BackendStack cluster (run-task accepts any
    // cluster name on the command line, so no cross-stack cluster reference is
    // needed). An advisory lock in alembic/env.py serialises concurrent callers.
    // A bare ECS cluster dedicated to the migration task. On a fresh first
    // deploy BackendStack (which owns the service cluster) does not exist yet -
    // and that is precisely when the migration must run - so the migration task
    // needs its own cluster to run on. A Fargate-only cluster has no instance
    // cost, and because it carries no Fargate capacity-provider association it
    // is not subject to the teardown race fixed in SFBL-300 (the task is invoked
    // with `--launch-type FARGATE`, not a capacity-provider strategy).
    const migrationCluster = new ecs.Cluster(this, 'MigrationCluster', {
      vpc: props.vpc,
      clusterName: `bulk-loader-${env}-migration`,
    });

    const migrationLogGroup = new logs.LogGroup(this, 'MigrationLogGroup', {
      logGroupName: `/bulk-loader/${env}/migration`,
      retention: logRetentionFromDays(props.tier.logRetentionDays),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const migrationTaskRole = new iam.Role(this, 'MigrationTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: `Bulk Loader one-shot migration task role (${env})`,
    });

    const migrationTaskDefinition = new ecs.FargateTaskDefinition(this, 'MigrationTaskDef', {
      memoryLimitMiB: props.tier.ecsTaskMemory,
      cpu: props.tier.ecsTaskCpu,
      taskRole: migrationTaskRole,
    });

    // SSM Parameter Store references for the migration task's config injection.
    // Imported by name so ECS resolves the live value at task launch. These are
    // the same parameters the service task (BackendStack) injects - both stacks
    // import them independently; the import is idempotent.
    const corsOriginsParam = ssm.StringParameter.fromStringParameterName(
      this, 'CorsOriginsParam', `/${env}/bulk-loader/cors-origins`,
    );
    const logLevelParam = ssm.StringParameter.fromStringParameterName(
      this, 'LogLevelParam', `/${env}/bulk-loader/log-level`,
    );
    const adminUsernameParam = ssm.StringParameter.fromStringParameterName(
      this, 'AdminUsernameParam', `/${env}/bulk-loader/admin-username`,
    );
    const emailFromAddressParam = ssm.StringParameter.fromStringParameterName(
      this, 'EmailFromAddressParam', `/${env}/bulk-loader/email-from-address`,
    );
    const emailSesRegionParam = ssm.StringParameter.fromStringParameterName(
      this, 'EmailSesRegionParam', `/${env}/bulk-loader/email-ses-region`,
    );

    migrationTaskDefinition.addContainer('migration', {
      image: ecs.ContainerImage.fromEcrRepository(this.backendRepository, props.ecrImageTag),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'migration',
        logGroup: migrationLogGroup,
      }),
      // Same secret/SSM injection as the service task - the migration runs the
      // full app config validator at boot.
      secrets: {
        ENCRYPTION_KEY: ecs.Secret.fromSecretsManager(this.encryptionKeySecret),
        JWT_SECRET_KEY: ecs.Secret.fromSecretsManager(this.jwtSecretKeySecret),
        DATABASE_URL: ecs.Secret.fromSecretsManager(this.databaseUrlSecret),
        ADMIN_EMAIL: ecs.Secret.fromSecretsManager(this.adminEmailSecret),
        ADMIN_PASSWORD: ecs.Secret.fromSecretsManager(this.adminPasswordSecret),
        CORS_ORIGINS: ecs.Secret.fromSsmParameter(corsOriginsParam),
        LOG_LEVEL: ecs.Secret.fromSsmParameter(logLevelParam),
        ADMIN_USERNAME: ecs.Secret.fromSsmParameter(adminUsernameParam),
        EMAIL_FROM_ADDRESS: ecs.Secret.fromSsmParameter(emailFromAddressParam),
        EMAIL_SES_REGION: ecs.Secret.fromSsmParameter(emailSesRegionParam),
      },
      environment: {
        APP_DISTRIBUTION: 'aws_hosted',
        RUN_MIGRATIONS: 'true',
      },
      // Override the Dockerfile CMD: run alembic upgrade head then exit cleanly.
      command: ['sh', '-c', 'alembic upgrade head'],
    });

    // Grant the migration task's execution role the secret/SSM read access ECS
    // needs to inject the values above.
    this.encryptionKeySecret.grantRead(migrationTaskDefinition.executionRole!);
    this.jwtSecretKeySecret.grantRead(migrationTaskDefinition.executionRole!);
    this.databaseUrlSecret.grantRead(migrationTaskDefinition.executionRole!);
    this.adminEmailSecret.grantRead(migrationTaskDefinition.executionRole!);
    this.adminPasswordSecret.grantRead(migrationTaskDefinition.executionRole!);
    corsOriginsParam.grantRead(migrationTaskDefinition.executionRole!);
    logLevelParam.grantRead(migrationTaskDefinition.executionRole!);
    adminUsernameParam.grantRead(migrationTaskDefinition.executionRole!);
    emailFromAddressParam.grantRead(migrationTaskDefinition.executionRole!);
    emailSesRegionParam.grantRead(migrationTaskDefinition.executionRole!);

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
    // SFBL-298: migration task outputs live here now (relocated from BackendStack)
    // so `cdk deploy Network Data` already surfaces them before BackendStack exists.
    new cdk.CfnOutput(this, 'MigrationTaskDefinitionArn', {
      value: migrationTaskDefinition.taskDefinitionArn,
      description: 'ARN of the one-shot Alembic migration task - invoke via aws ecs run-task before deploying BackendStack and before each subsequent rollout',
      exportName: `${this.stackName}-MigrationTaskDefinitionArn`,
    });
    new cdk.CfnOutput(this, 'BackendServiceSecurityGroupId', {
      value: props.backendServiceSecurityGroup.securityGroupId,
      description: 'Security group ID for ECS tasks - pass to aws ecs run-task --network-configuration',
    });
    new cdk.CfnOutput(this, 'MigrationClusterName', {
      value: migrationCluster.clusterName,
      description: 'ECS cluster for the one-shot migration task - pass to aws ecs run-task --cluster',
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
