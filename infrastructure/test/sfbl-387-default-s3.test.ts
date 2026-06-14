import { Template, Match } from 'aws-cdk-lib/assertions';
import { synthStacks } from './helpers';

/**
 * SFBL-387: the CDK backend + migration task definitions inject the first-party
 * bucket env vars, and the TASK roles (not execution roles) get scoped S3
 * access to exactly the two bucket ARNs.
 *
 * Falsification:
 *  - a wildcard S3 resource (Resource: "*") on the task role fails the scope test;
 *  - S3 actions on an execution role fails the direction test;
 *  - a missing S3_*_BUCKET env var fails the env-var test.
 */
describe('SFBL-387 — first-party S3 default storage (CDK)', () => {
  const { backend, data } = synthStacks();
  const backendTemplate = Template.fromStack(backend);
  const dataTemplate = Template.fromStack(data);

  test('backend task definition carries the three S3 env vars', () => {
    backendTemplate.hasResourceProperties('AWS::ECS::TaskDefinition', {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Name: 'backend',
          Environment: Match.arrayWith([
            Match.objectLike({ Name: 'S3_INPUT_BUCKET' }),
            Match.objectLike({ Name: 'S3_OUTPUT_BUCKET' }),
            Match.objectLike({ Name: 'S3_BUCKET_REGION' }),
          ]),
        }),
      ]),
    });
  });

  test('migration task definition carries the three S3 env vars', () => {
    dataTemplate.hasResourceProperties('AWS::ECS::TaskDefinition', {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Name: 'migration',
          Environment: Match.arrayWith([
            Match.objectLike({ Name: 'S3_INPUT_BUCKET' }),
            Match.objectLike({ Name: 'S3_OUTPUT_BUCKET' }),
            Match.objectLike({ Name: 'S3_BUCKET_REGION' }),
          ]),
        }),
      ]),
    });
  });

  // The scoped S3 grant appears as an inline policy on the task role. We assert
  // object RW + ListBucket exist and that no S3 statement uses Resource: "*".
  function s3Statements(template: Template): any[] {
    const policies = template.findResources('AWS::IAM::Policy');
    const stmts: any[] = [];
    for (const policy of Object.values(policies)) {
      const doc = (policy as any).Properties.PolicyDocument.Statement as any[];
      for (const s of doc) {
        const actions = Array.isArray(s.Action) ? s.Action : [s.Action];
        if (actions.some((a: string) => typeof a === 'string' && a.startsWith('s3:'))) {
          stmts.push(s);
        }
      }
    }
    return stmts;
  }

  test('backend task role gets object RW + ListBucket, scoped (no wildcard resource)', () => {
    const stmts = s3Statements(backendTemplate);
    const allActions = stmts.flatMap((s) => (Array.isArray(s.Action) ? s.Action : [s.Action]));
    expect(allActions).toEqual(
      expect.arrayContaining(['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:ListBucket']),
    );
    // No S3 statement may use a bare "*" resource.
    for (const s of stmts) {
      const resources = Array.isArray(s.Resource) ? s.Resource : [s.Resource];
      expect(resources).not.toContain('*');
    }
    // There IS at least one S3 statement (falsifies an accidental no-grant regression).
    expect(stmts.length).toBeGreaterThan(0);
  });

  test('migration task role also gets scoped S3 grants (no wildcard resource)', () => {
    const stmts = s3Statements(dataTemplate);
    const allActions = stmts.flatMap((s) => (Array.isArray(s.Action) ? s.Action : [s.Action]));
    expect(allActions).toEqual(
      expect.arrayContaining(['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:ListBucket']),
    );
    for (const s of stmts) {
      const resources = Array.isArray(s.Resource) ? s.Resource : [s.Resource];
      expect(resources).not.toContain('*');
    }
    expect(stmts.length).toBeGreaterThan(0);
  });

  test('the S3 grant targets the task role, not the execution role', () => {
    // Collect logical IDs of execution roles (those assumable by ecs-tasks AND
    // carrying the ECR/logs managed posture are created by CDK as ExecutionRole).
    // Simpler + robust: assert no IAM policy that grants s3:* is attached to a
    // role whose logical id contains "ExecutionRole".
    const policies = backendTemplate.findResources('AWS::IAM::Policy');
    for (const policy of Object.values(policies)) {
      const props = (policy as any).Properties;
      const doc = props.PolicyDocument.Statement as any[];
      const hasS3 = doc.some((s) => {
        const actions = Array.isArray(s.Action) ? s.Action : [s.Action];
        return actions.some((a: string) => typeof a === 'string' && a.startsWith('s3:'));
      });
      if (!hasS3) continue;
      const rolesRef = JSON.stringify(props.Roles ?? []);
      expect(rolesRef).not.toContain('ExecutionRole');
    }
  });
});
