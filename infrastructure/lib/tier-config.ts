/**
 * TierConfig — shape of a Bronze/Silver/Gold sizing preset.
 *
 * Presets live in `cdk.json` under `context.tiers`. Each environment block
 * under `context.environments` selects one via the `tier` field. Resolved
 * shape is passed to every stack as `props.tier` so resource sizing,
 * retention, and feature flags come from a single coherent set of values.
 *
 * See `docs/deployment/aws.md` "Sizing and cost" for the canonical matrix.
 *
 * - Fields up to `outputRetentionDays` are consumed by SFBL-275 stacks
 *   (Network / Data / Backend / Frontend).
 * - Gold-specific feature flags below — `workerEnabled`, `redisEnabled`,
 *   `redisInstanceClass`, `redisMultiAz`, `wafEnabled`, `useFargateSpot` —
 *   are part of the contract today but are not yet provisioned. SFBL-295
 *   reads the same flags and adds the corresponding resources (worker
 *   Fargate tier, ElastiCache Redis, AWS WAF, Fargate Spot capacity
 *   provider strategy) when each is true.
 */
export interface TierConfig {
  // RDS — sized in B1 / consumed throughout DataStack
  rdsInstanceClass: string;
  rdsMultiAz: boolean;
  rdsAllocatedStorage: number;
  rdsBackupRetentionDays: number;
  /**
   * Whether to protect the RDS instance from accidental deletion. Drives both
   * `deletionProtection: true` on the DBInstance (RDS-level guard against the
   * DeleteDBInstance API call) and `RemovalPolicy.RETAIN` on the CDK construct
   * (CloudFormation will retain the DB if the stack is destroyed). Should be
   * enabled for any tier that holds real data — i.e. Silver and Gold.
   *
   * Decoupled from `rdsMultiAz` because Multi-AZ is a HA/cost decision while
   * deletion protection is a data-loss guard; they don't have to move
   * together. Silver runs single-AZ but still holds real data and should be
   * protected.
   */
  rdsDeletionProtection: boolean;

  // ECS — Fargate task shape and replica count
  ecsDesiredCount: number;
  ecsTaskCpu: number;
  ecsTaskMemory: number;

  // CloudWatch
  logRetentionDays: number;
  containerInsightsEnabled: boolean;

  // S3 lifecycle (B3) — 0 = retain forever
  inputRetentionDays: number;
  outputRetentionDays: number;

  // Gold-only flags — consumed by SFBL-295. Default-falsy on Bronze/Silver.
  workerEnabled?: boolean;
  redisEnabled?: boolean;
  redisInstanceClass?: string;
  redisMultiAz?: boolean;
  wafEnabled?: boolean;
  useFargateSpot?: boolean;
}

/**
 * Resolve a tier shape from CDK context — `tiers[envConfig.tier]`.
 *
 * Throws with a clear message if the environment doesn't name a tier or
 * the tier preset is missing — both are setup errors that should fail at
 * synth time, not at deploy time.
 */
export function resolveTier(
  envName: string,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  envConfig: Record<string, any>,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tiers: Record<string, any> | undefined,
): TierConfig {
  const tierName = envConfig.tier as string | undefined;
  if (!tierName) {
    throw new Error(
      `Environment "${envName}" is missing a "tier" field. ` +
      `Add tier: "bronze" | "silver" | "gold" to cdk.json under context.environments.${envName}, ` +
      `or override in cdk.context.json. See docs/deployment/aws.md "Sizing and cost".`,
    );
  }
  const preset = tiers?.[tierName];
  if (!preset) {
    throw new Error(
      `Tier preset "${tierName}" not found in cdk.json context.tiers. ` +
      `Available presets: ${Object.keys(tiers ?? {}).join(', ')}.`,
    );
  }
  return preset as TierConfig;
}

/**
 * Convert `logRetentionDays` from a number into the CDK enum value used by
 * `logs.LogGroup({ retention })`. CDK doesn't accept arbitrary day values —
 * only specific RetentionDays enum members. Round to the nearest supported
 * value (always rounding up so we never retain less than the operator asked).
 */
import * as logs from 'aws-cdk-lib/aws-logs';
export function logRetentionFromDays(days: number): logs.RetentionDays {
  // Sorted ascending. Pick the first member >= days, or the largest if days exceeds all.
  const supported: Array<[number, logs.RetentionDays]> = [
    [1, logs.RetentionDays.ONE_DAY],
    [3, logs.RetentionDays.THREE_DAYS],
    [5, logs.RetentionDays.FIVE_DAYS],
    [7, logs.RetentionDays.ONE_WEEK],
    [14, logs.RetentionDays.TWO_WEEKS],
    [30, logs.RetentionDays.ONE_MONTH],
    [60, logs.RetentionDays.TWO_MONTHS],
    [90, logs.RetentionDays.THREE_MONTHS],
    [120, logs.RetentionDays.FOUR_MONTHS],
    [150, logs.RetentionDays.FIVE_MONTHS],
    [180, logs.RetentionDays.SIX_MONTHS],
    [365, logs.RetentionDays.ONE_YEAR],
    [400, logs.RetentionDays.THIRTEEN_MONTHS],
    [545, logs.RetentionDays.EIGHTEEN_MONTHS],
    [731, logs.RetentionDays.TWO_YEARS],
    [1827, logs.RetentionDays.FIVE_YEARS],
    [3653, logs.RetentionDays.TEN_YEARS],
  ];
  for (const [d, enumVal] of supported) {
    if (d >= days) return enumVal;
  }
  return logs.RetentionDays.TEN_YEARS;
}
