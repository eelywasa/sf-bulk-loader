import * as fs from 'fs';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';

export interface FrontendStackProps extends cdk.StackProps {
  envName: string;
  domainName: string;
  /** ACM certificate ARN — must be in us-east-1 for CloudFront. */
  certificateArn: string;
  /** Backend origin hostname covered by the ALB certificate (for example api.example.com). */
  backendOriginDomainName: string;
}

/**
 * FrontendStack — CloudFront + S3 static hosting for the aws_hosted distribution.
 *
 * Architecture:
 *   Browser → CloudFront → /api/*  → backend origin hostname → ALB → ECS/Fargate
 *                        → /ws/*   → backend origin hostname → ALB → ECS/Fargate
 *                        → /*      → S3                     → React SPA
 *
 * TLS is terminated at CloudFront (wss:// at client, ws:// at ALB internally).
 * The certificate must be provisioned in us-east-1 regardless of the deployment region.
 *
 * Frontend build deployment:
 *   The Vite build at ../frontend/dist is uploaded to S3 and a CloudFront
 *   invalidation is issued automatically by the BucketDeployment construct
 *   below. Operators must run `cd frontend && npm run build` before
 *   `cdk deploy BulkLoader-{env}-Frontend`. If ../frontend/dist is missing
 *   at synth time the BucketDeployment is skipped with a warning — useful
 *   for `cdk synth` smoke checks during development without a build.
 */
export class FrontendStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    // --- S3 Bucket — static frontend assets ---
    const frontendBucket = new s3.Bucket(this, 'FrontendBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      // Bucket is not public — CloudFront accesses it via Origin Access Control (OAC).
    });

    // --- CloudFront Origin Access Control ---
    // OAC replaces the legacy Origin Access Identity (OAI) pattern.
    // It restricts direct S3 bucket access to CloudFront only.
    const oac = new cloudfront.S3OriginAccessControl(this, 'OAC', {
      description: `Bulk Loader frontend OAC (${props.envName})`,
    });

    // --- ALB origin for API and WebSocket paths ---
    // The ALB handles /api/* and /ws/* paths.
    // CloudFront does not terminate WebSocket connections — it proxies them through.
    const albOrigin = new origins.HttpOrigin(props.backendOriginDomainName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
      // CloudFront connects using a hostname covered by the ALB certificate.
    });

    // --- CloudFront Distribution ---
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: `Salesforce Bulk Loader — ${props.envName}`,
      defaultRootObject: 'index.html',

      // Default behavior: serve React SPA from S3.
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(frontendBucket, { originAccessControl: oac }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        // SPA routing: 403/404 from S3 is rewritten to /index.html so React Router handles it.
      },

      // /api/* → ALB → Fargate (not cached; forwarded directly)
      additionalBehaviors: {
        '/api/*': {
          origin: albOrigin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
        // /ws/* → ALB → Fargate (WebSocket — not cached, all methods, long TTL disabled)
        '/ws/*': {
          origin: albOrigin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },

      // Custom error responses redirect SPA deep links back to index.html.
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
      ],

      domainNames: [props.domainName],
      certificate: acm.Certificate.fromCertificateArn(this, 'Cert', props.certificateArn),
    });

    // --- Frontend Deployment ---
    // Uploads ../frontend/dist into the frontend bucket and invalidates the
    // CloudFront distribution. Operators must run `cd frontend && npm run build`
    // before `cdk deploy BulkLoader-{env}-Frontend` so the dist is present at
    // synth time. Without it, the construct cannot package the asset.
    //
    // The dist path is resolved relative to the cdk app cwd (infrastructure/).
    // We skip the construct when dist/ is missing so `cdk synth` still works
    // for type-checking and template-shape validation in dev workflows that
    // don't include a frontend build.
    const distPath = path.resolve(__dirname, '..', '..', 'frontend', 'dist');
    if (fs.existsSync(distPath) && fs.existsSync(path.join(distPath, 'index.html'))) {
      new s3deploy.BucketDeployment(this, 'DeployFrontend', {
        sources: [s3deploy.Source.asset(distPath)],
        destinationBucket: frontendBucket,
        distribution,
        distributionPaths: ['/*'],
        prune: true,  // remove objects from S3 that are not in dist/
      });
    } else {
      // Surface the missing-build state at synth time so operators notice
      // before running `cdk deploy`. Annotation appears in `cdk synth` output
      // and on the CloudFormation events stream.
      cdk.Annotations.of(this).addWarning(
        `Frontend build not found at ${distPath}. ` +
        `Run \`cd frontend && npm run build\` before \`cdk deploy\` so the ` +
        `BucketDeployment construct can package the SPA. \`cdk synth\` will ` +
        `succeed but the deployed frontend bucket will be empty until a build is uploaded.`
      );
    }

    // --- Outputs ---
    new cdk.CfnOutput(this, 'DistributionDomainName', {
      value: distribution.distributionDomainName,
      description: 'CloudFront distribution domain (use this or configure your custom domain)',
    });
    new cdk.CfnOutput(this, 'DistributionId', {
      value: distribution.distributionId,
      description: 'CloudFront distribution ID — needed for cache invalidation on deploy',
    });
    new cdk.CfnOutput(this, 'FrontendBucketName', {
      value: frontendBucket.bucketName,
      description: 'S3 bucket for frontend static assets',
    });
  }
}
