# Terraform deployment flavour

Standalone Terraform/OpenTofu configuration for deploying the Salesforce Bulk
Loader into your own AWS account. It provisions the same architecture as the
first-party CDK deployment under [`infrastructure/`](../infrastructure/) —
the two flavours are peers, share no state, and you pick one.

The full operator guide (apply ordering, image mirroring, secret population,
smoke tests) lives at [`docs/deployment/aws-terraform.md`](../docs/deployment/aws-terraform.md).
This README covers layout, prerequisites, and the quick start.

## Runtime

Works identically with **Terraform >= 1.10** and **OpenTofu >= 1.10** — every
`terraform` command below can be run as `tofu` verbatim. The configuration
uses only standard HCL and the AWS provider; no HCP Terraform / Terraform
Cloud features.

> **Provider lock file:** `.terraform.lock.hcl` is deliberately git-ignored
> here because Terraform and OpenTofu record providers against different
> registries and one CLI's lock file conflicts under the other. Once you have
> chosen a runtime, generate the lock file with `init` and commit it in your
> own fork or pipeline.

## Prerequisites (create these BEFORE the first apply)

1. **Route53 public hosted zone** for `hosted_zone_domain` — the backend DNS
   record is created in it.
2. **ACM certificate in `us-east-1`** covering `domain_name` — CloudFront
   only accepts us-east-1 certificates, regardless of deploy region.
3. **ACM certificate in your deploy region** covering `backend_domain_name`
   — terminates TLS on the ALB.
4. **S3 bucket for Terraform state** (versioning + encryption recommended).
   No DynamoDB lock table is needed — native S3 locking (`use_lockfile`) is
   used instead.

## Layout

```
infrastructure-terraform/
├── versions.tf                  # runtime floor + AWS provider pin
├── providers.tf                 # deploy-region provider + us-east-1 alias (CloudFront)
├── backend.tf                   # partial S3 backend; supply backend.hcl
├── backend.hcl.example          # state bucket/key/region template
├── variables.tf                 # the full input contract
├── main.tf                      # composes the four modules
├── outputs.tf
├── tiers/{bronze,silver,gold}.tfvars   # committed sizing presets
├── environment.tfvars.example   # per-env values you copy + fill in
└── modules/{network,data,backend,frontend}/
```

## Quick start

```bash
cd infrastructure-terraform

# 1. State backend
cp backend.hcl.example backend.hcl   # fill in your state bucket
terraform init -backend-config=backend.hcl

# 2. Environment values
cp environment.tfvars.example production.tfvars   # fill in domains/certs/region

# 3. Plan with a tier preset + your environment file
terraform plan \
  -var-file=tiers/silver.tfvars \
  -var-file=production.tfvars \
  -out=plan.out

# 4. Review, then apply the reviewed plan artifact
terraform apply plan.out
```

The first deploy is **staged** — the backend image must be mirrored into ECR
and the database migrated before the ECS service can start. Follow
`docs/deployment/aws-terraform.md` for the full ordered sequence.

## Tiers

| | bronze | silver | gold |
|---|---|---|---|
| RDS | db.t4g.micro, 20 GiB | db.t4g.small, 20 GiB | db.t4g.medium, 100 GiB, Multi-AZ |
| Backend tasks | 1 × 0.5 vCPU/1 GiB | 2 × 0.5 vCPU/1 GiB | 2 × 1 vCPU/2 GiB (+ Fargate Spot) |
| Log retention | 7 d | 30 d | 365 d |
| Container insights + flow logs | off | on | on |
| RDS deletion protection | off | **on** | **on** |

Sizing matches the CDK tier presets (`infrastructure/cdk.json`), with one
deviation: silver/gold keep RDS deletion protection **on**, because the CDK's
snapshot-on-destroy persistence machinery is not part of this flavour.

## Validation (no AWS account needed)

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate -no-tests
terraform test
```

Same commands work under `tofu` (where plain `tofu validate` is fine).
`-no-tests` works around a Terraform validate quirk: its test-file pass
mis-handles the aliased-provider mapping the frontend test uses and reports a
spurious "Provider configuration not present" error. The test files are fully
exercised by `terraform test` itself, and OpenTofu validates them clean.
