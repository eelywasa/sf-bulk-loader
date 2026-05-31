import * as cdk from 'aws-cdk-lib';
import { NetworkStack } from '../lib/network-stack';
import { DataStack } from '../lib/data-stack';
import { BackendStack } from '../lib/backend-stack';
import { TierConfig } from '../lib/tier-config';

/**
 * Test scaffolding for the SFBL-275 / SFBL-299 CDK stacks.
 *
 * `synthStacks()` mirrors the Network → Data → Backend wiring in `bin/app.ts`
 * with deterministic placeholder values, so individual stacks can be turned
 * into `Template.fromStack(...)` assertions. A `cdk.App()` constructed in a
 * test does NOT auto-load `cdk.json` context the way the CLI does, so tier
 * values and any `-c` context are passed explicitly here.
 */

/** Bronze preset mirror (see cdk.json context.tiers.bronze). Non-prod defaults. */
export const BRONZE_TIER: TierConfig = {
  rdsInstanceClass: 'db.t4g.micro',
  rdsMultiAz: false,
  rdsAllocatedStorage: 20,
  rdsBackupRetentionDays: 1,
  rdsDeletionProtection: false,
  ecsDesiredCount: 1,
  ecsTaskCpu: 512,
  ecsTaskMemory: 1024,
  logRetentionDays: 7,
  containerInsightsEnabled: false,
  inputRetentionDays: 7,
  outputRetentionDays: 30,
};

export interface SynthOptions {
  /** Environment name - drives the env==='production' branches. Defaults to 'staging'. */
  envName?: string;
  /** Partial tier overrides merged over BRONZE_TIER. */
  tier?: Partial<TierConfig>;
  /** CDK context (e.g. { restoreFromSnapshot: 'snap-123' }). */
  context?: Record<string, unknown>;
}

export function synthStacks(opts: SynthOptions = {}) {
  const envName = opts.envName ?? 'staging';
  const tier: TierConfig = { ...BRONZE_TIER, ...opts.tier };
  const app = new cdk.App({ context: opts.context });
  const env: cdk.Environment = { account: '123456789012', region: 'eu-west-1' };

  const network = new NetworkStack(app, `BulkLoader-${envName}-Network`, {
    env,
    envName,
    vpcCidr: '10.0.0.0/16',
  });

  const data = new DataStack(app, `BulkLoader-${envName}-Data`, {
    env,
    envName,
    vpc: network.vpc,
    backendServiceSecurityGroup: network.backendServiceSecurityGroup,
    tier,
    ecrImageTag: 'latest',
    hostedZoneDomain: 'example.invalid',
  });

  const backend = new BackendStack(app, `BulkLoader-${envName}-Backend`, {
    env,
    envName,
    vpc: network.vpc,
    albSecurityGroup: network.albSecurityGroup,
    backendServiceSecurityGroup: network.backendServiceSecurityGroup,
    backendRepository: data.backendRepository,
    encryptionKeySecret: data.encryptionKeySecret,
    jwtSecretKeySecret: data.jwtSecretKeySecret,
    databaseUrlSecret: data.databaseUrlSecret,
    adminEmailSecret: data.adminEmailSecret,
    adminPasswordSecret: data.adminPasswordSecret,
    sesIdentityArn: data.sesIdentityArn,
    backendDomainName: 'api.example.invalid',
    backendCertificateArn:
      'arn:aws:acm:eu-west-1:123456789012:certificate/00000000-0000-0000-0000-000000000000',
    hostedZoneDomain: 'example.invalid',
    tier,
    ecrImageTag: 'latest',
  });

  return { app, network, data, backend };
}
