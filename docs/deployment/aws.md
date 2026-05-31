# AWS-Hosted Deployment

## What this covers / who should read this

The operator guide for deploying the Bulk Loader to AWS with the `aws_hosted`
profile — CloudFront + ALB + ECS/Fargate + RDS PostgreSQL + S3. Read this if
you are provisioning a new environment, rotating a deployment's secrets, or
wiring up SSM/Secrets Manager. For the admin CLI used to recover locked-out
accounts see [`docs/usage/admin-recovery.md`](../usage/admin-recovery.md).

> **Status: validated end-to-end against `bulkloader.forcetide.net`
> on 2026-05-01 (SFBL-278).** All four stacks deploy cleanly; the
> first-deployment runbook below captures every gotcha hit during that
> validation.

---

## Profile

The AWS-hosted distribution uses the `aws_hosted` profile:

```
APP_DISTRIBUTION=aws_hosted
```

This enforces at startup:

| Setting | Value | Notes |
|---------|-------|-------|
| `auth_mode` | `local` | In-app authentication required |
| `transport_mode` | `https` | HTTPS mandatory; HTTP rejected at startup |
| `input_storage_mode` | `s3` | S3 required; local storage rejected at startup |
| `DATABASE_URL` | PostgreSQL only | SQLite is rejected at startup for this profile |

## Architecture

| Layer | Service |
|-------|---------|
| Frontend | S3 + CloudFront |
| Backend | ECS/Fargate behind ALB, reached via dedicated backend origin hostname |
| Database | Amazon RDS PostgreSQL 16 |
| Input/output file storage | Amazon S3 |
| TLS | Terminated at CloudFront (frontend) and ALB (backend origin) |
| Secrets | AWS Secrets Manager (sensitive) + SSM Parameter Store (non-sensitive) |
| Infrastructure | AWS CDK → CloudFormation |

```
Browser
  └─► CloudFront (frontend domain / CloudFront certificate)
        ├─► /api/* and /ws/* → backend origin hostname → ALB → Fargate container (port 8000)
        └─► /*               → S3 bucket (React SPA, index.html fallback)
```

TLS is terminated at CloudFront and the ALB. CloudFront connects to the backend using a dedicated
origin hostname that matches the ALB certificate, rather than the raw `*.elb.amazonaws.com` name.
The Fargate container listens on plain HTTP port 8000 internally. WebSocket connections use
`wss://` at the browser; the ALB proxies them as plain `ws://` to the container.

---

## Infrastructure as Code

Infrastructure is defined with **AWS CDK** (TypeScript) at `infrastructure/`. CDK synthesises
to CloudFormation, which manages all provisioning and updates. No manual console configuration
is required or acceptable for a reproducible deployment.

### Stacks

| Stack | Contents |
|-------|----------|
| `BulkLoader-{env}-Network` | VPC, public subnets (ALB + ECS) and isolated subnets (RDS) across 2 AZs, S3 Gateway Endpoint |
| `BulkLoader-{env}-Data` | ECR repository, RDS PostgreSQL (with `force_ssl=1` parameter group), S3 input + output buckets, Secrets Manager secrets, SES domain identity |
| `BulkLoader-{env}-Backend` | ECS cluster, Fargate task/service, ALB, backend Route53 alias, SES IAM policies |
| `BulkLoader-{env}-Frontend` | CloudFront distribution, S3 frontend bucket, automated `BucketDeployment` of the Vite build |

**ECR lives in `Data`, not `Backend`** — this is deliberate. The ECS service in
`Backend` cannot start until at least one image exists in ECR; the split lets
the operator deploy `Network + Data` first, push the image, then deploy
`Backend + Frontend`. See "First Deployment" below.

Environments (`staging`, `production`) are parameterised via CDK context — same code, different
values. Environment configuration lives in `infrastructure/cdk.json` under `context.environments`.

### Network Topology

The Network stack uses a no-NAT-Gateway design to minimise cost:

| Subnet type | Contains | Internet access |
|-------------|----------|-----------------|
| Public (× 2 AZs) | ALB, ECS Fargate tasks | Direct via Internet Gateway |
| Isolated (× 2 AZs) | RDS PostgreSQL | None — VPC-internal only |

**No NAT Gateway is provisioned.** Fargate tasks are placed in public subnets and assigned public
IPs so they can reach the Salesforce API directly. Inbound traffic to the containers is restricted
by the ECS security group to the ALB only — no direct public access to port 8000 is possible.
The attack-surface exposure is equivalent to a private-subnet deployment.

RDS remains in isolated subnets. The backend stack adds an explicit security-group rule allowing
the ECS service to reach PostgreSQL on the default port; no broader database exposure is opened.

**S3 Gateway Endpoint** is added to the VPC at no charge. All S3 traffic (input CSV reads and
result CSV writes) is routed over the AWS backbone rather than the public internet, eliminating
S3-related data-transfer charges.

Saving vs a standard NAT Gateway design: approximately **$32–45/month** per environment (NAT
Gateway hourly fee + per-GB data processing charge).

---

## Runtime Configuration

All application configuration is injected into the ECS task at launch. No config files are
mounted; no filesystem state is read for configuration. The application reads everything from
environment variables, which is compatible with the existing `config.py` model.

