import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { FrontendStack, FrontendStackProps } from '../lib/frontend-stack';

/**
 * SFBL-390: the FrontendStack manages the frontend domain_name Route53 A-alias
 * to the CloudFront distribution in-stack (created/destroyed/repointed with the
 * stack - no manual Route53 step), gated by the manageFrontendDns flag.
 *
 * Falsification:
 *  - with manageFrontendDns=false the synth must contain NO Route53 RecordSet,
 *    proving external-DNS deployments are unaffected. If a record is emitted
 *    when the flag is false, the test fails.
 */
const baseProps: Omit<FrontendStackProps, 'manageFrontendDns'> = {
  env: { account: '123456789012', region: 'eu-west-1' },
  envName: 'staging',
  domainName: 'bulk-loader.example.com',
  certificateArn:
    'arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000',
  backendOriginDomainName: 'api.bulk-loader.example.com',
  hostedZoneDomain: 'bulk-loader.example.com',
};

function synthFrontend(manageFrontendDns?: boolean): Template {
  const app = new cdk.App();
  const stack = new FrontendStack(app, 'BulkLoader-staging-Frontend', {
    ...baseProps,
    manageFrontendDns,
  });
  return Template.fromStack(stack);
}

describe('SFBL-390 — frontend apex DNS alias (CDK)', () => {
  test('default (flag unset): one A-alias for domain_name targeting CloudFront', () => {
    const template = synthFrontend(undefined);

    template.resourceCountIs('AWS::Route53::RecordSet', 1);
    template.hasResourceProperties('AWS::Route53::RecordSet', {
      Name: 'bulk-loader.example.com.',
      Type: 'A',
      HostedZoneName: 'bulk-loader.example.com.',
      AliasTarget: Match.objectLike({
        // Global CloudFront alias hosted-zone id.
        HostedZoneId: 'Z2FDTNDATAQYW2',
        DNSName: Match.anyValue(),
      }),
    });
  });

  test('manageFrontendDns=true behaves identically to the default', () => {
    synthFrontend(true).resourceCountIs('AWS::Route53::RecordSet', 1);
  });

  test('manageFrontendDns=false: no Route53 record is emitted (falsification)', () => {
    synthFrontend(false).resourceCountIs('AWS::Route53::RecordSet', 0);
  });
});
