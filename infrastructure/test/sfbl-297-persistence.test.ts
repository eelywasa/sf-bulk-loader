import { Template, Match } from 'aws-cdk-lib/assertions';
import { synthStacks } from './helpers';

/**
 * SFBL-297 - persistOnDestroy: snapshot the DB and retain secrets/buckets on
 * teardown, and restore from a snapshot via `-c restoreFromSnapshot=<id>`.
 */
describe('SFBL-297 persistOnDestroy', () => {
  const APP_SECRET_NAMES = [
    '/staging/bulk-loader/encryption-key',
    '/staging/bulk-loader/jwt-secret-key',
    '/staging/bulk-loader/database-url',
    '/staging/bulk-loader/admin-email',
    '/staging/bulk-loader/admin-password',
  ];

  describe('disposable tier (persistOnDestroy: false)', () => {
    const data = () => synthStacks({ tier: { persistOnDestroy: false } }).data;

    test('RDS is wiped on destroy', () => {
      Template.fromStack(data()).hasResource('AWS::RDS::DBInstance', {
        DeletionPolicy: 'Delete',
      });
    });

    test('app secrets and data buckets are wiped on destroy', () => {
      const template = Template.fromStack(data());
      for (const name of APP_SECRET_NAMES) {
        template.hasResource('AWS::SecretsManager::Secret', {
          DeletionPolicy: 'Delete',
          Properties: Match.objectLike({ Name: name }),
        });
      }
      // Both data buckets delete.
      const buckets = template.findResources('AWS::S3::Bucket');
      for (const b of Object.values(buckets)) {
        expect(b.DeletionPolicy).toBe('Delete');
      }
    });
  });

  describe('persistent tier (persistOnDestroy: true)', () => {
    const data = () => synthStacks({ tier: { persistOnDestroy: true } }).data;

    test('RDS snapshots on destroy', () => {
      Template.fromStack(data()).hasResource('AWS::RDS::DBInstance', {
        DeletionPolicy: 'Snapshot',
      });
    });

    test('deletion protection is forced off so the snapshot-delete can run', () => {
      // Even when the tier also asks for rdsDeletionProtection, persist wins:
      // a SNAPSHOT removal policy cannot run while deletion protection is on.
      const d = synthStacks({
        tier: { persistOnDestroy: true, rdsDeletionProtection: true },
      }).data;
      const template = Template.fromStack(d);
      template.hasResource('AWS::RDS::DBInstance', { DeletionPolicy: 'Snapshot' });
      template.hasResourceProperties('AWS::RDS::DBInstance', {
        DeletionProtection: false,
      });
    });

    test('app secrets are retained on destroy', () => {
      const template = Template.fromStack(data());
      for (const name of APP_SECRET_NAMES) {
        template.hasResource('AWS::SecretsManager::Secret', {
          DeletionPolicy: 'Retain',
          Properties: Match.objectLike({ Name: name }),
        });
      }
    });

    test('data buckets are retained on destroy', () => {
      const template = Template.fromStack(data());
      // Input + output + access-logs buckets all retain (three in DataStack).
      const buckets = template.findResources('AWS::S3::Bucket');
      expect(Object.values(buckets).length).toBe(3);
      for (const b of Object.values(buckets)) {
        expect(b.DeletionPolicy).toBe('Retain');
      }
    });

    test('retained buckets get deterministic names so a restore can re-import them', () => {
      const template = Template.fromStack(data());
      const names = Object.values(template.findResources('AWS::S3::Bucket'))
        .map((b) => b.Properties?.BucketName)
        .sort();
      expect(names).toEqual([
        'bulk-loader-staging-access-logs',
        'bulk-loader-staging-input',
        'bulk-loader-staging-output',
      ]);
    });
  });

  describe('restore (-c restoreFromSnapshot=<id>)', () => {
    const restored = () =>
      synthStacks({
        tier: { persistOnDestroy: true },
        context: { restoreFromSnapshot: 'bulkloader-staging-snap-2026' },
      }).data;

    test('RDS is rebuilt from the named snapshot', () => {
      Template.fromStack(restored()).hasResourceProperties('AWS::RDS::DBInstance', {
        DBSnapshotIdentifier: 'bulkloader-staging-snap-2026',
      });
    });

    test('app secrets are imported by name, not recreated', () => {
      const template = Template.fromStack(restored());
      // None of the five app-secret names should appear as a created
      // AWS::SecretsManager::Secret resource (they are imported instead).
      const secrets = template.findResources('AWS::SecretsManager::Secret');
      const createdNames = Object.values(secrets)
        .map((s) => s.Properties?.Name)
        .filter(Boolean);
      for (const name of APP_SECRET_NAMES) {
        expect(createdNames).not.toContain(name);
      }
    });

    test('input/output buckets are imported by name, not recreated (Codex #97)', () => {
      // On restore the retained, deterministically-named buckets are imported
      // via s3.Bucket.fromBucketName, so no AWS::S3::Bucket resources are
      // created - the retained objects reattach instead of orphaning.
      const template = Template.fromStack(restored());
      template.resourceCountIs('AWS::S3::Bucket', 0);
    });
  });
});
