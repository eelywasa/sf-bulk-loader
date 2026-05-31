import { Template } from 'aws-cdk-lib/assertions';
import { synthStacks } from './helpers';

/**
 * SFBL-299 follow-up - persistOnDestroy is an environment-lifecycle decision
 * resolved per-environment (envConfig.persistOnDestroy ?? tier.persistOnDestroy
 * ?? false), decoupled from the tier's sizing. A small (bronze) environment can
 * therefore be a real, persistent one without flipping the shared tier preset.
 */
describe('SFBL-299 per-environment persistence decoupling', () => {
  const dbDeletionPolicy = (opts: Parameters<typeof synthStacks>[0]) => {
    const template = Template.fromStack(synthStacks(opts).data);
    const db = Object.values(
      template.findResources('AWS::RDS::DBInstance'),
    )[0];
    return db.DeletionPolicy;
  };

  test('env override turns persistence ON for a tier that defaults OFF', () => {
    // Bronze-like tier with persistOnDestroy unset, but the environment opts in.
    expect(
      dbDeletionPolicy({ persistOnDestroy: true }),
    ).toBe('Snapshot');
  });

  test('env override turns persistence OFF for a tier that defaults ON', () => {
    // Tier default is persist, but this environment opts out (disposable).
    expect(
      dbDeletionPolicy({ tier: { persistOnDestroy: true }, persistOnDestroy: false }),
    ).toBe('Delete');
  });

  test('falls back to the tier default when the env does not specify', () => {
    expect(dbDeletionPolicy({ tier: { persistOnDestroy: true } })).toBe('Snapshot');
    expect(dbDeletionPolicy({ tier: { persistOnDestroy: false } })).toBe('Delete');
  });

  test('disposable env keeps CDK-generated bucket names (collision-free cycling)', () => {
    const template = Template.fromStack(synthStacks({ persistOnDestroy: false }).data);
    const named = Object.values(template.findResources('AWS::S3::Bucket')).filter(
      (b) => b.Properties?.BucketName,
    );
    expect(named).toHaveLength(0);
  });
});
