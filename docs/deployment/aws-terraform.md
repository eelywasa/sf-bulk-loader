# AWS deployment with Terraform / OpenTofu

A standalone Terraform flavour of the AWS-hosted deployment, for teams that
standardise on Terraform (or OpenTofu) rather than CDK. It provisions the
same architecture as [deployment/aws.md](aws.md) — the two flavours are
peers, share **no state**, and you pick one per environment. CDK remains the
first-party path; nothing here changes it.

The configuration lives in
[`infrastructure-terraform/`](../../infrastructure-terraform/) and works
identically with **Terraform >= 1.10** and **OpenTofu >= 1.10** — every
`terraform` command below runs verbatim as `tofu`.

## What gets provisioned

The same four layers as the CDK app (see the parity checklist at the bottom):

1. **Network** — VPC across 2 AZs: public subnets (ALB + Fargate tasks with
   public IPs — no NAT Gateway) and isolated subnets (RDS only), S3 gateway
   endpoint, security groups, tier-gated VPC flow logs.
2. **Data** — ECR repository, RDS PostgreSQL 16 (TLS-enforced, encrypted
   gp3), input/output/access-log S3 buckets, Secrets Manager secrets, SSM
   config parameters, SES identity, and the one-shot Alembic migration task.
3. **Backend** — ECS Fargate service behind an ALB (HTTPS + redirect),
   Route53 alias, deployment circuit breaker, gold-tier Fargate Spot hybrid.
4. **Frontend** — S3 + CloudFront (OAC) serving the React SPA, with
   `/api/*` and `/ws/*` proxied to the backend origin uncached.

Internal project infrastructure (the test-evidence/Allure stack) is CDK-only
and deliberately not reproduced.

## Prerequisites

Create these **before** the first apply:

1. **Route53 public hosted zone** for `hosted_zone_domain` — the backend DNS
   record lands in it.
2. **ACM certificate in `us-east-1`** covering `domain_name` — CloudFront
   accepts only us-east-1 certificates, regardless of deploy region. (The
   variable contract rejects any other region at plan time.)
3. **ACM certificate in the deploy region** covering `backend_domain_name` —
   terminates TLS on the ALB.
4. **S3 bucket for Terraform state** (versioning + encryption recommended).
   No DynamoDB lock table — native S3 locking (`use_lockfile`) is used.
5. **AWS CLI + Docker** locally, for the image mirror step.

## First deploy — the ordered sequence

The first deploy is **staged**: the ECS service can only start once the
backend image exists in ECR and the database schema is migrated. Order
matters; this mirrors the CDK first-deploy runbook.

### 1. Configure

```bash
cd infrastructure-terraform
cp backend.hcl.example backend.hcl                      # state bucket/key/region
cp environment.tfvars.example production.tfvars          # domains, certs, region
terraform init -backend-config=backend.hcl
```

### 2. Apply the network + data layers

```bash
terraform apply \
  -target=module.network -target=module.data \
  -var-file=tiers/silver.tfvars -var-file=production.tfvars
```

(`-target` is used deliberately here: the backend service must not start
before steps 3–5. The final full apply in step 6 reconciles everything.)

### 3. Mirror the published image into ECR

Fargate pulls from the ECR repository this stack creates — never from GHCR
directly (locked-down VPCs often have no ghcr.io egress, and the deploy must
not depend on GHCR availability):

```bash
ECR_URL=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${ECR_URL%%/*}"
docker buildx imagetools create -t "${ECR_URL}:stable" \
  ghcr.io/eelywasa/sf-bulk-loader-backend:0.15.18
```

The tag you push must match `image_tag` in your tfvars.

> **Why `imagetools create` and not pull/tag/push:** the published image is
> multi-arch and Fargate runs **x86_64** by default. A plain `docker pull` on
> an Apple-Silicon machine resolves to the arm64 variant, and pushing that
> single-arch image leaves the task crashing with `exec format error`.
> `imagetools create` copies the full multi-arch manifest registry-to-registry
> (no local pull), so the right variant is always available. (Found by the
> first real apply smoke.)

### 4. Populate the secrets

`DATABASE_URL` is composed automatically from the RDS endpoint + generated
credentials. The remaining four are created empty and must be populated
before any task starts:

