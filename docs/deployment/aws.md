# AWS-Hosted Deployment

## What this covers / who should read this

The operator guide for deploying the Bulk Loader to AWS with the `aws_hosted`
profile — CloudFront + ALB + ECS/Fargate + RDS PostgreSQL + S3. Read this if
you are provisioning a new environment, rotating a deployment's secrets, or
wiring up SSM/Secrets Manager. For the admin CLI used to recover locked-out
accounts see [`docs/usage/admin-recovery.md`](../usage/admin-recovery.md).

> **Status: Skeleton implemented (Ticket 9)**
>
> The CDK infrastructure stacks exist at `infrastructure/` and the architecture is fully
> defined. A complete working deployment requires provisioning secrets, pushing a backend
> image to ECR, and running `cdk deploy`. The application code is ready for `aws_hosted`
> deployment — no backend or frontend changes are required.

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
| `BulkLoader-{env}-Data` | RDS PostgreSQL, S3 input + output buckets, Secrets Manager secrets |
| `BulkLoader-{env}-Backend` | ECR repository, ECS cluster, Fargate task/service, ALB, backend Route53 alias |
| `BulkLoader-{env}-Frontend` | CloudFront distribution, S3 frontend bucket |

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

### Secrets Manager (sensitive values)

Injected as ECS task secrets — values never appear in plaintext in the task definition.

| Secret name | App env var | Contents |
|-------------|-------------|----------|
| `/{env}/bulk-loader/encryption-key` | `ENCRYPTION_KEY` | Fernet key for stored Salesforce connection secrets |
| `/{env}/bulk-loader/jwt-secret-key` | `JWT_SECRET_KEY` | JWT signing secret for in-app bearer tokens |
| `/{env}/bulk-loader/database-url` | `DATABASE_URL` | Full PostgreSQL asyncpg connection string |
| `/{env}/bulk-loader/admin-email` | `ADMIN_EMAIL` | Bootstrap admin email / login identifier (used on first boot only) |
| `/{env}/bulk-loader/admin-password` | `ADMIN_PASSWORD` | Bootstrap admin password (used on first boot only) |
| `/{env}/bulk-loader/rds-credentials` | (internal) | RDS master credentials — managed by RDS, used to construct DATABASE_URL |

### SSM Parameter Store (non-sensitive values)

Resolved by ECS at task launch and injected as plain environment variables.

| Parameter name | App env var | Example value |
|----------------|-------------|---------------|
| `/{env}/bulk-loader/cors-origins` | `CORS_ORIGINS` | `["https://bulk-loader.example.com"]` |
| `/{env}/bulk-loader/log-level` | `LOG_LEVEL` | `INFO` |
| `/{env}/bulk-loader/frontend-base-url` | `FRONTEND_BASE_URL` | `https://bulk-loader.example.com` — used to build absolute URLs in outbound email (invitations, password resets) |

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

### 1. Bootstrap CDK (once per account/region)

```bash
cdk bootstrap aws://ACCOUNT_ID/REGION
```

### 2. Configure environment values

Copy the example context file and fill in real values:

```bash
cd infrastructure
cp cdk.context.json.example cdk.context.json
# Edit cdk.context.json with real certificate ARNs, domain names, and hosted zone values.
```

Or edit `infrastructure/cdk.json` directly for values safe to commit (no account IDs or real ARNs).

Environment config now includes:

| Key | Purpose |
|-----|---------|
| `domainName` | Public frontend hostname served by CloudFront |
| `certificateArn` | CloudFront certificate ARN (must be in `us-east-1`) |
| `backendDomainName` | Backend origin hostname used by CloudFront (for example `api.example.com`) |
| `backendCertificateArn` | ALB certificate ARN in the deployment region |
| `hostedZoneDomain` | Route53 hosted zone that owns `backendDomainName` |

### 3. Deploy infrastructure stacks

```bash
cd infrastructure
npm install
npx cdk deploy --all -c env=staging
```

This creates all four stacks in dependency order. Note the outputs — you'll need the ECR URI,
backend origin domain name, and bucket names from the CloudFormation outputs.

### 4. Provision secrets before first ECS start

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

### 5. Provision SSM parameters

