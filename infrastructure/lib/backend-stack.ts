import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { TierConfig, logRetentionFromDays } from './tier-config';

export interface BackendStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.Vpc;
  albSecurityGroup: ec2.SecurityGroup;
  backendServiceSecurityGroup: ec2.SecurityGroup;
  /**
   * ECR repository created by DataStack. BackendStack consumes the existing
   * repository - operators must push at least one image to it before this
   * stack is deployed (see SFBL-276 deploy ordering).
   */
  backendRepository: ecr.IRepository;
  // ISecret, not Secret: on a SFBL-297 snapshot restore DataStack imports these
  // by name (Secret.fromSecretNameV2), which yields ISecret. grantRead and
  // ecs.Secret.fromSecretsManager both accept ISecret.
  encryptionKeySecret: secretsmanager.ISecret;
  jwtSecretKeySecret: secretsmanager.ISecret;
  databaseUrlSecret: secretsmanager.ISecret;
  adminEmailSecret: secretsmanager.ISecret;
  adminPasswordSecret: secretsmanager.ISecret;
  /** SES domain identity ARN - IAM ses:SendEmail/SendRawEmail are scoped to this resource. */
  sesIdentityArn: string;
  /** DNS hostname that CloudFront uses as the backend origin (for example api.example.com). */
  backendDomainName: string;
  /** ACM certificate ARN for the ALB HTTPS listener. */
  backendCertificateArn: string;
  /** Route53 hosted zone name that owns backendDomainName. */
  hostedZoneDomain: string;
  /** Bronze/Silver/Gold tier preset - drives ECS task shape, replica count, log retention. */
  tier: TierConfig;
  ecrImageTag: string;
}

/**
 * BackendStack - ECS/Fargate backend service for the aws_hosted distribution.
 *
 * Provisions:
 *   - ECS cluster (Fargate; no EC2 capacity to manage)
 *   - Fargate task definition with:
 *       - Secrets Manager injection for all sensitive env vars
 *       - SSM Parameter Store injection for non-sensitive config
 *       - IAM task role with S3 read/write permissions
 *   - Application Load Balancer (HTTPS on 443 → HTTP on 8000 internally)
 *   - Route53 alias record for the backend origin hostname used by CloudFront
 *   - CloudWatch Logs log group
 *
 * The ECR repository for the backend image is created by DataStack (see
 * SFBL-276 - first-deploy ordering fix). Operators must push an image to
 * the repository before deploying this stack, otherwise the ECS service
 * fails its first task launch with an image-not-found error.
 *
 * Runtime config injection model:
 *
 *   Secrets Manager (sensitive) → ECS task secrets:
 *     /{env}/bulk-loader/encryption-key  → ENCRYPTION_KEY
 *     /{env}/bulk-loader/jwt-secret-key  → JWT_SECRET_KEY
 *     /{env}/bulk-loader/database-url    → DATABASE_URL
 *     /{env}/bulk-loader/admin-email     → ADMIN_EMAIL    (first boot only)
 *     /{env}/bulk-loader/admin-password  → ADMIN_PASSWORD (first boot only)
 *
 *   SSM Parameter Store (non-sensitive) → ECS task secrets (resolved at task launch):
 *     /{env}/bulk-loader/cors-origins         → CORS_ORIGINS
 *     /{env}/bulk-loader/log-level            → LOG_LEVEL
 *     /{env}/bulk-loader/admin-username       → ADMIN_USERNAME
 *     /{env}/bulk-loader/email-from-address   → EMAIL_FROM_ADDRESS  (SES sender)
 *     /{env}/bulk-loader/email-ses-region     → EMAIL_SES_REGION    (optional)
 *
 *   These are passed via the container `secrets:` block using
 *   `ecs.Secret.fromSsmParameter(...)` rather than `parameter.stringValue`
 *   under `environment:`. The latter resolves at synth time and bakes the
 *   literal value into the CloudFormation template - meaning a parameter
 *   change does not propagate to a new task without `cdk deploy`. Using
 *   `ecs.Secret.fromSsmParameter` means ECS reads the live value when the
 *   task starts, so a parameter edit + service rolling restart picks it up.
 *
 *   Hardcoded in task definition (distribution policy, not secrets):
 *     APP_DISTRIBUTION=aws_hosted
 *
 * The application reads all of these from environment variables - no code changes
 * are needed in config.py. The aws_hosted profile validates at startup that
 * transport_mode=https, input_storage_mode=s3, and DATABASE_URL is PostgreSQL.
 *
 * TLS is terminated at the ALB. CloudFront connects to the ALB using the backend origin
 * hostname so the TLS certificate matches the origin request hostname. The Fargate
 * container listens on plain HTTP (port 8000) internally. The backend logs a reminder of
 * this at startup when transport_mode=https. WebSocket connections use wss:// at the
 * client layer; the ALB forwards ws:// to the container transparently.
 */