```bash
ENV=production
aws secretsmanager put-secret-value --secret-id /$ENV/bulk-loader/encryption-key \
  --secret-string "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
aws secretsmanager put-secret-value --secret-id /$ENV/bulk-loader/jwt-secret-key \
  --secret-string "$(openssl rand -base64 48)"
aws secretsmanager put-secret-value --secret-id /$ENV/bulk-loader/admin-email \
  --secret-string "admin@example.com"
aws secretsmanager put-secret-value --secret-id /$ENV/bulk-loader/admin-password \
  --secret-string "<bootstrap admin password>"
```

The five SSM config parameters (`cors-origins`, `log-level`,
`admin-username`, `email-from-address`, `email-ses-region`) are created with
working defaults; edit them in the console/CLI at any time — Terraform never
reverts your edits.

### 5. Run the database migration

```bash
terraform output -raw migration_run_command   # prints a ready-made aws ecs run-task line
```

Run the printed command, then confirm `alembic upgrade head` completed in the
`/bulk-loader/<env>/migration` log group before continuing. The task runs on
its own dedicated cluster, so it works before the backend exists.

### 6. Apply everything

```bash
terraform plan  -var-file=tiers/silver.tfvars -var-file=production.tfvars -out=plan.out
terraform apply plan.out
```

### 7. Upload the frontend

```bash
terraform output -raw frontend_deploy_command   # build + s3 sync --delete + invalidation
```

