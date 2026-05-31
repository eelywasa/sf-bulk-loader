import { Template, Match } from 'aws-cdk-lib/assertions';
import { synthStacks } from './helpers';

/**
 * SFBL-300 - clean teardown: ECR emptyOnDelete + explicit ECS
 * capacity-provider association so `cdk destroy` needs no manual intervention.
 */
describe('SFBL-300 clean teardown', () => {
  describe('ECR repository', () => {
    test('non-prod tiers set emptyOnDelete so destroy purges images', () => {
      const { data } = synthStacks({ envName: 'staging' });
      Template.fromStack(data).hasResourceProperties('AWS::ECR::Repository', {
        EmptyOnDelete: true,
      });
    });

    test('production keeps the repository on destroy and does not empty it', () => {
      const { data } = synthStacks({ envName: 'production' });
      const template = Template.fromStack(data);
      // RETAIN policy on the repo + emptyOnDelete:false - production images
      // are never purged on destroy.
      template.hasResource('AWS::ECR::Repository', {
        DeletionPolicy: 'Retain',
        Properties: Match.objectLike({ EmptyOnDelete: false }),
      });
    });
  });

  describe('ECS capacity-provider association', () => {
    test('explicit association exists with FARGATE providers', () => {
      const { backend } = synthStacks();
      Template.fromStack(backend).hasResourceProperties(
        'AWS::ECS::ClusterCapacityProviderAssociations',
        {
          CapacityProviders: ['FARGATE', 'FARGATE_SPOT'],
        },
      );
    });

    test('association depends on the cluster so CFN detaches it before deleting the cluster', () => {
      const { backend } = synthStacks();
      const template = Template.fromStack(backend);
      const clusterId = Object.keys(
        template.findResources('AWS::ECS::Cluster'),
      )[0];
      template.hasResource('AWS::ECS::ClusterCapacityProviderAssociations', {
        DependsOn: Match.arrayWith([clusterId]),
      });
    });
  });
});
