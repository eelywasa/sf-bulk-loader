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

// Use the AWS account/region from the caller's environment.
// Run `aws configure` or set AWS_PROFILE before deploying.
const awsEnv: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

const prefix = `BulkLoader-${envName}`;

// Stack 1: VPC and network topology
const networkStack = new NetworkStack(app, `${prefix}-Network`, {
  env: awsEnv,
  envName,
  vpcCidr: envConfig.vpcCidr as string,
  description: `Salesforce Bulk Loader — network layer (${envName})`,
});

// Stack 2: RDS, S3, Secrets Manager, ECR, SES identity
const dataStack = new DataStack(app, `${prefix}-Data`, {
  env: awsEnv,
  envName,
  vpc: networkStack.vpc,
  backendServiceSecurityGroup: networkStack.backendServiceSecurityGroup,
  tier,
  hostedZoneDomain: envConfig.hostedZoneDomain as string,
  sesIdentityDomain: envConfig.sesIdentityDomain as string | undefined,
  description: `Salesforce Bulk Loader — data layer (${envName})`,
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
  description: `Salesforce Bulk Loader — backend service (${envName})`,
});
backendStack.addDependency(dataStack);

// Stack 4: CloudFront + S3 static frontend
new FrontendStack(app, `${prefix}-Frontend`, {
  env: awsEnv,
  envName,
  domainName: envConfig.domainName as string,
  certificateArn: envConfig.certificateArn as string,
  backendOriginDomainName: envConfig.backendDomainName as string,
  description: `Salesforce Bulk Loader — frontend hosting (${envName})`,
}).addDependency(backendStack);

app.synth();
