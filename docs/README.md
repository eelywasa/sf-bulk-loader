# Documentation index

The Bulk Loader's documentation is organised into three **pillars** plus a
policy layer. If you're not sure where to start, pick the pillar that matches
your role.

---

## Architecture & design

Concept-level documents describing how the system is built. Read these before
making architectural changes.

| Document | Description |
|---|---|
| [architecture.md](architecture.md) | System overview — backend, frontend, data model, distribution profiles |
| [architecture/auth-and-rbac.md](architecture/auth-and-rbac.md) | Auth modes, JWT sessions, RBAC model, invitations |
| [architecture/run-execution.md](architecture/run-execution.md) | Orchestrator, partitioning, Salesforce Bulk API, polling, aborts |
| [architecture/storage.md](architecture/storage.md) | Input discovery, output sinks, encryption at rest |

---

## Operations & developer

How to run, develop, and operate the app.

| Document | Description |
|---|---|
| [deployment/docker.md](deployment/docker.md) | Self-hosted Docker deployment — configuration, HTTPS, PostgreSQL |
| [deployment/desktop.md](deployment/desktop.md) | Desktop (Electron) deployment |
| [deployment/aws.md](deployment/aws.md) | AWS-hosted deployment |
| [development.md](development.md) | Local development, tests, migrations |
| [admin-recovery.md](admin-recovery.md) | Break-glass CLI for locked-out admins |
| [observability.md](observability.md) | Event taxonomy, metrics, spans, DoD checklist |
| [ci.md](ci.md) | CI workflow topology |
| [email.md](email.md) | Outbound email backend, SMTP credentials, delivery log |
| [salesforce-jwt-setup.md](salesforce-jwt-setup.md) | Deep-dive walkthrough: Salesforce Connected App + JWT |
| [s3-connection-setup.md](s3-connection-setup.md) | S3 input/output connection walkthrough |

---

## Usage (operator handbook)

Task-oriented topic pages for day-to-day use. Each page carries YAML
frontmatter (`title`, `slug`, `nav_order`, `required_permission`, `summary`)
and stands alone — deep links are safe.

These topics are available both here and **in the running application** at `/help` (the Help link in the top-right of the app shell). The in-app version is built from this directory at deploy time — no internet connection needed.

Start at [`usage/index.md`](usage/index.md), which lists topics in nav order:

- Getting started, Salesforce connection, CSV format
- Authoring load plans, running loads, files pane
- Bulk queries, output sinks
- Notifications
- User management, settings
- Account recovery

---

## Specs

`docs/specs/` is reserved for **live** cross-team contracts:

- [specs/rbac-permission-matrix.md](specs/rbac-permission-matrix.md) +
  `specs/rbac-permission-matrix.yml` — the authoritative permission → route
  map.

Historical specs that have been implemented live under
[`specs/implemented/`](specs/implemented/) for reference; they are **not**
authoritative about current behaviour — check the code or the relevant pillar
instead.

---

## Policy

See the **Documentation Policy** section in the repo-root
[`CLAUDE.md`](../CLAUDE.md) for authoring rules (pillar boundaries, YAML
frontmatter contract, spec archival, README scope).
