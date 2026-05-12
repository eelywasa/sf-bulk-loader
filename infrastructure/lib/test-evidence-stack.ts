import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export interface TestEvidenceStackProps extends cdk.StackProps {
  /**
   * GitHub repo whose Collaborators list is the single source of truth for
   * dashboard access. The Lambda@Edge function checks the OAuth'd user
   * against this repo via `GET /user/repos?affiliation=collaborator`.
   * Example: "eelywasa/sf-bulk-loader".
   */
  authorizedRepo: string;

  /**
   * GitHub repo identifier for the GHA OIDC publishing role trust policy.
   * Almost always the same as `authorizedRepo`; kept separate so the
   * conceptual distinction is visible in the stack interface.
   */
  ghaRepoIdentifier: string;

  /**
   * Custom domain for the reports site (e.g. "reports.bulk-loader.example.com").
   * Optional — if omitted the stack uses the default `*.cloudfront.net` URL.
   */
  domainName?: string;

  /**
   * ACM certificate ARN for `domainName`. **Must be in us-east-1.** Required
   * if `domainName` is set; ignored otherwise.
   */
  certificateArn?: string;

  /**
   * Route53 hosted zone that owns `domainName` (e.g. "example.com"). Reserved
   * for future use — Route53 alias records are not created in this scaffold.
   */
  hostedZoneDomain?: string;

  /**
   * If true, create a new GitHub Actions OIDC provider in the account.
   * Set false if a provider already exists (only one per account is allowed
   * for `token.actions.githubusercontent.com`); supply `existingOidcProviderArn`.
   */
  createOidcProvider: boolean;

  /**
   * ARN of an existing GitHub Actions OIDC provider. Used when
   * `createOidcProvider` is false.
   */
  existingOidcProviderArn?: string;
}

/**
 * TestEvidenceStack - SFBL-334 / SFBL-341.
 *
 * Private S3 bucket fronted by CloudFront with Origin Access Control.
 * A Lambda@Edge function on viewer-request gates access via GitHub OAuth +
 * a collaborator check against `authorizedRepo`. CI publishes via OIDC into
 * a per-PR / per-run prefix inside the bucket.
 *
 * URL layout served by the bucket:
 *   main/                    - latest report from main
 *   pr-{n}/                  - per-PR report (lifecycle: 30d)
 *   tier-2/{run-id}/         - per Tier-2 run (lifecycle: 90d)
 *
 * **Region constraint:** this stack MUST deploy to us-east-1 because
 * CloudFront edge functions only support that region as origin. Hard-coded
 * in `bin/test-evidence-app.ts`.
 *
 * **Scope of this initial scaffold (commit 1):**
 *   - S3 bucket with lifecycle rules
 *   - CloudFront distribution with OAC
 *   - Secrets Manager shell (no values; SFBL-350 J seeds at deploy time)
 *   - Lambda@Edge role + GHA OIDC publishing role (no Lambda function yet)
 *
 * **Out of scope here (lands in next commit):**
 *   - The actual Lambda@Edge OAuth handler code
 *   - Wiring the function as a viewer-request behaviour on the distribution
 *   - Route53 alias record for the custom domain
 *
 * Until the Lambda@Edge handler lands, the distribution is reachable but NOT
 * yet OAuth-gated. The deploy step in SFBL-341's DoD must wait for the
 * second commit before the smoke test cases can pass.
 */
export class TestEvidenceStack extends cdk.Stack {
  /** The evidence bucket — CI publishes into per-PR / per-run prefixes. */
  public readonly bucket: s3.Bucket;

  /** CloudFront distribution serving the OAuth-gated reports site. */
  public readonly distribution: cloudfront.Distribution;

  /** Secrets Manager secret shell — seeded by SFBL-350 J after deploy. */
  public readonly oauthSecret: secretsmanager.Secret;

  /** IAM role assumed by GHA via OIDC to publish reports into the bucket. */
  public readonly publisherRole: iam.Role;

