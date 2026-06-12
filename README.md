# Salesforce Bulk Loader

A containerized web application for orchestrating large-scale data loads into
Salesforce using the **Bulk API 2.0**. Define multi-object load plans, track
job progress in real time, and capture success/error logs — all through a
browser-based UI.

---

## Quick Start

```bash
git clone https://github.com/eelywasa/sf-bulk-loader.git
cd sf-bulk-loader
cp .env.example .env          # set ADMIN_EMAIL and ADMIN_PASSWORD
mkdir -p data/input data/output data/db
docker compose up --build
```

Open **http://localhost**.

For HTTPS, PostgreSQL, desktop, or AWS deployments see the
[deployment guides](docs/deployment/docker.md).

---

## Documentation

The full handbook lives under [`docs/`](docs/README.md), organised into three
pillars:

- **Architecture & design** — [`docs/architecture.md`](docs/architecture.md)
  is the entry point. System overview, auth/RBAC model, run execution,
  storage.
- **Operations & developer** — deployment guides
  ([docker](docs/deployment/docker.md) /
  [desktop](docs/deployment/desktop.md) /
  [aws — CDK](docs/deployment/aws.md) /
  [aws — Terraform](docs/deployment/aws-terraform.md)), [local development](docs/development.md)
  ([test evidence dashboard](docs/development.md#test-evidence)),
  [admin recovery](docs/usage/admin-recovery.md),
  [observability](docs/observability.md),
  [CI](docs/ci.md), [email](docs/email.md).
- **Usage** — [`docs/usage/`](docs/usage/index.md). Task-oriented topic pages
  for operators: Salesforce connection, CSV format, authoring plans, running
  loads, notifications, user management.

---

## Claude Desktop integration (MCP)

The desktop app auto-registers as an MCP server for Claude Desktop on macOS.
Once registered, you can trigger loads, monitor progress, and diagnose failures
directly from a Claude conversation. See
[`docs/usage/using-the-mcp-server.md`](docs/usage/using-the-mcp-server.md) for
the operator guide and
[`docs/architecture/mcp-server.md`](docs/architecture/mcp-server.md) for the
architecture.

---

## Distribution Profiles

The app supports three deployment profiles, selected via `APP_DISTRIBUTION`:

| Profile | Auth | Transport | Database |
|---------|------|-----------|----------|
| `self_hosted` (default) | Local in-app login | HTTP or HTTPS | SQLite or PostgreSQL |
| `desktop` | None (no login) | Local loopback | SQLite only |
| `aws_hosted` | Local in-app login | HTTPS required | PostgreSQL required |

See [`docs/architecture.md`](docs/architecture.md#distribution-profiles) for
the detail.
