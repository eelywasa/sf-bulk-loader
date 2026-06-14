---
title: Migrating from self-hosted to AWS-hosted
slug: migrating-to-aws-hosted
nav_order: 50
tags: [deployment, migration, aws, postgres, s3, ses]
summary: >-
  One-shot offline cutover from a self-hosted Docker Bulk Loader instance to the AWS-hosted distribution profile. Includes DB, encryption-key, S3, SES, and DNS handover.
---

## What this covers / who should read this

Operators running the Bulk Loader under the `self_hosted` distribution profile (the default Docker Compose deploy described in [`docker.md`](docker.md)) who want to move it to the `aws_hosted` profile on AWS (CloudFront + ALB + ECS/Fargate + RDS PostgreSQL + S3 + SES, described in [`aws.md`](aws.md)).

Migrating between profiles isn't just a database swap — every storage and integration boundary changes:

| Concern | `self_hosted` | `aws_hosted` |
|---|---|---|
| Database | SQLite (default) or operator-managed Postgres | RDS PostgreSQL with `?ssl=require` |
| Input files | Local filesystem (`INPUT_DIR`) | S3 input bucket via per-Connection IAM keys |
| Output files | Local filesystem (`OUTPUT_DIR`) | S3 output bucket via per-Connection IAM keys |
| Email backend | `noop` (default) or `smtp` | `ses` (defaults to SES v2 SendEmail with task-role IAM) |
| Transport | HTTP behind nginx | HTTPS, ALB, CloudFront in front |
| Secret storage | `.env` files / Docker secrets | AWS Secrets Manager + SSM Parameter Store |
| Encryption-key custody | Host file (`data/db/encryption.key`) | Secrets Manager `/{env}/bulk-loader/encryption-key` |
| Auth mode | Local (username/password) | Local (unchanged) |

This is a **one-way, offline cutover**. Plan a maintenance window — the guide assumes the running instance can be stopped for the duration. Live-traffic / zero-downtime migration is out of scope.

The reverse direction (`aws_hosted → self_hosted`) is not documented; if you need it, [`docs/deployment/aws.md`](aws.md) and the database export from RDS are your starting points.

---

## Pre-flight checklist

Work through this list before starting the cutover. Most of it can be done well in advance; only the final step is time-sensitive.

