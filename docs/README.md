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
| [architecture/foreign-keys.md](architecture/foreign-keys.md) | FK inventory, cascade/SET NULL intent, SQLite enforcement |
| [architecture/aws-topology.md](architecture/aws-topology.md) | AWS request path + stack ownership (Mermaid views + auto-generated CDK diagram) |
| [architecture/mcp-server.md](architecture/mcp-server.md) | MCP server — sidecar architecture, two deployment channels, discovery-file and tool-binding contracts |
| [architecture/mcp-tool-reference.md](architecture/mcp-tool-reference.md) | Reference table of all 36 MCP tools grouped by area |
| [ui-conventions.md](ui-conventions.md) | Design tokens, `formStyles.ts`, shared components, theming rules |

---

## Operations & developer

How to run, develop, and operate the app.

| Document | Description |
|---|---|
| [deployment/docker.md](deployment/docker.md) | Self-hosted Docker deployment — configuration, HTTPS, PostgreSQL |
| [deployment/desktop.md](deployment/desktop.md) | Desktop (Electron) deployment |
| [deployment/aws.md](deployment/aws.md) | AWS-hosted deployment (CDK) |
| [deployment/aws-terraform.md](deployment/aws-terraform.md) | AWS-hosted deployment — Terraform/OpenTofu flavour |
| [deployment/migrating-to-postgres.md](deployment/migrating-to-postgres.md) | Self-hosted SQLite → PostgreSQL cutover |
| [deployment/migrating-to-aws-hosted.md](deployment/migrating-to-aws-hosted.md) | Self-hosted → AWS-hosted cutover (DB, encryption key, S3, SES, DNS) |
| [development.md](development.md) | Local development, tests, migrations |
| [versioning.md](versioning.md) | Version scheme, git tags, how to cut a release |
| [observability.md](observability.md) | Event taxonomy, metrics, spans, DoD checklist |
| [ci.md](ci.md) | CI workflow topology |
| [email.md](email.md) | Outbound email backend, SMTP credentials, delivery log |
| [operations/test-evidence-runbook.md](operations/test-evidence-runbook.md) | OAuth App provisioning, Secrets Manager seeding, rotation, incident revocation, access management for the test-evidence dashboard |

---

## Usage (operator handbook)

Task-oriented topic pages for day-to-day use. Each page carries YAML
frontmatter (`title`, `slug`, `nav_order`, `required_permission`, `summary`)
and stands alone — deep links are safe.

These topics are available both here and **in the running application** at `/help` (the Help link in the top-right of the app shell). The in-app version is built from this directory at deploy time — no internet connection needed.

Start at [`usage/index.md`](usage/index.md), which lists topics in nav order:

- Getting started, Salesforce connection (+ JWT setup walkthrough), CSV format
- Authoring load plans, running loads, files pane
- Bulk queries, output sinks (+ S3 connection setup walkthrough)
- Notifications
- User management, settings, admin recovery (break-glass CLI)
- Account recovery

---

## Specs

`docs/specs/` is reserved for **live** cross-team contracts:

- [specs/rbac-permission-matrix.md](specs/rbac-permission-matrix.md) +
  `specs/rbac-permission-matrix.yml` — the authoritative permission → route
  map.
- [specs/test-evidence-taxonomy.md](specs/test-evidence-taxonomy.md) — the
  cross-layer Allure label / link / category / URL contract every test
  suite conforms to. Source of truth for the helpers under
  `tests/e2e/sf/playwright/helpers/allure.ts` and
  `backend/tests/_allure_helpers.py`.
- [specs/input-encoding-and-error-visibility.md](specs/input-encoding-and-error-visibility.md)
  — locked design for input-decoding robustness, run error-summary visibility,
  and load-step `object_name` validation. Not yet ticketed.

Historical specs that have been implemented live under
[`specs/implemented/`](specs/implemented/) for reference; they are **not**
authoritative about current behaviour — check the code or the relevant pillar
instead. Notable historical artefacts:

- [specs/implemented/sfbl-334-spike-report.md](specs/implemented/sfbl-334-spike-report.md)
  — the Phase 1 hosting spike that informed (and then was overturned by)
  the SFBL-334 cross-layer Allure dashboard architecture. Preserved for the
  size + history-merge numbers, not the verdict.

---

## Policy

See the **Documentation Policy** section in the repo-root
[`CLAUDE.md`](../CLAUDE.md) for authoring rules (pillar boundaries, YAML
frontmatter contract, spec archival, README scope).
