import { Template, Match } from 'aws-cdk-lib/assertions';
import { synthStacks } from './helpers';

/**
 * SFBL-355 - CDK hardening quick-wins from the cfn-guard assessment:
 * ECS deployment circuit breaker, S3 access logging, RDS gp3 +
 * autoMinorVersionUpgrade, and (tier-gated) VPC flow logs.
 */
describe('SFBL-355 hardening quick-wins', () => {
  test('ECS service has a deployment circuit breaker with rollback', () => {
    const { backend } = synthStacks();
    Template.fromStack(backend).hasResourceProperties('AWS::ECS::Service', {
      DeploymentConfiguration: Match.objectLike({
        DeploymentCircuitBreaker: { Enable: true, Rollback: true },
      }),
    });
  });

  test('input + output buckets log to a dedicated access-logs bucket', () => {
    const { data } = synthStacks();
    const template = Template.fromStack(data);
    // Three buckets: input, output, access-logs.
    template.resourceCountIs('AWS::S3::Bucket', 3);
    // Both data buckets declare a LoggingConfiguration.
    const logged = Object.values(
      template.findResources('AWS::S3::Bucket'),
    ).filter((b) => b.Properties?.LoggingConfiguration);
    expect(logged).toHaveLength(2);
  });

  test('RDS uses gp3 storage and auto minor version upgrade', () => {
    const { data } = synthStacks();
    Template.fromStack(data).hasResourceProperties('AWS::RDS::DBInstance', {
      StorageType: 'gp3',
      AutoMinorVersionUpgrade: true,
    });
  });

  describe('VPC flow logs (tier-gated)', () => {
    test('enabled on an observability tier (containerInsightsEnabled)', () => {
      const { network } = synthStacks({
        tier: { containerInsightsEnabled: true },
      });
      Template.fromStack(network).resourceCountIs('AWS::EC2::FlowLog', 1);
    });

    test('disabled on a disposable tier (bronze: containerInsights off)', () => {
      const { network } = synthStacks({
        tier: { containerInsightsEnabled: false },
      });
      Template.fromStack(network).resourceCountIs('AWS::EC2::FlowLog', 0);
    });
  });
});
