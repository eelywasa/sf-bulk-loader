# Documentation audit — SFBL-210

**Generated:** 2026-04-22 for [SFBL-210](https://matthew-jenkin.atlassian.net/browse/SFBL-210).

This file is the work list for the remaining Phase 1 stories under [SFBL-208](https://matthew-jenkin.atlassian.net/browse/SFBL-208):

- **Architecture & design pillar** — [SFBL-211](https://matthew-jenkin.atlassian.net/browse/SFBL-211)
- **Operations & developer pillar** — [SFBL-212](https://matthew-jenkin.atlassian.net/browse/SFBL-212)
- **Usage pillar** — [SFBL-213](https://matthew-jenkin.atlassian.net/browse/SFBL-213)
- **Drift sweep + `CLAUDE.md` docs-policy + README trim** — [SFBL-214](https://matthew-jenkin.atlassian.net/browse/SFBL-214)

This file is intentionally disposable — it will be deleted by SFBL-214 once the pillar work lands.

---

## Top-level

| File | Purpose (who for) | Known staleness / drift risks | Target pillar |
|---|---|---|---|
| `README.md` | Project overview & quick-start signpost | `ADMIN_USERNAME` / `ADMIN_PASSWORD` line 36 — should be `ADMIN_EMAIL` (SFBL-185). Architecture/project-structure sections duplicate what will live in `docs/architecture.md`. | `index` (trim to signpost, SFBL-214) |
| `CLAUDE.md` | Claude Code agent guidance | No docs-policy section yet — add one in SFBL-214 codifying Jira-primary / docs-as-handbook. | `policy` |

## docs/

| File | Purpose | Known staleness / drift risks | Target pillar |
|---|---|---|---|
| `admin-recovery.md` | Break-glass CLI for operators locked out | `is_admin=True` narrative in a couple of places; rewrite for profile-based RBAC (`profile_id=admin`). Confirm CLI invocation matches shipped code. | `ops` (SFBL-212) |
| `ci.md` | CI workflow topology | Appears current; confirm job names match `.github/workflows/*.yml` after recent epics. | `ops` (SFBL-212) |
| `development.md` | Local dev, tests, migrations, parallel-agent conventions | `ADMIN_USERNAME` example; link to `PARALLEL_AGENTS.md` for orchestration; verify test commands. | `ops` (SFBL-212) |
| `email.md` | Email backend + delivery log reference | Link at line 4 points to `specs/email-service-spec.md` — fix to `specs/implemented/email-service-spec.md` (moved by SFBL-210). Otherwise current. | `ops` (SFBL-212) |
| `issues.md` | Outstanding known issues | Audit: most entries resolved; promote to curated known-issues page or delete. | `ops` (SFBL-212 decision) |
| `observability.md` | Event taxonomy, metrics, spans, DoD checklist | Authoritative; keep in place (will not move into `docs/architecture/`). | `ops` (SFBL-212) |
| `s3-connection-setup.md` | S3 IAM + connection wiring | Verify against shipped UI flow (SFBL-115). | `usage` (SFBL-213) |
| `salesforce-jwt-setup.md` | Connected App + JWT walkthrough | Verify against current UI + `SF_API_VERSION` default. | `usage` (SFBL-213) |
| `ui-conventions.md` | Design tokens, form styling, shared components | Maintained per epic DoD; authoritative. | `architecture` (SFBL-211 link) |
| `usage.md` | Task-oriented user docs — connections, plans, runs, notifications | Large; audit for coverage gaps against shipped features (Bulk Query SFBL-114, Notifications SFBL-117, S3 sinks SFBL-115, User Management SFBL-185/187/188). Will be restructured into `docs/usage/` tree with frontmatter by SFBL-213. | `usage` (SFBL-213) |

## docs/deployment/

| File | Purpose | Known staleness / drift risks | Target pillar |
|---|---|---|---|
| `aws.md` | AWS-hosted profile (CDK, RDS, CloudFront) | `ADMIN_USERNAME` references; add `ADMIN_EMAIL`, `INVITATION_TTL_HOURS`, other RBAC env vars. | `ops` (SFBL-212) |
| `desktop.md` | Electron desktop profile | `auth_mode=none` narrative; verify against current shipped desktop bundle. | `ops` (SFBL-212) |
| `docker.md` | Self-hosted Docker profile | `ADMIN_USERNAME` references; line 164 link `../specs/distrubution-layer-spec.md` needs updating to `../specs/implemented/distribution-layer-spec.md` (filename typo fixed on move). Break-glass CLI subcommands to cross-check. | `ops` (SFBL-212) |

## docs/specs/ (root — live contracts only)

| File | Purpose | Status | Target pillar |
|---|---|---|---|
| `rbac-permission-matrix.md` | Human-readable permission matrix | Current — enforced by `backend/tests/test_permission_matrix.py` | `live-contract` (stays) |
| `rbac-permission-matrix.yml` | Canonical YAML source | Current — regenerate `.md` when this changes | `live-contract` (stays) |

SFBL-210 triage moved 7 other files out of this directory (see next section).

## docs/specs/implemented/ — archived specs

Moved by SFBL-210 triage on 2026-04-22 (archival banner added to each):

| File | Ticket | Superseded by |
|---|---|---|
| `auth-spec.md` | — | `multi-user-rbac.md` (also archived); live contract is `rbac-permission-matrix.md` |
| `distribution-layer-spec.md` (typo fixed on move) | — | `docs/deployment/{docker,desktop,aws}.md` + `docs/architecture.md` (pending SFBL-211) |
| `email-service-spec.md` | SFBL-136 | `docs/email.md` |
| `multi-user-rbac.md` | SFBL-185 / 187 / 188 | `docs/specs/rbac-permission-matrix.{md,yml}` + ops docs |
| `notifications-spec.md` | SFBL-117 | `docs/usage.md` (notifications section) |
| `profile-reset-decisions.md` | SFBL-145–151 | shipped — superseded by admin-recovery docs |
| `worker-execution-spike.md` | SFBL-120 | spike concluded; outcomes adopted in orchestrator code |

Pre-existing files in `docs/specs/implemented/` (left untouched):

- `csv-preview-enhancements-spec.md`
- `dark-mode-theming-spec.md`
- `frontend-theme-toggle-spec.md`
- `input-storage-spec.md`
- `observability-baseline-spec.md`
- `refactor-rationalisation-spec.md`

## Known staleness hotspots (drift sweep work list for SFBL-214)

Grep targets and expected fixes:

| Pattern | Current locations (audit date) | Target |
|---|---|---|
| `ADMIN_USERNAME` | `README.md`, `docs/development.md`, `docs/deployment/{docker,aws,desktop}.md`, `.env.example` (if present) | `ADMIN_EMAIL` |
| `is_admin=True` as bootstrap narrative | `docs/admin-recovery.md` | Profile-based language (`profile_id=admin`) |
| `/invite/<token>` URL shape | Search across `docs/` | `/invite/accept?token=<token>` |
| Old spec paths (now archived) | `docs/deployment/docker.md:164`, `docs/email.md:4`, `docs/specs/rbac-permission-matrix.md:17` | `docs/specs/implemented/<file>.md` |
| Code-comment references to moved specs | `backend/app/models/notification_subscription.py`, `backend/app/models/email_delivery.py`, `DECISIONS.md` | Update to `docs/specs/implemented/…` paths |

## Pillar → story mapping

| Pillar | Story | Scope |
|---|---|---|
| Architecture | [SFBL-211](https://matthew-jenkin.atlassian.net/browse/SFBL-211) | Create `docs/architecture.md` + `docs/architecture/` subdir (`auth-and-rbac.md`, `run-execution.md`, `storage.md`). Absorb design narrative currently in `CLAUDE.md` and in the now-archived specs. Every page starts with "What this covers / who should read this". |
| Ops/Dev | [SFBL-212](https://matthew-jenkin.atlassian.net/browse/SFBL-212) | Refresh `docs/deployment/*.md`, `admin-recovery.md`, `development.md`, `ci.md`, `observability.md`; audit `issues.md`. |
| Usage | [SFBL-213](https://matthew-jenkin.atlassian.net/browse/SFBL-213) | Split `usage.md` into `docs/usage/` tree keyed by task (getting started, connections, plans, runs, files, bulk query, notifications, output sinks, user management, settings, account recovery). **Every file uses the YAML frontmatter contract** (title/slug/nav_order/tags/required_permission/summary) — feeds Phase 2 in-app Help. |
| Drift sweep | [SFBL-214](https://matthew-jenkin.atlassian.net/browse/SFBL-214) | Apply the drift-hotspots table above; add docs-policy section to `CLAUDE.md`; trim `README.md` to signpost; delete this audit file. |