All injected env vars use the ECS task `secrets:` mapping — both Secrets Manager
and SSM Parameter Store values are resolved at task launch via
`ecs.Secret.fromSecretsManager(...)` / `ecs.Secret.fromSsmParameter(...)`. This
means a parameter edit + service rolling restart picks up the new value
without a `cdk deploy`. The synthesised template carries the parameter ARN
references, never the literal value.

### Secrets Manager (sensitive values)

| Secret name | App env var | Contents |
|-------------|-------------|----------|
| `/{env}/bulk-loader/encryption-key` | `ENCRYPTION_KEY` | Fernet key for stored Salesforce connection secrets |
| `/{env}/bulk-loader/jwt-secret-key` | `JWT_SECRET_KEY` | JWT signing secret for in-app bearer tokens |
| `/{env}/bulk-loader/database-url` | `DATABASE_URL` | Full PostgreSQL asyncpg connection string |
| `/{env}/bulk-loader/admin-email` | `ADMIN_EMAIL` | Bootstrap admin email / login identifier (used on first boot only) |
| `/{env}/bulk-loader/admin-password` | `ADMIN_PASSWORD` | Bootstrap admin password (used on first boot only) |
| `/{env}/bulk-loader/rds-credentials` | (internal) | RDS master credentials — managed by RDS, used to construct DATABASE_URL |

### SSM Parameter Store (non-sensitive runtime config)

| Parameter name | App env var | Example value |
|----------------|-------------|---------------|
| `/{env}/bulk-loader/cors-origins` | `CORS_ORIGINS` | `["https://bulk-loader.example.com"]` |
| `/{env}/bulk-loader/log-level` | `LOG_LEVEL` | `INFO` |
| `/{env}/bulk-loader/admin-username` | `ADMIN_USERNAME` | `admin` (display name for the bootstrap user) |
| `/{env}/bulk-loader/frontend-base-url` | `FRONTEND_BASE_URL` | `https://bulk-loader.example.com` — used to build absolute URLs in outbound email (invitations, password resets) |
| `/{env}/bulk-loader/email-from-address` | `EMAIL_FROM_ADDRESS` | `notifications@your-domain.example` — must be verified in SES (see "Email (SES)" section) |
| `/{env}/bulk-loader/email-ses-region` | `EMAIL_SES_REGION` | `eu-west-1` (defaults to deploy region if blank) |

Other runtime-tunable values — `SF_API_VERSION`, `DEFAULT_PARTITION_SIZE`,
JWT lifetime, login rate limits, password reset TTLs — are managed via the
`/settings/*` admin UI post-deploy and stored in the `app_settings` table.
They do not need to be injected as env vars.

### Hardcoded in task definition

| Env var | Value |
|---------|-------|
| `APP_DISTRIBUTION` | `aws_hosted` |

---

## Authentication

The `aws_hosted` profile uses the same in-app login model as `self_hosted`. Users authenticate
with their email address and password; the backend issues a signed JWT. The bootstrap admin
account (`ADMIN_EMAIL` / `ADMIN_PASSWORD`) is seeded on first boot and ignored on subsequent
starts. See [`docs/architecture/auth-and-rbac.md`](../architecture/auth-and-rbac.md) for the
identity, session, and invitation model.

**SSO / OIDC** is not supported in this release. It is an explicitly planned future enhancement
for hosted distributions.

---

## Prerequisites

- AWS CLI configured (`aws configure` or `AWS_PROFILE`)
- Node.js 20+ and npm (for CDK)
- AWS CDK CLI: `npm install -g aws-cdk`
- Docker (for building and pushing the backend image)
- ACM certificate in `us-east-1` for the frontend CloudFront distribution
- ACM certificate in the deployment region for the backend ALB listener
- A Route53 hosted zone matching `hostedZoneDomain`

---

## First Deployment

> **Read this whole section before starting.** First deploys against a clean
> account hit several non-obvious gotchas that aren't visible from a single
> `cdk deploy --all` invocation. The flow below is the one validated against
> `bulkloader.forcetide.net` during SFBL-278; in particular the
> *MigrationTaskDef chicken-and-egg* (step 8), the *frontend build flavour*
> (step 9), and the *Route53 alias for the frontend domain* (step 11) all
> require manual operator action that the CDK does not currently automate.

### Common first-deploy gotchas

- **Two ACM certs are required in two different regions.** CloudFront
  always reads its certificate from `us-east-1`, regardless of where the
  rest of your stack lives. The ALB certificate must be in the deployment
  region. If you forget the `us-east-1` cert, FrontendStack synth fails.
- **`cdk.json` placeholders win over `cdk.context.json`** because CDK
  deep-merges them with `cdk.json` taking priority for overlapping keys.
  Strip any `domainName` / `certificateArn` / `hostedZoneDomain`
  placeholders out of `cdk.json`'s environment blocks — `cdk.context.json`
  is the single source of truth for first-deploy values.
- **`npm run build:desktop` is not the AWS build.** It bakes
  `VITE_API_URL=http://127.0.0.1:8000` and a hash router into the bundle
  — fine for Electron, broken on AWS. Always use plain `npm run build`
  before deploying FrontendStack (step 9).
