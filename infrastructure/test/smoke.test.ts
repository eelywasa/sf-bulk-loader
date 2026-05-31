import { Template } from 'aws-cdk-lib/assertions';
import { synthStacks } from './helpers';

describe('stack synthesis smoke test', () => {
  test('all stacks synthesise with the expected core resources', () => {
    const { network, data, backend } = synthStacks();

    Template.fromStack(network).resourceCountIs('AWS::EC2::VPC', 1);

    const dataTemplate = Template.fromStack(data);
    dataTemplate.resourceCountIs('AWS::ECR::Repository', 1);
    dataTemplate.resourceCountIs('AWS::RDS::DBInstance', 1);
    dataTemplate.resourceCountIs('AWS::S3::Bucket', 2);

    const backendTemplate = Template.fromStack(backend);
    backendTemplate.resourceCountIs('AWS::ECS::Cluster', 1);
    backendTemplate.resourceCountIs('AWS::ECS::Service', 1);
  });
});
