import { Template, Match } from 'aws-cdk-lib/assertions';
import { synthStacks } from './helpers';

/**
 * SFBL-298 - the one-shot Alembic migration task definition lives in DataStack
 * (not BackendStack) so it exists after `cdk deploy Network Data`, before the
 * service stack. This closes the first-deploy chicken-and-egg from DECISIONS 027.
 */
describe('SFBL-298 migration task relocation', () => {
  const migrationContainer = Match.objectLike({
    ContainerDefinitions: Match.arrayWith([
      Match.objectLike({
        Name: 'migration',
        Command: ['sh', '-c', 'alembic upgrade head'],
        Environment: Match.arrayWith([
          { Name: 'RUN_MIGRATIONS', Value: 'true' },
        ]),
      }),
    ]),
  });

  test('migration task definition is in DataStack', () => {
    const { data } = synthStacks();
    Template.fromStack(data).hasResourceProperties(
      'AWS::ECS::TaskDefinition',
      migrationContainer,
    );
  });

  test('DataStack provides a dedicated migration cluster + outputs', () => {
    const { data } = synthStacks();
    const template = Template.fromStack(data);
    // A bare Fargate cluster so the migration can run before BackendStack exists.
    template.hasResourceProperties('AWS::ECS::Cluster', {
      ClusterName: 'bulk-loader-staging-migration',
    });
    template.hasOutput('MigrationTaskDefinitionArn', {});
    template.hasOutput('MigrationClusterName', {});
    template.hasOutput('BackendServiceSecurityGroupId', {});
  });

  test('migration cluster has no Fargate capacity-provider association (no teardown race)', () => {
    const { data } = synthStacks();
    // The migration cluster runs tasks via --launch-type FARGATE, so it must
    // not carry a capacity-provider association (which would reintroduce the
    // SFBL-300 destroy race on a different cluster).
    Template.fromStack(data).resourceCountIs(
      'AWS::ECS::ClusterCapacityProviderAssociations',
      0,
    );
  });

  test('BackendStack no longer defines the migration task or its outputs', () => {
    const { backend } = synthStacks();
    const template = Template.fromStack(backend);

    // The only ECS task definition left in BackendStack is the service task.
    const taskDefs = template.findResources('AWS::ECS::TaskDefinition');
    const migrationDefs = Object.values(taskDefs).filter((r) =>
      (r.Properties?.ContainerDefinitions ?? []).some(
        (c: { Name?: string }) => c.Name === 'migration',
      ),
    );
    expect(migrationDefs).toHaveLength(0);

    expect(
      Object.keys(template.findOutputs('MigrationTaskDefinitionArn')),
    ).toHaveLength(0);
    expect(
      Object.keys(template.findOutputs('BackendServiceSecurityGroupId')),
    ).toHaveLength(0);
  });

  test('service task still runs with RUN_MIGRATIONS=false', () => {
    const { backend } = synthStacks();
    Template.fromStack(backend).hasResourceProperties('AWS::ECS::TaskDefinition', {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Name: 'backend',
          Environment: Match.arrayWith([
            { Name: 'RUN_MIGRATIONS', Value: 'false' },
          ]),
        }),
      ]),
    });
  });

  // SFBL-391: hosted task defs must set APP_ENV=production. Absence falls back
  // to the app's "development" default, which turns SQLAlchemy echo on.
  test('service + migration tasks set APP_ENV=production', () => {
    const { backend, data } = synthStacks();
    Template.fromStack(backend).hasResourceProperties('AWS::ECS::TaskDefinition', {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Name: 'backend',
          Environment: Match.arrayWith([{ Name: 'APP_ENV', Value: 'production' }]),
        }),
      ]),
    });
    Template.fromStack(data).hasResourceProperties('AWS::ECS::TaskDefinition', {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Name: 'migration',
          Environment: Match.arrayWith([{ Name: 'APP_ENV', Value: 'production' }]),
        }),
      ]),
    });
  });
});
