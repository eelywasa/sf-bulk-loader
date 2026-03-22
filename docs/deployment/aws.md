# AWS-Hosted Deployment

> **Status: Planned (Ticket 9)**
>
> The AWS-hosted distribution is not yet implemented. This document describes the
> intended architecture and configuration model. See
> [docs/specs/distrubution-layer-spec.md](../specs/distrubution-layer-spec.md)
> for the full design.

---

## Profile

The AWS-hosted distribution uses the `aws_hosted` profile:

```
APP_DISTRIBUTION=aws_hosted
```

This enforces:

| Setting | Value | Notes |
|---------|-------|-------|
| `auth_mode` | `local` | In-app authentication required |
| `transport_mode` | `https` | HTTPS mandatory; HTTP rejected at startup |
| `input_storage_mode` | `s3` | S3 default; local storage rejected at startup |
| `DATABASE_URL` | PostgreSQL only | SQLite is rejected at startup for this profile |

## Intended Architecture

| Layer | Target |
|-------|--------|
| Frontend | Static hosting (S3 + CloudFront, or equivalent) |
| Backend | ECS/Fargate containerised service |
| Database | Amazon RDS (PostgreSQL) |
| File storage | Amazon S3 |
| TLS | Terminated at load balancer / CloudFront |
| Secrets | AWS Secrets Manager or Parameter Store (compatible with app DB model) |

## Authentication

The `aws_hosted` profile uses the same in-app login model as `self_hosted`: users
authenticate with a username and password, and the backend issues a signed JWT.

Bootstrap admin credentials (`ADMIN_USERNAME`, `ADMIN_PASSWORD`) are required on first
boot and ignored thereafter — same as self-hosted.

**SSO / OIDC** is not supported in this release. It is an explicitly planned future
direction for hosted distributions, but is out of scope for the initial AWS implementation.

## Transport and TLS

The `aws_hosted` profile requires `transport_mode=https`. HTTPS is enforced at the
**load balancer or CloudFront layer** — the backend itself listens on plain HTTP internally.
The backend logs a reminder of this at startup.

WebSocket connections use `wss://` at the client-facing layer (load balancer terminates
TLS). The backend receives plain `ws://` connections internally and proxies them as normal.
No WebSocket-specific TLS configuration is needed in the backend or nginx.

---

## Database

The `aws_hosted` profile requires a PostgreSQL `DATABASE_URL`. Any standard
`postgresql+asyncpg://` connection string is accepted, including RDS endpoints:

```
DATABASE_URL=postgresql+asyncpg://user:password@your-rds-endpoint:5432/bulk_loader?ssl=require
```

Add `?ssl=require` for RDS instances with SSL enforcement.

## File Storage

The `aws_hosted` profile sets `input_storage_mode=s3`. Source CSV files are read from
S3 rather than the local filesystem. Configuration of the S3 bucket and credentials
is a planned implementation concern (Ticket 9).

## Secrets and Configuration

Application secrets (`ENCRYPTION_KEY`, `JWT_SECRET_KEY`) should be injected via
AWS Secrets Manager or SSM Parameter Store into the ECS task definition as environment
variables. The application model is compatible with this pattern — no code changes
are required.

Bootstrap admin credentials (`ADMIN_USERNAME`, `ADMIN_PASSWORD`) follow the same
pattern as self-hosted: required on first boot, ignored thereafter.
