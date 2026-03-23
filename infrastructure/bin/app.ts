#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { NetworkStack } from '../lib/network-stack';
import { DataStack } from '../lib/data-stack';
import { BackendStack } from '../lib/backend-stack';
import { FrontendStack } from '../lib/frontend-stack';

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

// Stack 2: RDS, S3, and Secrets Manager
const dataStack = new DataStack(app, `${prefix}-Data`, {
  env: awsEnv,
  envName,
  vpc: networkStack.vpc,
  backendServiceSecurityGroup: networkStack.backendServiceSecurityGroup,
  rdsInstanceClass: envConfig.rdsInstanceClass as string,
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
  inputBucket: dataStack.inputBucket,
  outputBucket: dataStack.outputBucket,
  encryptionKeySecret: dataStack.encryptionKeySecret,
  jwtSecretKeySecret: dataStack.jwtSecretKeySecret,
  databaseUrlSecret: dataStack.databaseUrlSecret,
  adminPasswordSecret: dataStack.adminPasswordSecret,
  backendDomainName: envConfig.backendDomainName as string,
  backendCertificateArn: envConfig.backendCertificateArn as string,
  hostedZoneDomain: envConfig.hostedZoneDomain as string,
  ecsDesiredCount: (envConfig.ecsDesiredCount as number) ?? 1,
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