The `domain_name` → CloudFront Route53 A-alias is created by `terraform apply`
itself (see [SFBL-390](https://matthew-jenkin.atlassian.net/browse/SFBL-390)):
the `frontend` module manages it under `hosted_zone_domain`, gated by
`manage_frontend_dns` (default true), and re-points it automatically whenever
the distribution domain changes. **No manual DNS record creation is needed.**
On environments upgraded from the old manual runbook, `allow_overwrite = true`
lets the first `apply` adopt the pre-existing manual alias rather than erroring.

Set `manage_frontend_dns = false` when this account should not own the
`domain_name` record during apply: an external-DNS deployment whose
`domain_name` is not in this account's Route53, **or** a staged same-account
migration/cutover where the live record must only be flipped after smoke-testing
(with the flag on, `allow_overwrite` would clobber the live record on the first
apply — see [migrating-to-aws-hosted.md](migrating-to-aws-hosted.md)). When
`false`, Terraform emits no Route53 record and you point your own DNS at the
`cloudfront_distribution_domain` output (CNAME, or an alias A record).

### 8. Verify SES (first deploy only)

`terraform output ses_dkim_tokens` lists DKIM tokens. For each token `T`,
add a CNAME `T._domainkey.<domain> -> T.dkim.amazonses.com`. Email sending
stays sandboxed until the identity verifies (and the account leaves the SES
sandbox — see [email.md](../email.md)). If the domain identity was already
verified in this account, set `ses_identity_adopt_existing = true` instead.

### Smoke test

- `https://<domain_name>` loads the login page.
- `https://<domain_name>/api/health/ready` returns 200.
- A trivial load plan executes end-to-end (WebSocket status updates flow —
  proves the `/ws/*` CloudFront behaviour).

## Ongoing deployments

```bash
# New backend image: mirror the new tag (step 3), run the migration task
# (step 5), then force a rolling restart:
aws ecs update-service --cluster bulk-loader-<env> \
  --service bulk-loader-<env>-backend --force-new-deployment

# Frontend: rerun the frontend_deploy_command output.
# Sizing change: switch tier tfvars and apply.
```

A bad image rollout rolls back automatically (deployment circuit breaker).

## Validation without an AWS account

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate -no-tests   # see note below
terraform test                 # 19 mocked native tests - no credentials, no cost
```

> `-no-tests` sidesteps a Terraform validate quirk: its test-file pass
> mis-handles the aliased-provider mapping in the frontend test's run blocks
> and reports a spurious "Provider configuration not present" error. The test
> files are fully exercised by `terraform test`, and plain `tofu validate`
> handles them correctly.

The test suite (`tests/*.tftest.hcl`) encodes the falsifiable acceptance
criteria from the epic: no plaintext path to the DB (`rds.force_ssl`), DB
reachable only from the backend SG, IAM reads on the execution role (never
the task role), gold-tier Fargate Spot hybrid, `/ws/*` uncached, OAC scoped
to the distribution's SourceArn, cert-region validation, and more. It passes
identically under `terraform test` and `tofu test`.

Three structural grep gates complement the suite (assertions cannot reference
absent resource types):

```bash
grep -rn "aws_nat_gateway" infrastructure-terraform/modules/ && echo FAIL || echo OK   # no-NAT cost guarantee
grep -n '"s3:\*"' infrastructure-terraform/modules/backend/*.tf && echo FAIL || echo OK # no wildcard S3 action on the task role
git ls-files infrastructure-terraform | grep -v '\.example$' | grep -v tests/ \
  | xargs grep -lE ':[0-9]{12}:' && echo FAIL || echo OK                                # no real account ids committed
```

> **SFBL-388:** the old `grep '"s3:'` gate asserted the task role carried *no*
> S3 grants. The task role now holds a **scoped** first-party-bucket grant
> (object RW + `ListBucket` on exactly the two bucket ARNs), so that gate is
> replaced by the precise positive + no-wildcard assertions in
> `tests/backend.tftest.hcl` / `tests/data.tftest.hcl`. The grep gate above now
> only guards against a wildcard `s3:*` *action* slipping in.

## Parity checklist (vs the CDK stacks)

Every resource the CDK provisions in the four product stacks, and where it
lands here:

| CDK (infrastructure/lib) | Terraform (infrastructure-terraform/modules) |
|---|---|
| VPC, 2 AZ, public + isolated subnets, no NAT | `network` — `aws_vpc`, 4 × `aws_subnet`, IGW, route tables |
| S3 gateway endpoint | `network` — `aws_vpc_endpoint` (both route tables) |
| ALB SG (443/80), backend SG (8000 from ALB) | `network` — standalone SG rule resources |
| VPC flow logs (tier-gated) | `network` — `aws_flow_log` + log group + delivery role, `count`-gated |
| ECR repo + keep-10 lifecycle | `data` — `aws_ecr_repository` + lifecycle policy |
| RDS PG16, force_ssl param group, encrypted gp3, isolated subnet group, DB SG | `data` — `aws_db_instance` + parameter/subnet groups + SG |
| Generated RDS credentials secret | `data` — `random_password` + `/<env>/bulk-loader/rds-credentials` |
| 5 app secrets (empty placeholders) | `data` — 4 empty + auto-composed `database-url` (deviation 2) |
| 5 SSM parameters (imported by CDK) | `data` — created with defaults + `ignore_changes` (deviation 3) |
| Input/output/access-log buckets, logging, lifecycle | `data` — 3 buckets with public-access block, HTTPS-only policy, logging |
| SES identity (create + DKIM + MAIL FROM, or adopt existing) | `data` — both branches, DKIM tokens output |
| Migration task + dedicated cluster + log group | `data` — task definition, cluster, scoped execution role |
| ECS cluster + FARGATE/FARGATE_SPOT capacity providers | `backend` — `aws_ecs_cluster_capacity_providers`, service depends on it |
| Fargate service (circuit breaker, 50/200, public IPs, gold Spot hybrid) | `backend` — `aws_ecs_service` |
| Task definition (10 injections, APP_DISTRIBUTION/RUN_MIGRATIONS, S3_INPUT/OUTPUT_BUCKET + S3_BUCKET_REGION, /live health) | `backend` — `aws_ecs_task_definition` |
| Execution role (scoped secret/SSM reads) + task role (SES + scoped first-party S3 RW/List, no wildcard — SFBL-388) | `backend` — `iam.tf` |
| ALB, HTTPS listener (TLS13 policy), HTTP→301, target group (/ready) | `backend` — `alb.tf` |
| Route53 alias for the backend domain | `backend` — `aws_route53_record` |
| Route53 alias for the frontend domain (gated by `manage_frontend_dns`, default true — SFBL-390; in root for a static `depends_on = [module.backend]`) | root `main.tf` — `aws_route53_record.frontend` |
| Frontend bucket + OAC + CloudFront (SPA, /api/*, /ws/*) | `frontend` — `main.tf` |
| 403/404 → index.html, us-east-1 cert | `frontend` — distribution config |
| BucketDeployment (build upload + invalidation, pruned) | operator step — `frontend_deploy_command` output (deviation 6) |

**Justified omissions:** `persistOnDestroy` / snapshot-restore machinery
(first-party staging ops tooling; customer deployments are create-fresh);
the test-evidence stack (internal CI); CDK CfnOutputs that exist purely for
cross-stack wiring (Terraform modules pass values directly).

**First-party S3 default storage parity (SFBL-385/388 — QA #9).** The two
flavours must end behaviourally identical on the storage surface. This is a
*positive* cross-flavour check, not just two suites passing in isolation:

| Contract | CDK (`infrastructure/lib`) | Terraform (`infrastructure-terraform/modules`) |
|---|---|---|
| Env var names | `S3_INPUT_BUCKET` / `S3_OUTPUT_BUCKET` / `S3_BUCKET_REGION` | identical |
| Injected on | backend service **and** migration task defs | identical |
| Task-role S3 actions | `s3:GetObject/PutObject/DeleteObject/AbortMultipartUpload` (objects) + `s3:ListBucket` (bucket) | identical |
| Resource shape | `<inputArn>/*`, `<outputArn>/*` (objects); `<inputArn>`, `<outputArn>` (list) — no wildcard | identical |
| Granted to | service **task** role + migration **task** role (never the execution roles) | identical |

Verified by `test/sfbl-387-default-s3.test.ts` (CDK synth) and the
`iam_direction` / `migration_task_contract` runs in
`tests/{backend,data}.tftest.hcl` (Terraform/OpenTofu plan). When changing the
storage grant in one flavour, update the other and re-confirm this table.

## Parity-deviation register

Conscious differences from the CDK flavour, each with its rationale:

| # | Deviation | Rationale |
|---|---|---|
| 1 | State locking uses native S3 `use_lockfile`, no DynamoDB table | One less prerequisite; supported by both runtimes at the 1.10 floor |
| 2 | `DATABASE_URL` auto-composed from RDS endpoint + generated credentials (CDK leaves it for the operator) | Removes the most error-prone manual step on a fresh deploy; `ignore_changes` hands ownership to the operator after first apply |
| 3 | SSM parameters created with working defaults (CDK imports operator-created ones) | No pre-apply `put-parameter` dance on a fresh account; `ignore_changes` means operator edits are never reverted |
| 4 | Bucket names include the account id (`bulk-loader-<env>-<account>-input`) | CDK's deterministic names collide across customer accounts in S3's global namespace |
| 5 | silver/gold tiers keep `rds_deletion_protection = true` (CDK: false), and the final snapshot is a **separate** variable (`rds_skip_final_snapshot`, false on silver/gold) | The CDK turns protection off only because its snapshot-on-destroy persist machinery guards durability; that machinery is not ported, so protection is the remaining guard. Snapshot behaviour is decoupled so that tearing down a protected tier (which requires flipping protection off first) still takes the final snapshot |
| 6 | Frontend upload is a documented operator command, not IaC (CDK: BucketDeployment) | Terraform has no asset-bundling equivalent; `aws s3 sync --delete` + invalidation preserves the prune semantics |
| 7 | CloudFront managed policies referenced by their global id constants (CDK: enum) | The ids are AWS-published, identical in every account/region; direct references make behaviour wiring assertable in the native test suite |
| 8 | Secret recovery window derives from the data-protection flag (30 d protected / 0 d disposable) | Mirrors the CDK RETAIN/DESTROY split in spirit; immediate deletion keeps bronze destroy/recreate cycles clean |
| 9 | `.terraform.lock.hcl` is git-ignored | Terraform and OpenTofu record providers against different registries; a committed lock file breaks the dual-CLI guarantee — commit your own once you've picked a runtime |

## Cost and sizing

Tier presets mirror the CDK matrix — see “Sizing and cost” in
[deployment/aws.md](aws.md). Headlines: no NAT Gateway (~$32/month saved),
bronze runs a single 0.5 vCPU task on `db.t4g.micro`, gold adds Multi-AZ and
the Fargate Spot hybrid.
