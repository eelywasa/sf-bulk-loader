#!/usr/bin/env node
/**
 * SFBL-334 / SFBL-341 — TestEvidenceStack CDK app entry.
 *
 * This is a STANDALONE CDK app, separate from `bin/app.ts`. The test-evidence
 * infrastructure is a single global deployment that serves CI evidence for
 * every PR / main / Tier-2 run; it's not tied to the per-env runtime lifecycle
 * (staging / production) and must live in `us-east-1` because of the
 * Lambda@Edge requirement on CloudFront.
 *
 * Deploy via:
 *   cd infrastructure
 *   npm run synth:test-evidence          # smoke-check synth
 *   npm run deploy:test-evidence         # deploy the stack
 *
 * Configuration:
 *   - cdk.json carries the safe placeholder values under `context.testEvidence`
 *   - Real values (custom domain, ACM cert ARN, hosted zone) live in
 *     cdk.context.json under the same key, copied from cdk.context.json.example
 *
 * Pair-deployed with SFBL-350 (J) which seeds the OAuth App + Secrets Manager
 * values that this stack's Lambda@Edge reads at runtime.
 */
import * as cdk from 'aws-cdk-lib';
import { TestEvidenceStack } from '../lib/test-evidence-stack';

const app = new cdk.App();

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const testEvidence = app.node.tryGetContext('testEvidence') as Record<string, any> | undefined;
if (!testEvidence) {
  throw new Error(
    'CDK context "testEvidence" is required. ' +
    'Configure it in cdk.json (safe values) and cdk.context.json (real ARNs / domain). ' +
    'See cdk.context.json.example for the expected shape.'
  );
}

const authorizedRepo = testEvidence.authorizedRepo as string | undefined;
if (!authorizedRepo) {
  throw new Error(
    'testEvidence.authorizedRepo is required (e.g. "eelywasa/sf-bulk-loader"). ' +
    'This is the GitHub repo whose collaborators get dashboard access.'
  );
}

// us-east-1 is mandatory for Lambda@Edge — CloudFront edge functions only
// support that region as the origin. Hard-coded rather than pulled from
// context so a misconfigured cdk.context.json can't break the deployment.
const awsEnv: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: 'us-east-1',
};

new TestEvidenceStack(app, 'BulkLoader-TestEvidence', {
  env: awsEnv,
  authorizedRepo,
  ghaRepoIdentifier: (testEvidence.ghaRepoIdentifier as string | undefined) ?? authorizedRepo,
  domainName: testEvidence.domainName as string | undefined,
  certificateArn: testEvidence.certificateArn as string | undefined,
  hostedZoneDomain: testEvidence.hostedZoneDomain as string | undefined,
  createOidcProvider: (testEvidence.createOidcProvider as boolean | undefined) ?? true,
  existingOidcProviderArn: testEvidence.existingOidcProviderArn as string | undefined,
  description:
    'Salesforce Bulk Loader - test evidence host (S3 + CloudFront + Lambda@Edge GitHub OAuth)',
});

app.synth();