export class BackendStack extends cdk.Stack {
  /** DNS name of the Application Load Balancer - consumed by FrontendStack for API routing. */
  public readonly albDnsName: string;

  constructor(scope: Construct, id: string, props: BackendStackProps) {
    super(scope, id, props);

    const env = props.envName;
    const repository = props.backendRepository;

    // --- CloudWatch Logs ---
    const logGroup = new logs.LogGroup(this, 'BackendLogGroup', {
      logGroupName: `/bulk-loader/${env}/backend`,
      retention: logRetentionFromDays(props.tier.logRetentionDays),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- ECS Cluster ---
    const cluster = new ecs.Cluster(this, 'Cluster', {
      vpc: props.vpc,
      clusterName: `bulk-loader-${env}`,
      containerInsightsV2: props.tier.containerInsightsEnabled
        ? ecs.ContainerInsights.ENABLED
        : ecs.ContainerInsights.DISABLED,
    });

    // SFBL-300: associate the Fargate capacity providers explicitly instead of
    // via the `enableFargateCapacityProviders: true` shortcut. The shortcut
    // references the cluster by its literal name, so CloudFormation sees no
    // dependency between the cluster and the AWS::ECS::ClusterCapacityProviderAssociations
    // resource and tears them down in parallel - the cluster delete then races
    // the association detach and fails with "The specified capacity provider is
    // in use and cannot be removed." Building the association explicitly and
    // adding a dependency on the cluster forces CloudFormation to delete (detach)
    // the association before the cluster, so `cdk destroy` succeeds unattended.
    const capacityProviderAssociations = new ecs.CfnClusterCapacityProviderAssociations(
      this,
      'ClusterCapacityProviders',
      {
        cluster: cluster.clusterName,
        capacityProviders: ['FARGATE', 'FARGATE_SPOT'],
        // No cluster-level default strategy - the service below sets its own
        // capacityProviderStrategies, matching the prior shortcut behaviour.
        defaultCapacityProviderStrategy: [],
      },
    );
    capacityProviderAssociations.node.addDependency(cluster);

    // --- IAM Task Role ---
    // Container-side identity for the running task. The application reads S3
    // input/output via per-Connection access keys stored encrypted in the DB
    // (see app/services/output_storage.py and app/api/input_connections.py),
    // so the task role does NOT need direct S3 grants on the input/output
    // buckets. Operators must create an IAM user, generate access keys, and
    // paste them into the InputConnection form via the UI - see
    // docs/deployment/aws.md "S3 input/output connections" section.
    //
    // SFBL-295 will add an `InputConnection.use_task_role` mode that lets
    // first-party buckets resolve via this role's default credential chain;
    // at that point S3 grants will be added back here.
    //
    // Secrets Manager and SSM access is granted via the task execution role
    // below (managed by ECS).
    const taskRole = new iam.Role(this, 'TaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: `Bulk Loader ECS task role (${env})`,
    });

    // --- Fargate Task Definition ---
    const taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDef', {
      memoryLimitMiB: props.tier.ecsTaskMemory,
      cpu: props.tier.ecsTaskCpu,
      taskRole,
      // The execution role is auto-created by CDK with permissions to pull from ECR
      // and write to CloudWatch Logs. CDK also grants it access to the secrets below.
    });