  constructor(scope: Construct, id: string, props: TestEvidenceStackProps) {
    super(scope, id, props);

    if (props.env?.region !== 'us-east-1') {
      throw new Error(
        `TestEvidenceStack must be deployed to us-east-1 (got: ${props.env?.region}). ` +
        'CloudFront edge functions only support us-east-1 as the origin region.'
      );
    }

    if (props.domainName && !props.certificateArn) {
      throw new Error(
        'certificateArn is required when domainName is set. ' +
        'The ACM certificate must be issued in us-east-1.'
      );
    }

    // --- S3 Bucket — evidence storage ---
    // Versioning ON so an accidental overwrite of a report can be rolled back.
    // Lifecycle: pr-*/ trimmed at 30d, tier-2/*/ at 90d, main/ retained.
    // Public access fully blocked; CloudFront accesses via OAC.
    this.bucket = new s3.Bucket(this, 'EvidenceBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      // The bucket survives a stack delete — these are operational artefacts,
      // not ephemeral test outputs. To wipe, empty the bucket explicitly then
      // re-deploy with removalPolicy flipped.
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          id: 'trim-pr-reports',
          enabled: true,
          prefix: 'pr-',
          expiration: cdk.Duration.days(30),
          // Old non-current versions clear out quickly so we don't accumulate
          // per-push deltas indefinitely.
          noncurrentVersionExpiration: cdk.Duration.days(7),
          abortIncompleteMultipartUploadAfter: cdk.Duration.days(1),
        },
        {
          id: 'trim-tier-2-reports',
          enabled: true,
          prefix: 'tier-2/',
          expiration: cdk.Duration.days(90),
          noncurrentVersionExpiration: cdk.Duration.days(30),
          abortIncompleteMultipartUploadAfter: cdk.Duration.days(1),
        },
        {
          // main/ has no expiration — it's the canonical latest-from-main
          // report. We do keep noncurrent versions bounded so the bucket
          // doesn't grow unboundedly across pushes.
          id: 'limit-main-version-history',
          enabled: true,
          prefix: 'main/',
          noncurrentVersionExpiration: cdk.Duration.days(90),
          abortIncompleteMultipartUploadAfter: cdk.Duration.days(1),
        },
      ],
    });

    // --- CloudFront Origin Access Control ---
    // OAC restricts direct S3 access to this distribution only.
    const oac = new cloudfront.S3OriginAccessControl(this, 'OAC', {
      description: 'Test evidence OAC — Lambda@Edge gates user access via GitHub OAuth',
    });

    // --- CloudFront Distribution ---
    // The Lambda@Edge viewer-request handler wired in the next commit will
    // intercept every request for OAuth + collaborator check. Until then,
    // the distribution forwards to the private bucket but has no access gate.
    // (The bucket is still blocked from public access at the S3 layer; an
    // un-gated CloudFront would just return signed-OAC responses for any URL.)
    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'Salesforce Bulk Loader — test evidence dashboard',

      // Root URL (https://reports.example.com/) maps to main/index.html.
      // Subdir paths (https://reports.example.com/pr-123/) get the
      // index.html fallback via the errorResponses rewrites below — this is
      // the standard "directory-default-document" pattern for S3 + CloudFront
      // without a CloudFront Function rewrite.
      defaultRootObject: 'index.html',

      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.bucket, {
          originAccessControl: oac,
          // Default S3 origin path is empty; reports live at /main/, /pr-{n}/,
          // /tier-2/{run-id}/ inside the bucket and the URL path maps 1:1.
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        // TODO (next commit): wire Lambda@Edge OAuth handler here as
        //   edgeLambdas: [{ functionVersion: ..., eventType: VIEWER_REQUEST }]
      },

      // The Allure-generated reports are deep-linkable SPAs; we want
      // /pr-123/foo.html to resolve via S3 directly, but /pr-123/ (no
      // trailing object) needs the index.html default. The errorResponses
      // below handle 403 (S3's "key not found" for a directory listing) by
      // rewriting to /pr-123/index.html — though for true subdir defaults
      // a CloudFront Function `URI rewrite` is the cleaner approach.
      //
      // TODO (next commit): replace these rewrites with a CloudFront
      // Function that appends `index.html` to any URI ending in `/`. The
      // errorResponses approach interacts badly with real 404s from the
      // bucket.
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
      ],

      ...(props.domainName && props.certificateArn
        ? {
            domainNames: [props.domainName],
            certificate: acm.Certificate.fromCertificateArn(this, 'Cert', props.certificateArn),
          }
        : {}),
    });

    // --- Secrets Manager — OAuth shell ---
    // SFBL-350 J seeds the actual clientId / clientSecret / sessionSigningKey
    // values after this stack is deployed. We create the shell here so the
    // Lambda@Edge IAM role can be granted read access to a known ARN.
    this.oauthSecret = new secretsmanager.Secret(this, 'OAuthSecret', {
      secretName: 'sfbl/test-evidence/oauth',
      description: 'GitHub OAuth client + session signing key for the test-evidence Lambda@Edge',
      // Don't generate a default — SFBL-350 J populates this manually.
      // Generated random would just need to be overwritten.
    });

    // --- Lambda@Edge execution role (TODO: actual function in next commit) ---
    // The Lambda needs Secrets Manager read for the OAuth shell. Lambda@Edge
    // runs in the AWS-managed `edgelambda.amazonaws.com` service alongside
    // the usual lambda.amazonaws.com trust — both are required.
    const lambdaEdgeRole = new iam.Role(this, 'LambdaEdgeRole', {
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('lambda.amazonaws.com'),
        new iam.ServicePrincipal('edgelambda.amazonaws.com'),
      ),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
      description: 'Test evidence Lambda@Edge — runs OAuth + collaborator check',
    });
    this.oauthSecret.grantRead(lambdaEdgeRole);

    // --- GitHub Actions OIDC provider ---
    // One provider per account for `token.actions.githubusercontent.com`.
    // If another stack/account-bootstrap has already created it, set
    // `createOidcProvider: false` and pass `existingOidcProviderArn`.
    let oidcProvider: iam.IOpenIdConnectProvider;
    if (props.createOidcProvider) {
      oidcProvider = new iam.OpenIdConnectProvider(this, 'GitHubOidcProvider', {
        url: 'https://token.actions.githubusercontent.com',
        clientIds: ['sts.amazonaws.com'],
        // GitHub's OIDC thumbprints are autorotated; the AWS-recommended
        // pattern is to let the construct fetch them at deploy time.
      });
    } else {
      if (!props.existingOidcProviderArn) {
        throw new Error(
          'existingOidcProviderArn is required when createOidcProvider is false.'
        );
      }
      oidcProvider = iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn(
        this,
        'GitHubOidcProvider',
        props.existingOidcProviderArn,
      );
    }

    // --- IAM role for GHA publishing ---
    // Trust policy scoped to:
    //   - repo:eelywasa/sf-bulk-loader (the publishing repo)
    //   - ref:refs/heads/main OR pull_request workflow runs
    //   - workflow path constraint added in SFBL-347 G (further tightens to
    //     the specific publish workflow file). Trust the broad subject here
    //     and let SFBL-347 layer on the workflow-level constraint via a
    //     conditions diff once that story lands.
    this.publisherRole = new iam.Role(this, 'PublisherRole', {
      roleName: 'sfbl-test-evidence-publisher',
      assumedBy: new iam.WebIdentityPrincipal(oidcProvider.openIdConnectProviderArn, {
        StringEquals: {
          'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        },
        StringLike: {
          'token.actions.githubusercontent.com:sub': [
            `repo:${props.ghaRepoIdentifier}:ref:refs/heads/main`,
            `repo:${props.ghaRepoIdentifier}:pull_request`,
          ],
        },
      }),
      description: 'Assumed by GitHub Actions to publish test evidence into the bucket',
      maxSessionDuration: cdk.Duration.hours(1),
    });

    // Scope publishing permissions to the bucket only — no other AWS access.
    // CloudFront invalidations are required after each publish so the new
    // report shows up immediately.
    this.bucket.grantReadWrite(this.publisherRole);
    this.publisherRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['cloudfront:CreateInvalidation'],
        resources: [
          `arn:aws:cloudfront::${this.account}:distribution/${this.distribution.distributionId}`,
        ],
      }),
    );

    // --- Outputs ---
    new cdk.CfnOutput(this, 'EvidenceBucketName', {
      value: this.bucket.bucketName,
      description: 'S3 bucket for test evidence (set BUCKET env var in CI to this)',
    });
    new cdk.CfnOutput(this, 'DistributionDomainName', {
      value: this.distribution.distributionDomainName,
      description:
        'CloudFront default domain (e.g. d1234abcd.cloudfront.net). Use the custom domain if configured.',
    });
    new cdk.CfnOutput(this, 'DistributionId', {
      value: this.distribution.distributionId,
      description: 'CloudFront distribution ID — needed for cache invalidation',
    });
    new cdk.CfnOutput(this, 'PublisherRoleArn', {
      value: this.publisherRole.roleArn,
      description:
        'IAM role for GitHub Actions to assume via OIDC when publishing test evidence',
    });
    new cdk.CfnOutput(this, 'OAuthSecretArn', {
      value: this.oauthSecret.secretArn,
      description:
        'Secrets Manager ARN for the OAuth client + session signing key (SFBL-350 J seeds the values)',
    });
  }
}