- **MigrationTaskDef is created by BackendStack but the service starts
  before migrations run.** `lifespan()` in `app/main.py` queries
  `profile_permissions` / calls `seed_admin` immediately on task start,
  which crashes against an empty schema. The service crashloops and
  BackendStack hangs. Workaround in step 8 below; longer-term fix
  ([SFBL-298](https://matthew-jenkin.atlassian.net/browse/SFBL-298)
  proposes relocating MigrationTaskDef to DataStack so it exists before
  the service ever starts).
- **CloudFront does not auto-create the Route53 alias for the frontend
  domain.** ALB origins for `backendDomainName` are aliased automatically;
  CloudFront aliases for `domainName` are not. Step 11 adds it manually.

### 1. Bootstrap CDK (once per account, **two regions**)

```bash
cdk bootstrap aws://ACCOUNT_ID/eu-west-1     # or your deploy region
cdk bootstrap aws://ACCOUNT_ID/us-east-1     # required for the CloudFront cert
```

### 2. Provision ACM certificates and validate via DNS

```bash
DOMAIN=bulkloader.example.com         # frontend (CloudFront) domain
API_DOMAIN=api.bulkloader.example.com # ALB origin used by CloudFront's /api/* and /ws/* behaviors
HOSTED_ZONE=example.com               # Route53 hosted zone that owns both names
DEPLOY_REGION=eu-west-1

# CloudFront cert MUST be in us-east-1
CERT_FRONTEND_ARN=$(aws acm request-certificate \
  --region us-east-1 \
  --domain-name "$DOMAIN" \
  --validation-method DNS \
  --query CertificateArn --output text)

# ALB cert in the deploy region
CERT_ALB_ARN=$(aws acm request-certificate \
  --region "$DEPLOY_REGION" \
  --domain-name "$API_DOMAIN" \
  --validation-method DNS \
  --query CertificateArn --output text)

# Read the DNS validation records ACM expects, then add them as CNAMEs
# in the hosted zone. Both certs must reach Status: ISSUED before
# CloudFormation will be willing to use them.
aws acm describe-certificate --region us-east-1   --certificate-arn "$CERT_FRONTEND_ARN" --query 'Certificate.DomainValidationOptions[].ResourceRecord'
aws acm describe-certificate --region "$DEPLOY_REGION" --certificate-arn "$CERT_ALB_ARN"      --query 'Certificate.DomainValidationOptions[].ResourceRecord'
```

Validation typically completes in a couple of minutes once the CNAMEs are
in place.

### 3. Configure environment values

```bash
cd infrastructure
cp cdk.context.json.example cdk.context.json
# Edit cdk.context.json — see fields below.
```

Confirm `cdk.json`'s `context.environments.<env>` block does **not** carry
overlapping `domainName` / `certificateArn` / `hostedZoneDomain` /
`backendDomainName` / `backendCertificateArn` placeholders for the env you
are deploying — those values must come from `cdk.context.json` so each
deployment can hold real values without committing them.

Required fields:

| Key | Purpose |
|-----|---------|
| `domainName` | Public frontend hostname served by CloudFront |
| `certificateArn` | CloudFront certificate ARN (must be in `us-east-1`) |
| `backendDomainName` | Backend origin hostname used by CloudFront (for example `api.example.com`) |
| `backendCertificateArn` | ALB certificate ARN in the deployment region |
| `hostedZoneDomain` | Route53 hosted zone that owns `backendDomainName` and `domainName` |
| `sesIdentityDomain` (optional) | SES sender domain — defaults to `hostedZoneDomain` |
| `sesIdentityAdoptExisting` (optional) | `true` if the SES identity is already verified outside this stack |

### 4. Deploy Network + Data only

```bash
npm install
npx cdk deploy BulkLoader-${ENV}-Network BulkLoader-${ENV}-Data -c env=${ENV}
```

This provisions the VPC, RDS, S3 buckets, Secrets Manager entries, and
the ECR repository. **Do not deploy `--all` yet** — Backend will fail
to reach steady state until you populate secrets (step 5) and push an
image (step 7), and Frontend will fail until `frontend/dist` is built
(step 9).

Capture the outputs you'll need:

```bash
ECR_URI=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Data \
  --query "Stacks[0].Outputs[?OutputKey=='EcrRepositoryUri'].OutputValue" --output text)
RDS_ENDPOINT=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Data \
  --query "Stacks[0].Outputs[?OutputKey=='RdsEndpoint'].OutputValue" --output text)
```

### 5. Provision secrets before first ECS start

The Secrets Manager secrets are created empty by CDK. Populate them before ECS attempts to start:

```bash
ENV=staging

# Generate and store the Fernet encryption key
ENCRYPTION_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
aws secretsmanager put-secret-value \
  --secret-id /${ENV}/bulk-loader/encryption-key \
  --secret-string "$ENCRYPTION_KEY"

# Generate and store the JWT signing secret
JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
aws secretsmanager put-secret-value \
  --secret-id /${ENV}/bulk-loader/jwt-secret-key \
  --secret-string "$JWT_SECRET"

# Construct DATABASE_URL from the RDS credentials secret (created by RDS)
# Get the RDS endpoint from CloudFormation output: BulkLoader-${ENV}-Data RdsEndpoint
RDS_ENDPOINT=<from-cfn-output>
RDS_CREDS=$(aws secretsmanager get-secret-value \
  --secret-id /${ENV}/bulk-loader/rds-credentials \
  --query SecretString --output text)
RDS_USER=$(echo $RDS_CREDS | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['username'])")
RDS_PASS=$(echo $RDS_CREDS | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['password'])")

aws secretsmanager put-secret-value \
  --secret-id /${ENV}/bulk-loader/database-url \
  --secret-string "postgresql+asyncpg://${RDS_USER}:${RDS_PASS}@${RDS_ENDPOINT}:5432/bulk_loader?ssl=require"

# Set admin bootstrap email + password
aws secretsmanager put-secret-value \
  --secret-id /${ENV}/bulk-loader/admin-email \
  --secret-string "admin@example.com"

aws secretsmanager put-secret-value \
  --secret-id /${ENV}/bulk-loader/admin-password \
  --secret-string "your-admin-password"
```

### 6. Provision SSM parameters

```bash
aws ssm put-parameter --name /${ENV}/bulk-loader/cors-origins \
  --value "[\"https://${DOMAIN}\"]" --type String

aws ssm put-parameter --name /${ENV}/bulk-loader/log-level \
  --value INFO --type String

aws ssm put-parameter --name /${ENV}/bulk-loader/frontend-base-url \
  --value "https://${DOMAIN}" --type String

aws ssm put-parameter --name /${ENV}/bulk-loader/admin-username \
  --value admin --type String

aws ssm put-parameter --name /${ENV}/bulk-loader/email-from-address \
  --value "no-reply@${HOSTED_ZONE}" --type String

aws ssm put-parameter --name /${ENV}/bulk-loader/email-ses-region \
  --value "${DEPLOY_REGION}" --type String
```

### 7. Build and push the backend image to ECR

```bash
aws ecr get-login-password --region "$DEPLOY_REGION" | \
  docker login --username AWS --password-stdin "$ECR_URI"

docker buildx build \
  --platform linux/amd64 \
  -t ${ECR_URI}:latest \
  -f backend/Dockerfile \
  backend/

docker push ${ECR_URI}:latest
```

### 8. Run the database migration, then deploy BackendStack

Since SFBL-298 the `MigrationTaskDefinition` and its own Fargate cluster live
in the **Data** stack, so they already exist after step 4. Run the migration
to bring the schema to head **before** deploying BackendStack — then the ECS
service boots against a populated schema and reaches steady state on the first
try. No background deploy, no polling, no manual `--force-new-deployment`.

```bash
# 1. Read the migration task, its cluster, the service security group, and the
#    public subnets - all from the already-deployed Network + Data stacks.
MIGRATION_ARN=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Data \
  --query "Stacks[0].Outputs[?OutputKey=='MigrationTaskDefinitionArn'].OutputValue" --output text)
MIGRATION_CLUSTER=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Data \
  --query "Stacks[0].Outputs[?OutputKey=='MigrationClusterName'].OutputValue" --output text)
SG_ID=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Data \
  --query "Stacks[0].Outputs[?OutputKey=='BackendServiceSecurityGroupId'].OutputValue" --output text)
VPC_ID=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Network \
  --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" --output text)
SUBNETS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:aws-cdk:subnet-type,Values=Public" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')

# 2. Run the migration task once and wait for it to exit cleanly.
TASK_ARN=$(aws ecs run-task \
  --cluster "$MIGRATION_CLUSTER" \
  --task-definition "$MIGRATION_ARN" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG_ID],assignPublicIp=ENABLED}" \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster "$MIGRATION_CLUSTER" --tasks "$TASK_ARN"

# 3. Confirm the migration exited 0 before continuing.
aws ecs describe-tasks --cluster "$MIGRATION_CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode'

# 4. Now deploy BackendStack - the service comes up against a populated schema.
npx cdk deploy BulkLoader-${ENV}-Backend -c env=${ENV} --require-approval never
```

Save the migration task ARN in case you need to re-run it later. The same
`run-task` invocation is used for every subsequent deploy (see "Ongoing
Deployments").

### 9. Build the frontend SPA — **plain `npm run build`, not `build:desktop`**

```bash
cd frontend
npm install
npm run build           # ← MUST be this, not `npm run build:desktop`
cd ..
```

`npm run build:desktop` bakes `VITE_API_URL=http://127.0.0.1:8000` and
the hash router into the bundle for Electron — both wrong on AWS.
The plain `build` script produces a relative-URL, browser-router build
that goes through CloudFront's `/api/*` and `/ws/*` behaviors to reach
the ALB.

Verify the bundle is clean before deploying:

```bash
grep -oE '127\.0\.0\.1:8000|localhost:8000' frontend/dist/assets/*.js && \
  echo "STOP — desktop build leaked into dist/" || echo "ok"
```

### 10. Deploy FrontendStack

```bash
npx cdk deploy BulkLoader-${ENV}-Frontend -c env=${ENV}
```

The `BucketDeployment` construct uploads `frontend/dist/` to S3 and
issues a CloudFront invalidation automatically. CloudFront distribution
provisioning typically takes 5–15 minutes on first creation.

### 11. Add the Route53 alias for the frontend domain

CDK does not auto-create the alias from `domainName` to the CloudFront
distribution (only the ALB origin alias for `backendDomainName` is
automated). Add it once:

```bash
DIST_DNS=$(aws cloudformation describe-stacks --stack-name BulkLoader-${ENV}-Frontend \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionDomainName'].OutputValue" --output text)
HOSTED_ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name "$HOSTED_ZONE." \
  --query 'HostedZones[0].Id' --output text | sed 's|/hostedzone/||')

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

aws route53 change-resource-record-sets \
  --hosted-zone-id "$HOSTED_ZONE_ID" \
  --change-batch file:///tmp/cf-alias.json
```

`Z2FDTNDATAQYW2` is the well-known CloudFront hosted-zone-ID constant —
it's not derived from anything in your account.

### 12. Smoke test

```bash
# Health behind CloudFront (exercises CloudFront → ALB → ECS → RDS)
curl -fsS "https://${DOMAIN}/api/health/ready"
# Expected: {"status":"ok"}

# Login as the bootstrap admin
ADMIN_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id /${ENV}/bulk-loader/admin-password --query SecretString --output text)
ADMIN_EMAIL=$(aws secretsmanager get-secret-value \
  --secret-id /${ENV}/bulk-loader/admin-email --query SecretString --output text)

TOKEN=$(curl -fsS -X POST "https://${DOMAIN}/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Confirm RBAC permissions hydrate correctly
curl -fsS -H "Authorization: Bearer $TOKEN" "https://${DOMAIN}/api/auth/me" | python3 -m json.tool
```

A successful response from `/api/auth/me` proves the full path works:
TLS termination at CloudFront, /api/* origin rewrite to the ALB, ECS
task → RDS query, JWT signing with the deployed secret, RBAC permission
matrix hydrated from migrated tables.

---

## Ongoing Deployments

### Backend update (new image)

Service tasks run with `RUN_MIGRATIONS=false` so that concurrent task
starts during a rolling deploy never race on `alembic upgrade head`.
Migrations run as a one-shot job between image push and service rollout:

1. Build and push the image to ECR:
   ```bash
   ECR_URI=$(aws cloudformation describe-stacks --stack-name BulkLoader-{env}-Data \
     --query "Stacks[0].Outputs[?OutputKey=='EcrRepositoryUri'].OutputValue" --output text)
   aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_URI
   docker buildx build --platform linux/amd64 -t $ECR_URI:latest -f backend/Dockerfile backend/
   docker push $ECR_URI:latest
   ```
2. Run the one-shot migration task and wait for it to complete. Since
   SFBL-298 the migration task, its cluster, and the service security-group
   output all live in the **Data** stack:
   ```bash
   MIGRATION_ARN=$(aws cloudformation describe-stacks --stack-name BulkLoader-{env}-Data \
     --query "Stacks[0].Outputs[?OutputKey=='MigrationTaskDefinitionArn'].OutputValue" --output text)
   MIGRATION_CLUSTER=$(aws cloudformation describe-stacks --stack-name BulkLoader-{env}-Data \
     --query "Stacks[0].Outputs[?OutputKey=='MigrationClusterName'].OutputValue" --output text)
   SG_ID=$(aws cloudformation describe-stacks --stack-name BulkLoader-{env}-Data \
     --query "Stacks[0].Outputs[?OutputKey=='BackendServiceSecurityGroupId'].OutputValue" --output text)
   SUBNETS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
     "Name=tag:aws-cdk:subnet-type,Values=Public" --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
   aws ecs run-task \
     --cluster $MIGRATION_CLUSTER \
     --task-definition $MIGRATION_ARN \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG_ID],assignPublicIp=ENABLED}"
   # Then poll until STOPPED with exit code 0:
   aws ecs wait tasks-stopped --cluster $MIGRATION_CLUSTER --tasks $TASK_ARN
   ```
   The migration task acquires a Postgres advisory lock for the duration of
   `alembic upgrade head` (see `backend/alembic/env.py`). Concurrent
   migration runs serialise on the lock; the second caller observes the
   schema is at head and exits in milliseconds.
3. Force a new service deployment so service tasks come up against the
   migrated schema:
   ```bash
   aws ecs update-service --cluster bulk-loader-{env} \
     --service BulkLoader-{env}-Backend-Service --force-new-deployment
   ```

For migrations that are forward-compatible with the previous code
(adding a nullable column, adding a new table) you can run step 2 first,
then step 3 — old code keeps running against the new schema during the
rollout. For migrations that aren't forward-compatible (NOT NULL flips,
column drops), schedule a maintenance window: scale the service to 0,
run the migration task, redeploy the service.

### Frontend update

The frontend build is uploaded automatically by the `BucketDeployment`
construct in `BulkLoader-{env}-Frontend`:

1. `cd frontend && npm install && npm run build`
2. `cdk deploy BulkLoader-{env}-Frontend -c env={env}` — picks up the
   new `frontend/dist/` contents, syncs to S3, and invalidates the
   CloudFront distribution.

The legacy three-step manual flow (`aws s3 sync` + `aws cloudfront
create-invalidation`) is no longer needed.

### Infrastructure change

1. Edit stacks in `infrastructure/lib/`
2. `cdk diff -c env={env}` to review changes
3. `cdk deploy --all -c env={env}` to apply

---

## Teardown

To tear a non-production environment down completely:

```bash
cdk destroy --all -c env={env}
```

This completes **without any manual intervention** — no `aws ecr
batch-delete-image`, no `aws ecs put-cluster-capacity-providers`, no
`aws s3 rb`. Two CDK properties make that work (SFBL-300):

- The backend **ECR repository** is created with `emptyOnDelete: true` on
  non-production tiers, so CloudFormation purges the pushed backend image
  before deleting the repository instead of failing with "repository …
  cannot be deleted because it still contains images".
- The **ECS capacity-provider association** is declared as an explicit
  construct that depends on the cluster, so CloudFormation detaches it
  before deleting the cluster instead of racing the detach and failing
  with "The specified capacity provider is in use and cannot be removed".

> **Data loss on a disposable tier.** On a tier with
> `persistOnDestroy: false` (bronze), `cdk destroy` deletes the RDS
> instance, the input/output S3 buckets, and regenerates the Secrets
> Manager entries on the next deploy. Any Salesforce connections, plans, or
> run history are lost. Use a `persistOnDestroy` tier (below) to keep data.

### Teardown with persistence (`persistOnDestroy` tiers)

Silver and gold set `persistOnDestroy: true` (SFBL-297). On these tiers
`cdk destroy` is non-destructive:

- **RDS** takes a final snapshot and deletes the instance (removal policy
  `SNAPSHOT`). The snapshot name looks like
  `bulkloaderproductiondatabase…-finalsnapshot-…`.
- The five app **secrets** (`encryption-key`, `jwt-secret-key`,
  `database-url`, `admin-email`, `admin-password`) are **retained** — the
  `encryption-key` especially, without which the Fernet-encrypted Salesforce
  credentials in a restored DB are unrecoverable.
- The input/output **S3 buckets** are retained.

```bash
cdk destroy --all -c env={env}
# RDS final snapshot + retained secrets + retained buckets remain.
# Parked cost is just snapshot + S3 storage (~$0.10/day), no running instance.
```

> **Deletion-protection note.** `persistOnDestroy` forces RDS deletion
> protection **off** (a snapshot-on-destroy can't run while it's on). The
> snapshot + retained secrets are the durability guard instead. If you want
> a truly undeletable production instance, set that tier to
> `persistOnDestroy: false` + `rdsDeletionProtection: true` (mutually
> exclusive — see DECISIONS 028).

### Restore from snapshot

Bring a parked `persistOnDestroy` environment back with the same data:

```bash
# 1. Find the most recent snapshot for this environment.
SNAP_ID=$(aws rds describe-db-snapshots \
  --query 'DBSnapshots[?starts_with(DBSnapshotIdentifier, `bulkloader{env}`)] | sort_by(@, &SnapshotCreateTime)[-1].DBSnapshotIdentifier' \
  --output text)

# 2. Deploy with the restore context. The DB is rebuilt from the snapshot and
#    the retained secrets are imported by name (not recreated).
cdk deploy --all -c env={env} -c restoreFromSnapshot=$SNAP_ID
```

Two things the operator must do after a restore:

1. **Keep passing `-c restoreFromSnapshot=$SNAP_ID` on every later deploy**
   of this environment. If you drop it, CloudFormation creates a fresh empty
   instance and deletes the restored one.
2. **Update `DATABASE_URL`.** The restored instance has a new endpoint and a
   freshly generated master password (in a new `rds-credentials` secret).
   Rewrite the retained `/{env}/bulk-loader/database-url` secret to the new
   `postgresql+asyncpg://…@<new-endpoint>:5432/bulk_loader?ssl=require` before
   running the migration task and deploying BackendStack.

---

## Database

The `aws_hosted` profile requires a PostgreSQL `DATABASE_URL`. Any standard
`postgresql+asyncpg://` connection string is accepted:

```
DATABASE_URL=postgresql+asyncpg://user:password@rds-endpoint:5432/bulk_loader?ssl=require
```

`?ssl=require` is mandatory — the RDS instance is configured with a custom
parameter group that sets `rds.force_ssl=1`, so the server rejects any
non-TLS connection. The `BulkLoader-{env}-Data` stack output
`RdsParameterGroupName` lets operators verify the parameter group is
actually attached post-deploy.

Storage is encrypted at rest (`storageEncrypted: true` set explicitly on
the `DatabaseInstance`).

The `aws_hosted` profile rejects SQLite at startup — `config.py` enforces
that `DATABASE_URL` starts with `postgresql+asyncpg://`.

Alembic migrations are gated by the `RUN_MIGRATIONS` env var
(default `true`):

```
CMD: if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then alembic upgrade head; fi
     && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- **Self-hosted Docker compose** leaves `RUN_MIGRATIONS` unset, so each
  container start applies pending migrations inline. Single container, no
  concurrency.
- **`aws_hosted` ECS service tasks** run with `RUN_MIGRATIONS=false`. They
  start uvicorn directly without touching Alembic — concurrent service
  tasks during a rolling deploy never race.
- **`aws_hosted` MigrationTaskDefinition** runs `alembic upgrade head` as
  a one-shot Fargate task before each service rollout. See
  "Ongoing Deployments" above for the deploy sequence.

The migration code path also acquires a Postgres advisory lock for the
duration of the upgrade (`backend/alembic/env.py`). This is
belt-and-braces — the canonical deploy flow only ever invokes a single
migration task at a time, but if anyone bypasses that flow (e.g. running
`alembic upgrade head` from a bastion during a deploy), the second
caller blocks on the lock until the first finishes, then observes the
schema is at head and exits cleanly.

---

## File Storage

The `aws_hosted` profile sets `input_storage_mode=s3`. Source CSV files are
read from S3 rather than the local filesystem; result files from the Bulk
API are written to S3.

### S3 input/output connections (IAM access keys required)

The application reads and writes S3 via per-Connection AWS access keys
stored encrypted in the database, **not** via the ECS task role's default
credential chain. This means the Data stack's input/output buckets are not
automatically accessible to the running application — operators must
configure an InputConnection (or OutputConnection) in the UI with explicit
credentials.

Setup steps after the buckets are provisioned:

1. **Create an IAM user** in the deploying account, e.g. `bulk-loader-{env}-s3`.
2. **Attach an inline policy** scoped to the input + output buckets:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["s3:ListBucket"],
         "Resource": [
           "arn:aws:s3:::<input-bucket>",
           "arn:aws:s3:::<output-bucket>"
         ]
       },
       {
         "Effect": "Allow",
         "Action": ["s3:GetObject"],
         "Resource": ["arn:aws:s3:::<input-bucket>/*"]
       },
       {
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
         "Resource": ["arn:aws:s3:::<output-bucket>/*"]
       }
     ]
   }
   ```
3. **Generate access keys** for the IAM user and copy them.
4. **In the running application**, navigate to *Connections → New Input
   Connection*. Choose the S3 provider, paste the access key ID + secret,
   set the bucket and region, and use the connection's "Test" button to
   verify read (and write, for output connections) access.

The bucket retention lifecycle rules are driven by the chosen tier (see
"Sizing and cost"). With `inputRetentionDays`/`outputRetentionDays` > 0,
S3 will expire objects after that many days; set to 0 to retain forever.

A code-path alternative — `InputConnection.use_task_role` mode that lets
boto3 resolve credentials from the ECS task role's default credential
chain, eliminating BYO keys for first-party buckets — is filed under
[SFBL-295](https://matthew-jenkin.atlassian.net/browse/SFBL-295) (production-scale hardening epic).

---

## Email (SES)

The `aws_hosted` profile defaults `email_backend=ses`. The application
sends transactional email (invitations, password resets, run completion
notifications) via SES v2 SendEmail using the ECS task role for IAM. The
CDK provisions:

- An `AWS::SES::EmailIdentity` for the configured domain
  (`hostedZoneDomain`, or override via `cdk.json` env's
  `sesIdentityDomain`).
- A MAIL FROM subdomain (`mail.<domain>`) so receiving providers don't
  show the "via amazonses.com" attribution.
- Two scoped IAM policies on the ECS task role:
  - `SesSendScopedToIdentity` — `ses:SendEmail` and `ses:SendRawEmail`,
    restricted to the SES identity ARN. Limits blast radius if the role
    is compromised.
  - `SesAccountReadForHealthProbe` — `ses:GetAccount` and
    `ses:GetSendQuota`, `Resource: "*"` (these are account-wide reads
    that don't accept a resource ARN). Used by the
    `/api/health/dependencies` SES probe.

### Identity verification — DNS records

DKIM verification requires three CNAME records added to your DNS
provider. CDK does not auto-write them (the synth step would otherwise
require a Route53 lookup against the deploying account, which fails in
CI / placeholder environments). After `cdk deploy BulkLoader-{env}-Data`,
read the DKIM records from the CloudFormation outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name BulkLoader-{env}-Data \
  --query "Stacks[0].Outputs[?starts_with(OutputKey, 'SesDkimRecord')].OutputValue" \
  --output text
```

Each line is `<token>._domainkey.<domain> CNAME <token>.dkim.amazonses.com`.
Add all three to the hosted zone for the domain. Verification typically
completes within minutes; the SES console shows "Successful" once propagated.

For the MAIL FROM domain (`mail.<domain>`), you also need:
- `MX` record: `mail.<domain> 10 feedback-smtp.<region>.amazonses.com`
- `TXT` record: `mail.<domain> "v=spf1 include:amazonses.com ~all"`

### Configuring the From address

After identity verification, set `EMAIL_FROM_ADDRESS` via SSM (see Runtime
Configuration table above). The address must use the verified domain or a
subdomain of it (e.g. `notifications@your-domain.example`).

### Testing

After SES is verified and `EMAIL_FROM_ADDRESS` is set:

1. Trigger a password-reset from the running app (use the bootstrap admin
   account).
2. Watch CloudWatch Logs for the `email_ses_*` events.
3. Confirm receipt at the test mailbox.
4. Hit `/api/health/dependencies` and verify the `email` line is healthy
   (uses `ses:GetSendQuota` — this is what the IAM policy
   `SesAccountReadForHealthProbe` enables).

If your account is in the SES sandbox, recipients must also be verified
in the SES console. Production access is requested via the SES console
(typically approved within 24h).

---

## Multi-Environment Pattern

The CDK stacks support `staging` and `production` environments out of the
box via CDK context. Environment-specific values (certificate ARNs, domain
names, hosted zone) live in `cdk.json` under `context.environments`. The
stack code is shared and parameterised — no duplication.

Sizing — RDS instance class, ECS replica count, log retention, S3
lifecycle — comes from a tier preset (Bronze / Silver / Gold) defined
under `cdk.json` `context.tiers`. Each environment selects one via the
`tier` field. See "Sizing and cost" above for what each tier provisions
and the monthly cost estimate.

To add a new environment:

1. Add a block under `context.environments` in `cdk.json` with a `tier`
   field (or override the tier shape per-env in `cdk.context.json`).
2. Provision the ACM certificate for that environment.
3. Run `cdk deploy --all -c env=<new-env>`.

---

## Sizing and cost

The CDK exposes three named **tier presets** under `context.tiers` in `cdk.json`. Each
environment selects a tier; the stack code reads tier values for instance classes,
task counts, log retention, and the optional production-scale layers
(arq/Redis worker tier, WAF, autoscaling). All prices below are **eu-west-1 (Ireland)
on-demand, USD/month**, post-Free-Tier. Adjust by ~−10% for `us-east-1`, ~+10% for
`ap-southeast-2`.

### Tier matrix

| Component | **Bronze** | **Silver** | **Gold** |
|---|---|---|---|
| Use case | 1-2 admins, demo/PoC | Small team (3-10 users), regular use | Customer-facing, audit/compliance |
| RDS instance | db.t4g.micro Single-AZ | db.t4g.small Single-AZ | db.t4g.medium **Multi-AZ** |
| RDS storage / backups | 20 GB / 1-day | 20 GB / 7-day | 100 GB / 30-day + cross-region snapshot |
| Fargate API tasks | 1× (0.5 vCPU / 1 GB) | 2× (0.5 vCPU / 1 GB) | 2× (1 vCPU / 2 GB) + autoscaling 2-6 |
| Fargate worker tier | none (orchestrator runs in API task) | none | 2× (1 vCPU / 2 GB) + queue-depth autoscaling |
| Redis (rate-limit + arq) | none — per-process limiter | none — per-process limiter | ElastiCache cache.t4g.small Multi-AZ |
| ALB | yes | yes | yes + access logs to S3 |
| CloudFront | yes | yes | yes + AWS WAF managed-rule baseline |
| Secrets Manager | 5 secrets | 5 secrets | 5 secrets + automated RDS credential rotation |
| CloudWatch Logs | 1-week retention | 1-month retention | 1-year retention + metric-filter alarms |
| ContainerInsights v2 | off | on | on |
| CloudWatch alarms | none | RDS storage + ECS task count (~3) | full suite (~12) + SNS topics |

### Monthly cost (eu-west-1)

| Line item | Bronze | Silver | Gold |
|---|---:|---:|---:|
| RDS instance + storage + backups | $15 | $29 | $132 |
| Fargate (API) | $20 | $40 | $80 |
| Fargate (worker tier) | — | — | $80 |
| ElastiCache Redis | — | — | $52 |
| ALB (+ access logs at Gold) | $25 | $25 | $28 |
| CloudFront (+ WAF at Gold) | $1 | $5 | $35 |
| S3 (input + output + frontend) | $2 | $5 | $15 |
| Secrets Manager | $2 | $2 | $2 |
| ECR | $1 | $1 | $2 |
| Route53 | $0.50 | $0.50 | $0.50 |
| CloudWatch Logs + ContainerInsights | $3 | $10 | $25 |
| CloudWatch alarms + SNS | — | $1 | $2 |
| SES | $0 | $0 | $1 |
| **Total** | **~$70/mo** | **~$120/mo** | **~$455/mo** |

### What you give up at each tier

**Bronze.** Single API task — task failure is a ~1-2 minute outage while ECS restarts.
Single-AZ RDS — AZ failure means restore from backup (~30-60 min RTO). No alarms — you
find out about problems when a user does. Caps at ~5 concurrent users before connection
or CPU limits hit. Inline Alembic migrations on container start work fine because there
is only one task.

**Silver.** Two API tasks gives task-level HA, but the DB is still Single-AZ — an AZ
outage still hurts. Per-process rate limiter is now broken across replicas (two
processes don't share state) but acceptable for an internal team where login flooding
is not a real threat. Migrations must use the one-shot migration task pattern (see
"Ongoing Deployments") because concurrent service-task starts would race.

**Gold.** Adds Multi-AZ RDS, Redis-backed shared state, the arq worker tier, WAF, and
full alarming. Most of the increment over Silver is operational insurance — outages
that would have been minutes at Silver become seconds at Gold; states that were
unobserved become observed.

### Caveats and variable costs

- **ALB hourly ($25) is the floor at all tiers** — required for HTTPS termination and
  WebSocket pass-through. Replacing with API Gateway HTTP API would save ~$24/month at
  idle but is a non-trivial backend rewrite (WSS via a separate WebSocket API).
- **Data egress to internet** — driven by output CSV downloads at $0.09/GB. A customer
  pulling 100 GB/month of result files adds ~$9/month.
- **Salesforce API egress** — free outbound from Fargate; bulk CSV bytes go S3 ↔ S3
  over the free gateway endpoint.
- **NAT Gateway** — zero (architecture deliberately uses public subnets).

### Lever guide

Knobs that materially shift cost without a tier change:

- **RDS Multi-AZ on/off** — doubles the RDS instance cost. Skip for Bronze/Silver
  unless you have a written uptime requirement.
- **Worker tier on/off (Gold only)** — saves ~$80/month if you can run the orchestrator
  in the API tasks. Acceptable when concurrent partition counts are low.
- **WAF on/off (Gold only)** — saves ~$25/month. Skip if not customer-facing.
- **CloudWatch log retention** — halve it to halve the storage line.

---

## Security Notes

- The Fargate container runs as a non-root user (inherited from `backend/Dockerfile`)
- All S3 buckets block public access; CloudFront accesses the frontend bucket via OAC
- Secrets Manager secrets are never exposed in CloudFormation templates or task definition plaintext
- RDS is in isolated subnets (no internet route), accessible only from within the VPC
- The database security group allows PostgreSQL traffic only from the ECS service
- ALB enforces TLS 1.2+ via `SslPolicy.RECOMMENDED_TLS`
- HTTP to HTTPS redirect is enforced at the ALB
- `enforceSSL: true` on all S3 buckets rejects unencrypted requests

---

## SSO / OIDC

Not supported in this release. In-app username/password authentication is used, same as
the self-hosted Docker distribution. SSO/OIDC integration is an explicitly planned future
enhancement for hosted distributions.