```bash
ENV=staging
CLOUDFRONT_DOMAIN=<your-domain-or-cf-domain>

aws ssm put-parameter \
  --name /${ENV}/bulk-loader/cors-origins \
  --value "[\"https://${CLOUDFRONT_DOMAIN}\"]" \
  --type String

aws ssm put-parameter \
  --name /${ENV}/bulk-loader/log-level \
  --value INFO \
  --type String

aws ssm put-parameter \
  --name /${ENV}/bulk-loader/frontend-base-url \
  --value "https://${CLOUDFRONT_DOMAIN}" \
  --type String
```

### 6. Build and push the backend image to ECR

```bash
# Get ECR URI from CloudFormation output: BulkLoader-{env}-Backend EcrRepositoryUri
ECR_URI=<from-cfn-output>
AWS_REGION=<your-region>

aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_URI

docker buildx build \
  --platform linux/amd64 \
  -t ${ECR_URI}:latest \
  -f backend/Dockerfile \
  backend/

docker push ${ECR_URI}:latest
```

### 7. Force ECS service update

After pushing a new image, trigger a rolling deploy:

```bash
aws ecs update-service \
  --cluster bulk-loader-${ENV} \
  --service BulkLoader-${ENV}-Backend-Service \
  --force-new-deployment
```

Or redeploy the backend stack (CDK will trigger a new task revision):

```bash
npx cdk deploy BulkLoader-${ENV}-Backend -c env=${ENV}
```

### 8. Deploy the frontend

```bash
# Build the React SPA
cd frontend && npm run build && cd ..

# Get bucket name from CloudFormation output: BulkLoader-{env}-Frontend FrontendBucketName
BUCKET=<from-cfn-output>
DIST_ID=<from-cfn-output>   # DistributionId

aws s3 sync frontend/dist/ s3://${BUCKET} --delete
aws cloudfront create-invalidation --distribution-id ${DIST_ID} --paths '/*'
```

### 9. Smoke test

```bash
# Replace with your CloudFront domain or custom domain
DOMAIN=https://<distribution-domain-or-domainName>

curl -f ${DOMAIN}/api/health
# Expected: {"status":"ok"}

curl -f ${DOMAIN}/
# Expected: 200 with React HTML
```

---

## Ongoing Deployments

**Backend update** (new image):
1. Build and push image to ECR (`docker buildx build ... && docker push ...`)
2. `aws ecs update-service --force-new-deployment` or `cdk deploy BulkLoader-{env}-Backend`

**Frontend update**:
1. `cd frontend && npm run build`
2. `aws s3 sync dist/ s3://{bucket} --delete`
3. `aws cloudfront create-invalidation --distribution-id {id} --paths '/*'`

**Infrastructure change**:
1. Edit stacks in `infrastructure/lib/`
2. `cdk diff -c env={env}` to review changes
3. `cdk deploy --all -c env={env}` to apply

---

## Database

The `aws_hosted` profile requires a PostgreSQL `DATABASE_URL`. Any standard
`postgresql+asyncpg://` connection string is accepted:

```
DATABASE_URL=postgresql+asyncpg://user:password@rds-endpoint:5432/bulk_loader?ssl=require
```

Add `?ssl=require` for RDS instances with SSL enforcement (recommended).

Alembic migrations run automatically on container start before uvicorn:

```
CMD: alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

This means each ECS task start applies any pending migrations. In production with multiple
tasks, only one task should run migrations — configure `minHealthyPercent: 100` and deploy
one task at a time, or use a separate one-off migration task. **TODO: implement a migration
task pattern for production multi-task deployments.**

---

## File Storage

The `aws_hosted` profile sets `input_storage_mode=s3`. Source CSV files are read from S3
rather than the local filesystem. Configure an input connection in the application UI
(Connections page → New Input Connection → S3 provider) pointing at the input S3 bucket
provisioned by the Data stack.

Output/result files from the Bulk API are stored in the output S3 bucket. The ECS task
role grants read/write access to both buckets automatically.

---

## Multi-Environment Pattern

The CDK stacks support `staging` and `production` environments out of the box via CDK context.
Environment-specific values (instance sizes, desired task counts, certificate ARNs, domain names,
and hosted zone) live in `cdk.json` under `context.environments`. The stack code is shared and
parameterised — no duplication.

To add a new environment:

1. Add a block under `context.environments` in `cdk.json`
2. Provision the ACM certificate for that environment
3. Run `cdk deploy --all -c env=<new-env>`

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