    // SSM Parameter Store - non-sensitive config resolved at task launch.
    // Provision these parameters before first ECS deployment:
    //   aws ssm put-parameter --name /{env}/bulk-loader/cors-origins --value '["https://your-domain.example"]' --type String
    //   aws ssm put-parameter --name /{env}/bulk-loader/log-level     --value 'INFO'                          --type String
    //   aws ssm put-parameter --name /{env}/bulk-loader/admin-username --value 'admin'                        --type String
    //
    // Imported via fromStringParameterName so CDK references the parameter by
    // ARN at deploy time. ECS resolves the live value when the task starts
    // (see ecs.Secret.fromSsmParameter usage below).
    const corsOriginsParam = ssm.StringParameter.fromStringParameterName(
      this, 'CorsOriginsParam', `/${env}/bulk-loader/cors-origins`
    );
    const logLevelParam = ssm.StringParameter.fromStringParameterName(
      this, 'LogLevelParam', `/${env}/bulk-loader/log-level`
    );
    const adminUsernameParam = ssm.StringParameter.fromStringParameterName(
      this, 'AdminUsernameParam', `/${env}/bulk-loader/admin-username`
    );
    const emailFromAddressParam = ssm.StringParameter.fromStringParameterName(
      this, 'EmailFromAddressParam', `/${env}/bulk-loader/email-from-address`
    );
    const emailSesRegionParam = ssm.StringParameter.fromStringParameterName(
      this, 'EmailSesRegionParam', `/${env}/bulk-loader/email-ses-region`
    );

    const container = taskDefinition.addContainer('backend', {
      image: ecs.ContainerImage.fromEcrRepository(repository, props.ecrImageTag),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'backend',
        logGroup,
      }),

      // All injected env vars go via the secrets: mapping so values resolve at
      // task launch, not at synth time. Sensitive values come from Secrets
      // Manager; non-sensitive values come from SSM Parameter Store. Both
      // mechanisms fetch the live value before the container starts, so a
      // parameter edit + rolling restart picks up the new value without
      // a CDK redeploy.
      secrets: {
        // Sensitive values - never appear in task definition plaintext.
        ENCRYPTION_KEY: ecs.Secret.fromSecretsManager(props.encryptionKeySecret),
        JWT_SECRET_KEY: ecs.Secret.fromSecretsManager(props.jwtSecretKeySecret),
        DATABASE_URL: ecs.Secret.fromSecretsManager(props.databaseUrlSecret),
        ADMIN_EMAIL: ecs.Secret.fromSecretsManager(props.adminEmailSecret),
        ADMIN_PASSWORD: ecs.Secret.fromSecretsManager(props.adminPasswordSecret),
        // Non-sensitive runtime config - read live from SSM at task launch.
        CORS_ORIGINS: ecs.Secret.fromSsmParameter(corsOriginsParam),
        LOG_LEVEL: ecs.Secret.fromSsmParameter(logLevelParam),
        ADMIN_USERNAME: ecs.Secret.fromSsmParameter(adminUsernameParam),
        EMAIL_FROM_ADDRESS: ecs.Secret.fromSsmParameter(emailFromAddressParam),
        EMAIL_SES_REGION: ecs.Secret.fromSsmParameter(emailSesRegionParam),
      },

      // Plain environment variables - only for static distribution policy
      // and the migration gate. Anything runtime-tunable lives in
      // SSM/Secrets Manager via secrets: above.
      environment: {
        // Distribution profile - drives all aws_hosted startup validation in config.py.
        APP_DISTRIBUTION: 'aws_hosted',
        // SFBL-277: service tasks never run migrations - the one-shot
        // MigrationTaskDefinition below handles that out-of-band before
        // each rolling deploy. See docs/deployment/aws.md "Ongoing
        // Deployments" for the deploy sequence.
        RUN_MIGRATIONS: 'false',
      },