- [ ] **AWS account ready.** A working `aws_hosted` deploy exists or is provisioned through [`aws.md`](aws.md) steps 1–7 (network, data, secrets, SSM, ECR image push). The actual cutover lands on top of an empty-but-deployed environment.
- [ ] **ACM certificates issued** in `us-east-1` (CloudFront) and the deploy region (ALB), with DNS validation completed. See [`aws.md`](aws.md) step 2.
- [ ] **Database engine choice resolved.** If your self-hosted instance is on SQLite, run [`migrating-to-postgres.md`](migrating-to-postgres.md) **first**, against a Postgres instance you can reach from the cutover machine. Don't migrate SQLite directly to RDS — it's the same script, but RDS isn't reachable from a developer workstation without bastion/SSH-tunnel hassle.
- [ ] **Encryption key recovered** from the running self-hosted host (see [step 3](#3-recover-the-encryption-key) below). Treat it like any other secret — never paste it into a chat or commit it.
- [ ] **JWT secret rotation decision made.** The cutover invalidates all existing sessions either way; this guide recommends rotating the JWT signing secret on cutover so old tokens immediately stop being honoured.
- [ ] **DNS plan.** You're flipping `bulkloader.example.com` from your self-hosted ALB/nginx to the AWS CloudFront distribution. Decide ahead of time whether you'll do an A-record swap with TTL pre-lowered or a phased cutover behind a new hostname.
- [ ] **Disable IaC frontend DNS until cutover (SFBL-390).** The AWS stack manages the `domain_name` → CloudFront alias in IaC by default (`manageFrontendDns` / `manage_frontend_dns`, default true). In a staged cutover that record still points at your **live self-hosted** system, so you must **deploy with the flag `false`** — otherwise the deploy moves production traffic to the unvalidated CloudFront before step 9. (On the Terraform flavour this is critical: `allow_overwrite = true` means a default-on apply silently clobbers the live record.) You flip DNS by hand in step 10, then optionally hand ownership to IaC afterwards.
- [ ] **Admin email + password chosen** for the bootstrap admin user that AWS Secrets Manager will inject into `lifespan()` on first boot. Existing user records survive the DB migration; this admin is the one you'll log in as the moment the AWS task starts, before the migrated users come back online.
- [ ] **Maintenance window communicated** to whoever uses the system. Plan for ~30–60 minutes of downtime depending on data volume.

---

## Step-by-step cutover

### 1. Stop self-hosted writes

```bash
docker compose stop backend
```

The frontend can stay up if you want to show a maintenance page; the database and storage volumes are now frozen. Confirm no orphaned `python` processes are still touching the SQLite file or the input/output directories.

### 2. Migrate the database to Postgres (if you started on SQLite)

If your self-hosted instance is on SQLite, follow [`migrating-to-postgres.md`](migrating-to-postgres.md) end-to-end against a Postgres instance you can reach from this machine. The recommended target is **a temporary Postgres on the same machine** — the script writes a fully populated Postgres DB locally, which you'll then dump and restore into RDS in the next step.

If your self-hosted instance is already on Postgres, skip ahead to step 2b.

#### 2b. Dump and restore into RDS

```bash
# 1. Dump the local Postgres into a single SQL file.
pg_dump --no-owner --no-acl --clean --if-exists \
  -h localhost -U bulk_loader -d bulk_loader \
  > /tmp/bulk_loader.dump.sql

# 2. Restore into RDS. Run this from a host with network access to RDS:
#    a Fargate one-shot task in the same VPC, an EC2 bastion in a public
#    subnet, or a temporary "publicly accessible" toggle on RDS for the
#    duration of the restore (revert it before going live).
RDS_ENDPOINT=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Data \
  --query "Stacks[0].Outputs[?OutputKey=='RdsEndpoint'].OutputValue" --output text)
RDS_CREDS=$(aws secretsmanager get-secret-value \
  --secret-id /${ENV}/bulk-loader/rds-credentials --query SecretString --output text)
RDS_USER=$(echo "$RDS_CREDS" | python3 -c "import sys,json;print(json.load(sys.stdin)['username'])")
RDS_PASS=$(echo "$RDS_CREDS" | python3 -c "import sys,json;print(json.load(sys.stdin)['password'])")

PGPASSWORD="$RDS_PASS" psql \
  -h "$RDS_ENDPOINT" -U "$RDS_USER" -d bulk_loader \
  --set=ON_ERROR_STOP=on -f /tmp/bulk_loader.dump.sql
```

**Recommendation:** use the bastion-EC2 / Fargate-one-shot pattern, not the "publicly accessible" RDS toggle. The toggle requires modifying the security group plus a parameter group reboot, and operators routinely forget to revert it.

After the restore, populate `/{env}/bulk-loader/database-url` in Secrets Manager with the connection string the AWS task will use:

```bash
aws secretsmanager put-secret-value \
  --secret-id /${ENV}/bulk-loader/database-url \
  --secret-string "postgresql+asyncpg://${RDS_USER}:${RDS_PASS}@${RDS_ENDPOINT}:5432/bulk_loader?ssl=require"
```

The `?ssl=require` is mandatory — RDS is provisioned with `rds.force_ssl=1` and rejects plaintext connections.

### 3. Recover the encryption key

> ⚠️ **Do not skip this step.** All stored Salesforce connection passwords, JWT private keys, and any other Fernet-encrypted columns are unreadable without the **original** encryption key. If AWS Secrets Manager generates a fresh key on first task boot, the migrated DB will boot successfully but every Connection in the app will silently fail to decrypt.

On the self-hosted host, find the existing encryption key. The default Docker compose stack writes it to `data/db/encryption.key` on the host bind-mount (or as a docker secret if you're using compose secrets). It's a 32-byte url-safe base64 Fernet key, ~44 chars long.

Copy that exact value into Secrets Manager **before** the AWS ECS service starts:

```bash
ENCRYPTION_KEY=$(cat /path/to/data/db/encryption.key)
aws secretsmanager put-secret-value \
  --secret-id /${ENV}/bulk-loader/encryption-key \
  --secret-string "$ENCRYPTION_KEY"
```

If the AWS environment was already deployed before the migration and `lifespan()` ran with a fresh auto-generated key, the only safe recovery is to:

1. Stop the ECS service (`update-service --desired-count 0`).
2. Truncate `connections.private_key_encrypted` and any other Fernet-protected columns the app stores (search the code for `fernet.encrypt`).
3. Overwrite the secret with the original self-hosted key.
4. Re-run the database restore from step 2b.
5. Bring the service back up.

Easier to just get the key right the first time.

### 4. Migrate input files

Sync the contents of your local `INPUT_DIR` into the AWS input S3 bucket:

```bash
INPUT_BUCKET=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Data \
  --query "Stacks[0].Outputs[?OutputKey=='InputBucketName'].OutputValue" --output text)

aws s3 sync ./data/inputs/ "s3://${INPUT_BUCKET}/" --region "$DEPLOY_REGION"
```

Existing `LoadStep.csv_file_pattern` values may reference filesystem paths (e.g. `/data/inputs/accounts/*.csv`). Once on AWS, they need to resolve against the InputConnection's bucket + prefix.

After cutover, in the running app:

1. Create an InputConnection pointing at the input bucket (see [`aws.md` → "S3 input/output connections"](aws.md#s3-inputoutput-connections-iam-access-keys-required) for the IAM-key setup).
2. Edit each migrated LoadStep so its `csv_file_pattern` is the bucket-relative key/prefix it should read from.
3. The Files page will then list the synced objects exactly as the self-hosted Files page used to list local files.

A future enhancement ([SFBL-296](https://matthew-jenkin.atlassian.net/browse/SFBL-296)) will surface the active storage location on the Files page so this mapping is obvious; for now, refer to your input-bucket name from CloudFormation outputs.

### 5. Decide what to do with output files

The output bucket holds Bulk API result CSVs from prior runs. Most operators **don't migrate historical output** — those files are essentially logs of past runs, not data the application reads back in. New runs after cutover write to the AWS output bucket via the configured OutputConnection.

If you do want a copy for audit / reporting, sync it the same way:

```bash
OUTPUT_BUCKET=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Data \
  --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" --output text)

aws s3 sync ./data/outputs/ "s3://${OUTPUT_BUCKET}/" --region "$DEPLOY_REGION"
```

This is informational only — the app never reads these files back.

### 6. Swap the email backend to SES

`self_hosted` defaults to `email_backend=noop` (no email is sent). Some operators wire it up to SMTP. `aws_hosted` defaults to `email_backend=ses`, which the running task uses via the ECS task role — there are no per-user SMTP credentials to migrate.

The SES identity is provisioned by `BulkLoader-${ENV}-Data` and (if `sesIdentityAdoptExisting: false`) requires DKIM + MAIL FROM DNS records before SES will accept sends. See [`aws.md` → "Email (SES)"](aws.md#email-ses) and the SFBL-279 work for the full setup.

After cutover:

1. Confirm the SES identity is **Verified** in the AWS console (`aws ses get-identity-verification-attributes` or visual check).
2. Confirm `EMAIL_FROM_ADDRESS` is set in SSM (`/{env}/bulk-loader/email-from-address`) to a `noreply@<verified-domain>` style address.
3. Trigger a password-reset for a non-admin test user; the email should arrive within a few seconds.
4. Hit `/api/health/dependencies` and confirm the `email` line is healthy.

If you used SMTP previously and want to keep doing so on AWS, set `email_backend=smtp` in the running app's settings (and provision SMTP credentials in Secrets Manager) — but the default-to-SES path is cleaner because it eliminates one more piece of self-hosted state.

### 7. Rotate JWT secret + populate admin password

The JWT signing key controls active session validity. Two reasonable choices on cutover:

- **Carry over the existing JWT secret** (read from the self-hosted instance, write to `/{env}/bulk-loader/jwt-secret-key`). Existing sessions survive the cutover. Surprising to users only if their session also straddles the DNS swap.
- **Rotate to a fresh JWT secret** (`python3 -c 'import secrets; print(secrets.token_hex(32))'`). All existing sessions are invalidated; users must log in again on the new domain. **Recommended** because the AWS environment is the new home of the system, and a clean session boundary catches anyone who somehow pointed at the old instance during cutover.

For the bootstrap admin user, populate `/{env}/bulk-loader/admin-email` and `/{env}/bulk-loader/admin-password` per [`aws.md` step 5](aws.md#5-provision-secrets-before-first-ecs-start). Use the same email as your existing self-hosted admin if you want — `seed_admin()` is idempotent and won't touch a user record that already exists in the migrated DB. The admin password secret only matters on the **first** boot when the admin doesn't yet exist; after that, the migrated user records are authoritative.

### 8. Deploy and migrate the schema

> **Deploy with frontend DNS management OFF.** Set `manageFrontendDns: false`
> (CDK, in `cdk.context.json`) or `manage_frontend_dns = false` (Terraform) for
> this environment before deploying, so the deploy does **not** create or
> overwrite the live `domain_name` record. You'll flip DNS by hand in step 10
> once smoke passes, and can re-enable IaC management afterwards (step 10b).

If you haven't already deployed the AWS environment, do so now per [`aws.md` step 8](aws.md#8-deploy-backendstack--handle-the-first-deploy-migration-race). The `MigrationTaskDef` chicken-and-egg from a true clean deploy doesn't apply here because your DB is already populated from step 2b — but the migration task should still run once to ensure `alembic_version` matches the deployed image:

```bash
MIGRATION_ARN=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Backend \
  --query "Stacks[0].Outputs[?OutputKey=='MigrationTaskDefinitionArn'].OutputValue" --output text)
TASK_ARN=$(aws ecs run-task \
  --cluster bulk-loader-${ENV} \
  --task-definition "$MIGRATION_ARN" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG_ID],assignPublicIp=ENABLED}" \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster bulk-loader-${ENV} --tasks "$TASK_ARN"
```

Then `aws ecs update-service --force-new-deployment` to start the service against the migrated schema. Wait for steady state.

### 9. Smoke test before flipping DNS

Run the full smoke test from [`aws.md` step 12](aws.md#12-smoke-test) against the **CloudFront domain name** (not yet your custom domain). Then drive the UI through:

- Log in as a **migrated user** (not just the bootstrap admin) — confirms the encryption key and password hashes round-tripped correctly.
- Open a previously-existing Salesforce Connection and click **Test** — confirms the Fernet-encrypted private key decrypts.
- Open a Plan and a LoadRun from history — confirms Steps, Run records, and Job records are intact.
- Edit one LoadStep to point at the input S3 bucket and trigger a small test run end-to-end.

Anything failing here means do **not** flip DNS. Roll back per the plan below, fix, and try again.

### 10. Flip DNS

Once smoke passes, point the public hostname at the CloudFront distribution. The exact mechanism depends on where the zone lives:

```bash
DIST_DNS=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Frontend \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionDomainName'].OutputValue" --output text)
HOSTED_ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name "${HOSTED_ZONE}." \
  --query 'HostedZones[0].Id' --output text | sed 's|/hostedzone/||')

# A-ALIAS for the public domain → CloudFront
cat > /tmp/cf-alias.json <<EOF
{ "Changes": [ { "Action": "UPSERT", "ResourceRecordSet": {
  "Name": "${DOMAIN}.", "Type": "A",
  "AliasTarget": {
    "HostedZoneId": "Z2FDTNDATAQYW2",
    "DNSName": "${DIST_DNS}.",
    "EvaluateTargetHealth": false
  }
} } ] }
EOF
aws route53 change-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" --change-batch file:///tmp/cf-alias.json
```

The previous A record (pointing at your self-hosted ALB / EC2 / nginx) is replaced by the CloudFront alias. Existing in-flight requests that resolve to the old IP will hit the (still-running, but not accepting writes) self-hosted instance for up to the previous TTL — which is why step 1 stopped writes before any of this began.

### 10b. (Optional) Hand the alias back to IaC

Once cutover is validated, you can return to the default of IaC-managed frontend
DNS so future stack-ups/downs and rebuilds repoint the alias automatically:

- **Terraform:** set `manage_frontend_dns = true` (or remove the override) and
  `terraform apply`. `allow_overwrite = true` lets Terraform adopt the record you
  created by hand in step 10 and keep it pointed at CloudFront — no gap.
- **CDK:** CloudFormation can't adopt a record created outside the stack, so the
  alias you made in step 10 must be **deleted** and recreated by the stack. Set
  `manageFrontendDns: true`, then `cdk deploy BulkLoader-${ENV}-Frontend`. To
  minimise the gap, delete the manual record immediately before the deploy (or
  accept a brief DNS gap during the propagation window). Skip this entirely if
  you prefer to keep managing the public record by hand.

### 11. Tear down (or freeze) the self-hosted instance

Don't delete the self-hosted instance immediately. The recommendation is:

- **Keep it powered off but intact for at least 7 days.** Disk volumes, the original encryption-key file, and the SQLite/Postgres dump should all survive on the host. Disable it from any reverse-proxy / load balancer so no stray traffic reaches it.
- **Snapshot the self-hosted database** (or copy the SQLite file) before you finally tear the host down. This is your last-resort rollback.
- **Document the AWS RDS snapshot identifier** taken on cutover day in your operations log alongside the self-hosted snapshot location.

After the soak period passes without rollback, you can decommission the self-hosted host normally.

---

## Rollback plan

If something fails in step 9 or after DNS flip:

1. **Re-point DNS back** to the self-hosted instance. The `Z2FDTNDATAQYW2` alias becomes the previous A record (you should have captured it before step 10 — `aws route53 list-resource-record-sets` against the zone before changing it).
2. **Restart the self-hosted backend.** The DB and storage are intact from step 1's freeze, so the instance comes back exactly as it was.
3. **Investigate the AWS environment offline.** Don't leave the AWS stacks in a half-cutover state pointing at a dirtied DB — either complete the cutover later (after fixing the issue) or `cdk destroy --all` and start over once the root cause is understood.

The window where an in-flight write could be lost is between step 1 (self-hosted stop) and step 10 (DNS flip) — i.e. the maintenance window itself. Outside that window, both directions are safe.

---

## Validation status

This document was authored as part of [SFBL-280](https://matthew-jenkin.atlassian.net/browse/SFBL-280). Walk it through against a real self-hosted-to-AWS cutover before you trust step-by-step accuracy on a production instance — every environment differs in its self-hosted starting state, and the "normal" path here covers the most common shape (Docker compose, SQLite or Postgres, noop email).

---

## Related

- [`docker.md`](docker.md) — self-hosted Docker compose deployment.
- [`aws.md`](aws.md) — AWS-hosted deployment, including the first-deployment runbook this migration lands on top of.
- [`migrating-to-postgres.md`](migrating-to-postgres.md) — the SQLite → Postgres step referenced from step 2.
