#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { NetworkStack } from '../lib/network-stack';
import { DataStack } from '../lib/data-stack';
import { BackendStack } from '../lib/backend-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { resolveTier } from '../lib/tier-config';

const app = new cdk.App();

// Resolve environment from CDK context.
// Usage: cdk deploy --all -c env=staging
//        cdk deploy --all -c env=production
const envName = app.node.tryGetContext('env') as string | undefined;
if (!envName) {
  throw new Error(
    'CDK context "env" is required. Pass -c env=staging or -c env=production'
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const environments = app.node.tryGetContext('environments') as Record<string, any> | undefined;
const envConfig = environments?.[envName];
if (!envConfig) {
  throw new Error(
    `No environment config found for "${envName}" in cdk.json context.environments. ` +
    `Available: ${Object.keys(environments ?? {}).join(', ')}`
  );
}

// Resolve the Bronze/Silver/Gold tier preset for this environment.
// Tier shapes live in cdk.json context.tiers; each env names one via the
// `tier` field. Stacks read sizing/retention/feature-flag values from the
// resolved preset rather than from per-environment overrides.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const tiers = app.node.tryGetContext('tiers') as Record<string, any> | undefined;
const tier = resolveTier(envName, envConfig, tiers);

// AWS account comes from the caller's environment (CDK_DEFAULT_ACCOUNT,
// set automatically by `aws sts get-caller-identity`). Region comes from
// the env config (`cdk.json`/`cdk.context.json`) so it's pinned to the
// chosen deployment region regardless of whatever AWS_DEFAULT_REGION /
// CDK_DEFAULT_REGION the operator's shell happens to have. Falls back
// to CDK_DEFAULT_REGION if envConfig.region is unset (preserves the
// original behaviour for environments that haven't opted in).
const awsEnv: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: (envConfig.region as string | undefined) ?? process.env.CDK_DEFAULT_REGION,
};

const prefix = `BulkLoader-${envName}`;

// Stack 1: VPC and network topology
const networkStack = new NetworkStack(app, `${prefix}-Network`, {
  env: awsEnv,
  envName,
  vpcCidr: envConfig.vpcCidr as string,
  tier,
  description: `Salesforce Bulk Loader - network layer (${envName})`,
});

// Stack 2: RDS, S3, Secrets Manager, ECR, SES identity
const dataStack = new DataStack(app, `${prefix}-Data`, {
  env: awsEnv,
  envName,
  vpc: networkStack.vpc,
  backendServiceSecurityGroup: networkStack.backendServiceSecurityGroup,
  tier,
  // SFBL-297/SFBL-299: persistence is an environment-lifecycle decision,
  // decoupled from the tier's sizing. An environment may override the tier's
  // default (e.g. a small/bronze env that is nonetheless a real, persistent
  // one). Falls back to the tier preset, then to false.
  persistOnDestroy:
    (envConfig.persistOnDestroy as boolean | undefined) ??
    tier.persistOnDestroy ??
    false,
  ecrImageTag: (envConfig.ecrImageTag as string) ?? 'latest',
  hostedZoneDomain: envConfig.hostedZoneDomain as string,
  sesIdentityDomain: envConfig.sesIdentityDomain as string | undefined,
  sesIdentityAdoptExisting: envConfig.sesIdentityAdoptExisting as boolean | undefined,
  description: `Salesforce Bulk Loader - data layer (${envName})`,
});
dataStack.addDependency(networkStack);

// Stack 3: ECS/Fargate backend service + ALB
const backendStack = new BackendStack(app, `${prefix}-Backend`, {
  env: awsEnv,
  envName,
  vpc: networkStack.vpc,
  albSecurityGroup: networkStack.albSecurityGroup,
  backendServiceSecurityGroup: networkStack.backendServiceSecurityGroup,
  backendRepository: dataStack.backendRepository,
  inputBucket: dataStack.inputBucket,
  outputBucket: dataStack.outputBucket,
  encryptionKeySecret: dataStack.encryptionKeySecret,
  jwtSecretKeySecret: dataStack.jwtSecretKeySecret,
  databaseUrlSecret: dataStack.databaseUrlSecret,
  adminEmailSecret: dataStack.adminEmailSecret,
  adminPasswordSecret: dataStack.adminPasswordSecret,
  sesIdentityArn: dataStack.sesIdentityArn,
  backendDomainName: envConfig.backendDomainName as string,
  backendCertificateArn: envConfig.backendCertificateArn as string,
  hostedZoneDomain: envConfig.hostedZoneDomain as string,
  tier,
  ecrImageTag: (envConfig.ecrImageTag as string) ?? 'latest',
  description: `Salesforce Bulk Loader - backend service (${envName})`,
});
backendStack.addDependency(dataStack);

// Stack 4: CloudFront + S3 static frontend
new FrontendStack(app, `${prefix}-Frontend`, {
  env: awsEnv,
  envName,
  domainName: envConfig.domainName as string,
  certificateArn: envConfig.certificateArn as string,
  backendOriginDomainName: envConfig.backendDomainName as string,
  description: `Salesforce Bulk Loader - frontend hosting (${envName})`,
}).addDependency(backendStack);

app.synth();