      portMappings: [{ containerPort: 8000 }],

      // Container health check uses /api/health/live: process-only check that
      // returns 200 whenever the uvicorn server is running. Used by ECS to
      // decide whether to restart the container. We deliberately do NOT use
      // /api/health/ready here, because a transient DB blip would cause ECS
      // to kill an otherwise-healthy task that just needs the DB to recover.
      // See app/api/utility.py for the endpoint definitions.
      healthCheck: {
        command: ['CMD-SHELL', 'curl -f http://localhost:8000/api/health/live || exit 1'],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(60),
      },
    });

    // Grant the task execution role read access to secrets so ECS can inject them.
    props.encryptionKeySecret.grantRead(taskDefinition.executionRole!);
    props.jwtSecretKeySecret.grantRead(taskDefinition.executionRole!);
    props.databaseUrlSecret.grantRead(taskDefinition.executionRole!);
    props.adminEmailSecret.grantRead(taskDefinition.executionRole!);
    props.adminPasswordSecret.grantRead(taskDefinition.executionRole!);
    corsOriginsParam.grantRead(taskDefinition.executionRole!);
    logLevelParam.grantRead(taskDefinition.executionRole!);
    adminUsernameParam.grantRead(taskDefinition.executionRole!);
    emailFromAddressParam.grantRead(taskDefinition.executionRole!);
    emailSesRegionParam.grantRead(taskDefinition.executionRole!);

    // --- IAM: SES permissions for the application's EmailService ---
    // The SES backend (app/services/email/backends/ses.py) uses the boto3
    // default credential chain (i.e. the ECS task role) and calls SES v2
    // SendEmail/SendRawEmail at runtime, plus SES v1 GetSendQuota for the
    // health probe at /api/health/dependencies (cached 60s).
    //
    // Two policies, scoped differently:
    //   1. Send actions - restricted to the SES identity ARN provisioned
    //      by DataStack. Limits the blast radius if the role is ever
    //      compromised: it can only send as the deployment's own identity.
    //   2. Read actions - Resource: "*" because GetSendQuota / GetAccount
    //      are account-wide reads that don't accept a resource ARN.
    taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: 'SesSendScopedToIdentity',
        actions: ['ses:SendEmail', 'ses:SendRawEmail'],
        resources: [props.sesIdentityArn],
      }),
    );
    taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: 'SesAccountReadForHealthProbe',
        actions: ['ses:GetSendQuota', 'ses:GetAccount'],
        resources: ['*'],
      }),
    );

    // Suppress unused variable warning - container is used implicitly through taskDefinition.
    void container;

    // --- Application Load Balancer ---
    // TLS is terminated here. The backend container receives plain HTTP on port 8000.
    const alb = new elbv2.ApplicationLoadBalancer(this, 'Alb', {
      vpc: props.vpc,
      internetFacing: true,
      securityGroup: props.albSecurityGroup,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
    });

    const httpsListener = alb.addListener('HttpsListener', {
      port: 443,
      certificates: [
        elbv2.ListenerCertificate.fromArn(props.backendCertificateArn),
      ],
      sslPolicy: elbv2.SslPolicy.RECOMMENDED_TLS,
    });

    // HTTP → HTTPS redirect
    alb.addListener('HttpListener', {
      port: 80,
      defaultAction: elbv2.ListenerAction.redirect({
        protocol: 'HTTPS',
        port: '443',
        permanent: true,
      }),
    });

    // --- ECS Fargate Service ---
    const service = new ecs.FargateService(this, 'Service', {
      cluster,
      taskDefinition,
      desiredCount: props.tier.ecsDesiredCount,
      securityGroups: [props.backendServiceSecurityGroup],
      // SFBL-355: deployment circuit breaker with rollback. Without it, a bad
      // image rollout (e.g. a task that crashloops on boot) leaves the service
      // stuck IN_PROGRESS until the deployment times out, with no automatic
      // recovery. With it, ECS detects the failed rollout and rolls back to the
      // last known-good task set automatically.
      circuitBreaker: { rollback: true },
      // Tasks run in public subnets and are assigned public IPs so they can reach
      // the Salesforce API without a NAT Gateway. Inbound traffic is restricted by
      // the security group to the ALB only - no direct public access to port 8000.
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      assignPublicIp: true,
      // Rolling deploy: always keep at least one healthy task during updates.
      minHealthyPercent: 50,
      maxHealthyPercent: 200,
      // Capacity provider strategy:
      // - Bronze/Silver: 100% on-demand FARGATE.
      // - Gold (tier.useFargateSpot=true): hybrid - keeps 1 task on-demand
      //   so the service stays up if Spot is reclaimed, runs additional tasks
      //   on FARGATE_SPOT for cost savings. Per SFBL-295 the worker tier is
      //   the primary Spot consumer; the API tier hybrid is a smaller win.
      capacityProviderStrategies: props.tier.useFargateSpot
        ? [
            { capacityProvider: 'FARGATE', weight: 1, base: 1 },
            { capacityProvider: 'FARGATE_SPOT', weight: 1 },
          ]
        : [
            { capacityProvider: 'FARGATE', weight: 1 },
          ],
    });

    // The service's capacityProviderStrategies require the providers to be
    // associated with the cluster first. With the explicit association
    // construct above (SFBL-300) that ordering is no longer implied by the
    // cluster construct, so declare the dependency directly.
    service.node.addDependency(capacityProviderAssociations);

    // Register ECS service with ALB target group.
    // /api/* and /ws/* are routed to the backend; / is handled by CloudFront → S3.
    //
    // ALB target health uses /api/health/ready, which returns 503 when the DB
    // is unreachable. This pulls a degraded task out of rotation so traffic
    // does not hit it; ECS keeps the task alive (the container's own
    // /api/health/live check still passes) so the task can rejoin once the
    // DB recovers. The legacy /api/health endpoint returns 200 even when DB
    // connectivity is broken, which is why we do not use it here.
    httpsListener.addTargets('BackendTarget', {
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [service],
      healthCheck: {
        path: '/api/health/ready',
        interval: cdk.Duration.seconds(30),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
      },
      // Sticky sessions are not required because the backend is stateless (JWT auth).
    });

    new route53.CfnRecordSet(this, 'BackendAliasRecord', {
      hostedZoneName: `${props.hostedZoneDomain}.`,
      name: `${props.backendDomainName}.`,
      type: 'A',
      aliasTarget: {
        dnsName: alb.loadBalancerDnsName,
        hostedZoneId: alb.loadBalancerCanonicalHostedZoneId,
        evaluateTargetHealth: true,
      },
    });

    this.albDnsName = alb.loadBalancerDnsName;

    // --- Outputs ---
    new cdk.CfnOutput(this, 'AlbDnsName', {
      value: alb.loadBalancerDnsName,
      description: 'ALB DNS name (used as CloudFront backend origin for /api/* and /ws/*)',
      exportName: `${this.stackName}-AlbDnsName`,
    });
    new cdk.CfnOutput(this, 'BackendOriginDomainName', {
      value: props.backendDomainName,
      description: 'DNS name that CloudFront uses as the HTTPS backend origin',
    });
    new cdk.CfnOutput(this, 'EcsClusterName', {
      value: cluster.clusterName,
      description: 'ECS cluster name',
    });

    // SFBL-298: the one-shot Alembic migration task definition (and its
    // MigrationTaskDefinitionArn / BackendServiceSecurityGroupId outputs) now
    // live in DataStack, so they exist after `cdk deploy Network Data` - before
    // this stack is synthesised. That closes the first-deploy chicken-and-egg
    // where the service booted against an empty schema. See data-stack.ts and
    // DECISIONS 027.
  }
}
