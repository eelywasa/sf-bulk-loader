import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';

export interface BackendStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.Vpc;
  albSecurityGroup: ec2.SecurityGroup;
  backendServiceSecurityGroup: ec2.SecurityGroup;
  inputBucket: s3.Bucket;
  outputBucket: s3.Bucket;
  encryptionKeySecret: secretsmanager.Secret;
  jwtSecretKeySecret: secretsmanager.Secret;
  databaseUrlSecret: secretsmanager.Secret;
  adminPasswordSecret: secretsmanager.Secret;
  /** DNS hostname that CloudFront uses as the backend origin (for example api.example.com). */
  backendDomainName: string;
  /** ACM certificate ARN for the ALB HTTPS listener. */
  backendCertificateArn: string;
  /** Route53 hosted zone name that owns backendDomainName. */
  hostedZoneDomain: string;
  ecsDesiredCount: number;
  ecrImageTag: string;
}

/**
 * BackendStack — ECS/Fargate backend service for the aws_hosted distribution.
 *
 * Provisions:
 *   - ECR repository for the backend Docker image
 *   - ECS cluster (Fargate; no EC2 capacity to manage)
 *   - Fargate task definition with:
 *       - Secrets Manager injection for all sensitive env vars
 *       - SSM Parameter Store injection for non-sensitive config
 *       - IAM task role with S3 read/write permissions
 *   - Application Load Balancer (HTTPS on 443 → HTTP on 8000 internally)
 *   - Route53 alias record for the backend origin hostname used by CloudFront
 *   - CloudWatch Logs log group
 *
 * Runtime config injection model:
 *
 *   Secrets Manager (sensitive) → ECS task secrets:
 *     /{env}/bulk-loader/encryption-key  → ENCRYPTION_KEY
 *     /{env}/bulk-loader/jwt-secret-key  → JWT_SECRET_KEY
 *     /{env}/bulk-loader/database-url    → DATABASE_URL
 *     /{env}/bulk-loader/admin-password  → ADMIN_PASSWORD
 *
 *   SSM Parameter Store (non-sensitive) → ECS task environment:
 *     /{env}/bulk-loader/cors-origins    → CORS_ORIGINS
 *     /{env}/bulk-loader/log-level       → LOG_LEVEL
 *     /{env}/bulk-loader/admin-username  → ADMIN_USERNAME
 *
 *   Hardcoded in task definition (distribution policy, not secrets):
 *     APP_DISTRIBUTION=aws_hosted
 *
 * The application reads all of these from environment variables — no code changes
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
  /** DNS name of the Application Load Balancer — consumed by FrontendStack for API routing. */
  public readonly albDnsName: string;

  constructor(scope: Construct, id: string, props: BackendStackProps) {
    super(scope, id, props);

    const env = props.envName;

    // --- ECR Repository ---
    // Build and push the backend image here before deploying ECS.
    // See aws.md for the full docker buildx → ECR push workflow.
    const repository = new ecr.Repository(this, 'BackendRepository', {
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

    // --- CloudWatch Logs ---
    const logGroup = new logs.LogGroup(this, 'BackendLogGroup', {
      logGroupName: `/bulk-loader/${env}/backend`,
      retention: env === 'production'
        ? logs.RetentionDays.ONE_MONTH
        : logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- ECS Cluster ---
    const cluster = new ecs.Cluster(this, 'Cluster', {
      vpc: props.vpc,
      clusterName: `bulk-loader-${env}`,
      enableFargateCapacityProviders: true,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    });

    // --- IAM Task Role ---
    // Grants the running container access to S3 buckets for input/output.
    // Secrets Manager and SSM access is granted via the task execution role (managed by ECS).
    const taskRole = new iam.Role(this, 'TaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: `Bulk Loader ECS task role (${env})`,
    });
    props.inputBucket.grantRead(taskRole);
    props.outputBucket.grantReadWrite(taskRole);

    // --- Fargate Task Definition ---
    const taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDef', {
      memoryLimitMiB: 1024,
      cpu: 512,
      taskRole,
      // The execution role is auto-created by CDK with permissions to pull from ECR
      // and write to CloudWatch Logs. CDK also grants it access to the secrets below.
    });

    // SSM Parameter Store — non-sensitive config resolved at task launch.
    // Provision these parameters before first ECS deployment:
    //   aws ssm put-parameter --name /{env}/bulk-loader/cors-origins --value '["https://your-domain.example"]' --type String
    //   aws ssm put-parameter --name /{env}/bulk-loader/log-level     --value 'INFO'                          --type String
    //   aws ssm put-parameter --name /{env}/bulk-loader/admin-username --value 'admin'                        --type String
    const corsOriginsParam = ssm.StringParameter.fromStringParameterName(
      this, 'CorsOriginsParam', `/${env}/bulk-loader/cors-origins`
    );
    const logLevelParam = ssm.StringParameter.fromStringParameterName(
      this, 'LogLevelParam', `/${env}/bulk-loader/log-level`
    );
    const adminUsernameParam = ssm.StringParameter.fromStringParameterName(
      this, 'AdminUsernameParam', `/${env}/bulk-loader/admin-username`
    );

    const container = taskDefinition.addContainer('backend', {
      image: ecs.ContainerImage.fromEcrRepository(repository, props.ecrImageTag),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'backend',
        logGroup,
      }),

      // Sensitive values from Secrets Manager — never appear in task definition plaintext.
      secrets: {
        ENCRYPTION_KEY: ecs.Secret.fromSecretsManager(props.encryptionKeySecret),
        JWT_SECRET_KEY: ecs.Secret.fromSecretsManager(props.jwtSecretKeySecret),
        DATABASE_URL: ecs.Secret.fromSecretsManager(props.databaseUrlSecret),
        ADMIN_PASSWORD: ecs.Secret.fromSecretsManager(props.adminPasswordSecret),
      },

      // Non-sensitive config injected as plain environment variables.
      // SSM Parameter Store values are resolved at task launch by ECS (not by the app).
      environment: {
        // Distribution profile — drives all aws_hosted startup validation in config.py.
        APP_DISTRIBUTION: 'aws_hosted',
        // SSM-sourced values resolved by ECS at task launch.
        // CDK references the parameter ARN; ECS fetches the value before container start.
        CORS_ORIGINS: corsOriginsParam.stringValue,
        LOG_LEVEL: logLevelParam.stringValue,
        ADMIN_USERNAME: adminUsernameParam.stringValue,
        // TODO: add SF_API_VERSION, DEFAULT_PARTITION_SIZE if environment-specific values
        //       are needed; otherwise the application defaults in config.py are used.
      },

      portMappings: [{ containerPort: 8000 }],

      // Container health check mirrors the /api/health endpoint.
      healthCheck: {
        command: ['CMD-SHELL', 'curl -f http://localhost:8000/api/health || exit 1'],
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
    props.adminPasswordSecret.grantRead(taskDefinition.executionRole!);
    corsOriginsParam.grantRead(taskDefinition.executionRole!);
    logLevelParam.grantRead(taskDefinition.executionRole!);
    adminUsernameParam.grantRead(taskDefinition.executionRole!);

    // Suppress unused variable warning — container is used implicitly through taskDefinition.
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
      desiredCount: props.ecsDesiredCount,
      securityGroups: [props.backendServiceSecurityGroup],
      // Tasks run in public subnets and are assigned public IPs so they can reach
      // the Salesforce API without a NAT Gateway. Inbound traffic is restricted by
      // the security group to the ALB only — no direct public access to port 8000.
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      assignPublicIp: true,
      // Rolling deploy: always keep at least one healthy task during updates.
      minHealthyPercent: 50,
      maxHealthyPercent: 200,
      capacityProviderStrategies: [
        { capacityProvider: 'FARGATE', weight: 1 },
        // TODO: add FARGATE_SPOT weight for non-production to reduce cost
      ],
    });

    // Register ECS service with ALB target group.
    // /api/* and /ws/* are routed to the backend; / is handled by CloudFront → S3.
    httpsListener.addTargets('BackendTarget', {
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [service],
      healthCheck: {
        path: '/api/health',
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
    new cdk.CfnOutput(this, 'EcrRepositoryUri', {
      value: repository.repositoryUri,
      description: 'ECR repository URI — push the backend image here before deploying',
    });
    new cdk.CfnOutput(this, 'EcsClusterName', {
      value: cluster.clusterName,
      description: 'ECS cluster name',
    });
  }
}
