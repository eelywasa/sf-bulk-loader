# Spec: Distribution Layer for the Salesforce Bulk Loader

## Overview

This spec covers introducing a first-class **distribution layer** for the Salesforce Bulk Loader so the same core application can be delivered in multiple forms without forking core business logic.

Target distribution models:

- **Desktop**: Electron application with bundled frontend and local backend
- **Self-hosted web**: containerised deployment for local server, VM, NAS, or internal infrastructure
- **AWS-hosted web**: cloud-hosted frontend and backend deployment

The goal is to preserve a shared core for:

- load plan orchestration
- Salesforce connectivity
- CSV processing and partitioning
- run tracking and job monitoring
- primary API contracts
- core data model
- most frontend flows and UI logic

while allowing selected platform concerns to vary by distribution, such as:

- runtime packaging
- persistence engine
- user management
- transport security
- secrets management
- storage paths and file access model
- deployment automation
- release and update model

This document follows the same staged implementation model as prior specs so the work can be delivered incrementally while maintaining architectural coherence.

---

## Phase 0 Decisions

The following decisions are fixed unless explicitly revised later:

| Topic | Decision |
|---|---|
| MVP distribution targets | **Electron desktop** and **self-hosted Docker** |
| AWS support | **Must be supported architecturally**, but is **not MVP** |
| Shared application core | Frontend and backend business logic remain shared as far as practical |
| Desktop shell | **Electron** |
| Self-hosted packaging baseline | **Docker / Docker Compose** |
| AWS frontend hosting direction | Static frontend hosting on AWS |
| Distribution layer principle | Deployment-specific concerns should be isolated from core application logic |
| Desktop backend topology | Local bundled backend process |
| Hosted backend topology | Network-accessible backend service |
| Current web deployment | Remains supported |
| Goal | Add distribution options, not replace the current model |
| Docker database support | Docker should support **either SQLite or PostgreSQL** |
| Docker default quick-start database | **SQLite** |
| AWS database default | **PostgreSQL** |
| Desktop auth policy | **No login** for Electron MVP |
| Hosted auth policy | Self-hosted Docker and AWS should use the **same in-app authentication model initially** |
| Self-hosted HTTPS stance | **Optional**, but **recommended for anything other than localhost installs** |
| Self-hosted default transport posture | **HTTP-first** with documented HTTPS option |
| AWS backend direction | **ECS/Fargate** is the working assumption |
| AWS file storage default | **S3** should be the default cloud-hosted file storage model |
| Desktop secrets MVP | Keep secrets in the **application database** initially |
| Desktop secure storage roadmap | Explicitly include **future OS-native secure storage** as a later enhancement |
| Self-hosted reverse proxy | **nginx remains acceptable** in the current architecture |
| Roadmap priority | Deliver **working Docker first**, **Electron second**, with **AWS skeleton support** |

---

## Current State

| Layer | Status |
|---|---|
| Frontend | React 18 + Vite SPA |
| Backend | FastAPI application |
| Database | SQLite |
| Current deployment model | Containerised web deployment via Docker Compose |
| TLS model | HTTPS terminated by nginx in current container deployment |
| User model | In-app user/auth model exists |
| File access | Backend-managed local filesystem access |
| Runtime assumption | Browser-based web access |

The current implementation is already part of the way toward a multi-distribution architecture:

- the frontend is a distinct SPA rather than server-rendered pages
- the backend owns orchestration, persistence, and filesystem access
- API boundaries already exist between UI and core services
- the backend already centralises many environment-driven concerns

The main gap is that deployment assumptions are still implicit. Today the app effectively assumes:

- browser access
- hosted backend
- current container topology
- one primary persistence mode
- one auth posture
- one broad transport posture

This spec makes those assumptions explicit and relocates them into a distribution layer and profile-aware runtime model.

---

## Architecture

### Architectural Principle

The app should be refactored toward three layers:

1. **Core application layer**
   - shared frontend application
   - shared backend services and API contracts
   - shared domain model and orchestration logic
   - shared persistence model wherever practical

2. **Distribution layer**
   - desktop wrapper
   - self-hosted container wrapper
   - AWS hosting wrapper

3. **Distribution policy layer**
   - auth requirements
   - transport security requirements
   - persistence strategy
   - secrets handling strategy
   - filesystem/storage behavior
   - packaging and release behavior

The core application layer should remain as distribution-agnostic as practical. The distribution layer should adapt packaging, runtime wiring, and deployment assets. The distribution policy layer should determine what behaviors are valid in each runtime profile.

### Core Design Rule

The core design rule is:

**distribution-specific behavior must be driven by explicit runtime profile and configuration, not by ad hoc environment checks spread through the codebase.**

That means:

- no accidental auth bypasses for desktop
- no implicit assumption that browser and backend share one origin
- no hidden assumption that SQLite is always available or sufficient
- no hardcoded coupling between current Docker layout and application behavior

### Distribution Models

#### Desktop Distribution

Electron should package:

- frontend bundle
- local backend runtime
- desktop-specific launcher/bootstrap logic
- desktop-only integration points such as native file/folder selection where appropriate

Desktop is a **single-user local tool** profile. It should optimize for:

- simple installation
- direct local execution
- no login barrier for MVP
- local workspace ergonomics
- low operational overhead

#### Self-Hosted Web Distribution

Container deployment should continue to support:

- browser-based access
- backend API service
- reverse proxy / optional TLS termination
- deployability to local server, VM, NAS, or internal infrastructure

Self-hosted is the **primary MVP deployment model**. It should optimize for:

- easy quick start
- straightforward local hosting
- optional hardening path
- ability to scale from lightweight installs to more robust hosted installs

#### AWS-Hosted Distribution

AWS deployment should support:

- static frontend hosting
- managed or containerised backend hosting
- mandatory HTTPS
- cloud-appropriate secrets and configuration handling
- cloud-native storage defaults

AWS is **not MVP**, but the architecture must leave a clean lane open for it.

**Infrastructure as code**: AWS-hosted deployments will be defined using **AWS CDK**. CDK synthesises to CloudFormation, which manages provisioning and updates. Infrastructure must be fully reproducible from code and must support environment-specific configuration (e.g. staging vs. production) without manual intervention. No ad hoc console-driven infrastructure is acceptable for a supported distribution.

**Runtime configuration**: Runtime configuration will be injected via AWS-native mechanisms — **Secrets Manager** for sensitive values such as encryption keys and database credentials, and **SSM Parameter Store** for non-sensitive configuration. These values must be mapped into the application's distribution-aware configuration model under the `aws_hosted` profile, so the core app remains agnostic to how configuration is sourced.

### Distribution Policy Matrix

The distribution layer should explicitly support policy differences by deployment model rather than hardcoding one universal behavior.

| Policy Area | Desktop | Self-hosted Docker | AWS-hosted |
|---|---|---|---|
| Auth | none | local in-app auth | local in-app auth initially |
| Transport | local loopback | http first, https optional/recommended | https required |
| Database | SQLite | SQLite or PostgreSQL | PostgreSQL |
| File storage | local filesystem | local/shared filesystem | S3 default |
| Secrets | app DB initially | app DB | Secrets Manager + SSM Parameter Store (infra/config); app DB for persisted connection secrets |
| Runtime topology | bundled local backend | hosted backend | hosted backend |

---

## Stage 1: Distribution Abstraction and Runtime Profiles

### Goal

Introduce an explicit runtime/distribution profile so the app can vary behavior by distribution without branching ad hoc throughout the codebase.

### Design Intent

Define a distribution profile model with values such as:

- `desktop`
- `self_hosted`
- `aws_hosted`

This profile should drive environment-specific behavior in a controlled way.

### Areas expected to become profile-aware

- auth requirements
- API base URL / transport assumptions
- database configuration
- path and workspace configuration
- secrets handling
- TLS expectations
- startup bootstrap logic

### Deliverables

- distribution profile definition
- central config model for profile-aware behavior
- initial backend and frontend consumption of runtime profile
- regression-safe default for current web deployment

### Design Guidance

Profile branching should be concentrated in:

- config loading
- bootstrap/startup code
- packaging/deployment assets
- a small number of explicit service adapters

It should **not** be spread through domain orchestration code unless absolutely necessary.

---

## Stage 2: Persistence Strategy by Distribution

### Goal

Allow persistence behavior to vary by distribution while preserving one shared application model.

### Target direction

- desktop: **SQLite**
- self-hosted web: **SQLite or PostgreSQL**
- AWS-hosted: **PostgreSQL**

### Design Intent

The persistence layer should:

- keep ORM models shared
- keep migrations manageable
- minimise dialect-specific logic in the core path
- make database selection configuration-driven
- allow the same Docker distribution to run in lightweight single-node mode with SQLite or more robust hosted mode with PostgreSQL

### Notes

Supporting both SQLite and PostgreSQL in Docker is acceptable and desirable, provided:

- the application remains portable across both dialects
- automated tests cover both database engines
- the documentation makes clear when SQLite is appropriate versus when PostgreSQL is recommended

Recommended guidance:

- **SQLite**: default quick-start choice for local, single-user, or lightweight self-hosted installs
- **PostgreSQL**: recommended for multi-user, long-running, or production-style hosted installs

### Required outcomes

- the app must start cleanly against either configured database engine in Docker
- migrations must work against both supported engines
- CI should exercise both engines for at least smoke/integration coverage
- runtime configuration must not assume one engine implicitly

### Design Constraint

The core application should be written against the shared ORM/persistence abstraction, not against SQLite-specific behavior. Hosted evolution must not require unpicking desktop shortcuts later.

### Topics to resolve later if needed

- whether PostgreSQL should become the default for a future hardened production profile
- whether any hosted-only features may legitimately become PostgreSQL-specific in a later phase

---

## Stage 3: Identity and Access by Distribution

### Goal

Make user management requirements explicit and distribution-specific.

### Target direction

- desktop: **no login**
- self-hosted web: **application login required**
- AWS-hosted: **application login required**, with possible future external identity integration

### Design Intent

The app should support:

- direct desktop operation with no login barrier for Electron MVP
- required authentication for hosted deployments
- future extensibility toward SSO/OIDC for hosted environments

### Notes

Desktop no-login mode should be treated as a deliberate distribution policy, not as an accidental bypass. Hosted distributions should continue to enforce authentication and authorization.

The first hosted implementation should keep one shared in-app auth model across self-hosted and AWS-oriented profiles. That keeps the MVP smaller and avoids inventing two parallel auth systems too early.

### Topics to resolve later if needed

- whether desktop should later support optional local lock/unlock behavior
- whether hosted auth should eventually diverge between self-hosted and AWS

---

## Stage 4: Transport Security and Network Topology

### Goal

Support different transport security expectations by distribution.

### Target direction

- desktop: local loopback transport without mandatory HTTPS
- self-hosted web: HTTP or HTTPS depending on deployment context
- AWS-hosted: HTTPS mandatory

### Design Intent

Transport assumptions should be explicit in configuration and deployment assets.

### Notes

For self-hosted Docker:

- HTTP remains acceptable for **localhost** and some internal/lab installs
- the default shipped setup should be **HTTP-first** for quick start
- HTTPS should be **recommended** for anything beyond localhost
- nginx remains a valid reverse-proxy and TLS-termination layer for MVP

For Electron:

- frontend and backend may communicate over local loopback without HTTPS
- the backend should bind to localhost only in desktop mode
- desktop mode must not expose a network-accessible API beyond local loopback

For AWS-hosted:

- public endpoints should assume HTTPS-only access
- WebSocket transport should use secure equivalents when publicly exposed

### nginx Position

nginx remains acceptable for MVP because it already fits the current architecture and cleanly handles:

- reverse proxying
- static frontend serving in the hosted model
- optional TLS termination
- a familiar deployment path for self-hosters

Alternatives such as Caddy or Traefik may be considered later if deployment ergonomics or certificate automation become a stronger priority, but there is no need to switch now.

### Topics to resolve later if needed

- whether self-hosted should later offer a first-class HTTPS-ready distribution profile
- whether nginx remains the long-term preferred reverse proxy or simply the MVP default

---

## Stage 5: Packaging and Bootstrap

### Goal

Define how each distribution is built, packaged, started, and upgraded.

### Desktop

Expected concerns:

- Electron packaging
- bundled backend runtime
- local process orchestration
- installer generation
- desktop update strategy

### Self-Hosted Web

Expected concerns:

- Docker Compose
- reverse proxy strategy
- environment configuration
- optional TLS certificate handling
- SQLite quick-start and PostgreSQL alternative setup

### AWS-Hosted Web

Expected concerns:

- static frontend build/deploy
- ECS/Fargate backend target
- **AWS CDK** stack definitions synthesised to CloudFormation — fully reproducible, no manual console provisioning
- environment-specific configuration without manual overrides (e.g. staging vs. production stacks)
- runtime config injection via **Secrets Manager** (sensitive values) and **SSM Parameter Store** (non-sensitive config), mapped into the `aws_hosted` profile
- release automation

### Packaging Principle

Packaging should be treated as a wrapper around the shared core, not as a reason to duplicate or fork backend/frontend logic.

That means:

- Docker should run the existing hosted model cleanly
- Electron should embed and bootstrap the same core app in a desktop runtime
- AWS should reuse the same core services with cloud-specific deployment assets

---

## Stage 6: Filesystem, Storage, and Secrets Behavior

### Goal

Make local storage and secrets handling distribution-aware.

### Target areas

- desktop workspace directory model
- server-side input/output directory model
- cloud deployment file storage strategy
- encryption-key handling
- secret persistence vs external secret store

### Target direction

- desktop: secrets remain stored in the application database for MVP
- self-hosted: secrets remain stored in the application database using current encryption model
- AWS-hosted: infrastructure-level secrets (encryption keys, database credentials) must be sourced from **Secrets Manager**; non-sensitive operational config from **SSM Parameter Store**; both must be mapped into the `aws_hosted` profile at startup so the core application remains agnostic to the injection mechanism. Application-managed encrypted storage (Fernet-encrypted connection secrets) may remain for data persisted in the database.
- AWS-hosted file storage: **S3 default**

### Design Guidance

Desktop must clearly separate:

- packaged application assets
- runtime database
- runtime logs
- user workspace/input/output data

Hosted profiles must avoid desktop assumptions such as writing into packaged locations or relying on local-user state.

### Future Direction

OS-native secure storage should be treated as a deliberate future enhancement for desktop rather than being forgotten in a vague “maybe later” bucket.

### Topics to resolve later if needed

- how encryption keys should be sourced in each distribution
- whether desktop should later adopt OS-native secure storage for sensitive secrets
- whether self-hosted should support non-local/shared file storage in a later phase

---

## Stage 7: Release Engineering and Supportability

### Goal

Ensure each distribution has a viable build, test, release, and support model.

### Priority order

1. working **self-hosted Docker** implementation
2. working **Electron** implementation
3. **AWS skeleton** sufficient to ensure the architecture supports later cloud delivery

### Areas expected to be distribution-specific

- CI/CD build matrix
- signing/notarization for desktop
- image publishing for self-hosted
- deployment automation skeleton for AWS
- log collection and diagnostics
- smoke test strategy by distribution

### MVP expectation

The MVP is not complete until:

- Docker distribution works end-to-end with documented SQLite quick start
- Docker distribution supports PostgreSQL as an alternate hosted database option
- Electron distribution can launch the packaged app with no login and a functioning local backend
- AWS assumptions are explicit enough that later implementation will not require undoing core architectural choices made for Docker/Electron

### Testing Expectation

Distribution work is not “done” when packaging builds. It is done when there is enough automation and smoke coverage to detect regressions across:

- runtime profile selection
- database engine choice
- desktop versus hosted auth posture
- transport behavior
- packaging/bootstrap behavior

---

## Security Considerations

| Concern | Direction |
|---|---|
| Mixed auth assumptions across distributions | Make auth policy explicit by runtime profile |
| Insecure transport in public deployments | Require HTTPS for AWS and support HTTPS for self-hosted |
| Secret leakage in desktop bundle | Separate packaged code from runtime secrets |
| Cross-distribution config drift | Centralise profile-aware configuration |
| Data-store mismatch between desktop and hosted | Keep ORM/migration strategy deliberate and tested |
| Over-coupling core logic to one deployment model | Isolate distribution-specific adapters and bootstrap logic |
| Accidental desktop network exposure | Bind local backend to localhost only in desktop mode |

---

## Environment and Configuration Model

The distribution layer should introduce an explicit configuration model for:

- runtime profile selection
- database URL and dialect
- auth mode
- transport mode
- workspace/input/output locations
- encryption/secrets handling
- hosted deployment settings
- desktop bootstrap settings

### Suggested profile-aware settings

| Setting | Desktop | Self-hosted Docker | AWS-hosted |
|---|---|---|---|
| `APP_DISTRIBUTION` | `desktop` | `self_hosted` | `aws_hosted` |
| `DATABASE_URL` | SQLite | SQLite or PostgreSQL | PostgreSQL |
| `AUTH_MODE` | `none` | `local` | `local` initially |
| `TRANSPORT_MODE` | `local` | `http` or `https` | `https` |
| `INPUT_STORAGE_MODE` | local filesystem | local/shared filesystem | S3 default |
| `SECRETS_MODE` | app_db | app_db | Secrets Manager (infra secrets) + SSM Parameter Store (config) + app_db (persisted connection secrets) |

### Configuration rules

- distribution-specific behavior should be derived from explicit config, not inferred from environment accidents
- the current web deployment should remain reproducible with a self-hosted profile
- desktop mode must be able to bootstrap without web-server assumptions such as nginx-managed same-origin hosting
- hosted modes must not assume desktop shortcuts such as no-auth or local-only loopback access
- unsupported config combinations should fail clearly at startup

---

## Roadmap

### Phase 1: Foundation

Focus:

- define runtime profiles
- centralise profile-aware config
- remove hidden deployment assumptions
- support SQLite and PostgreSQL deliberately

Primary outcome:

- the codebase can express distribution differences cleanly without widespread conditional sprawl

### Phase 2: Self-Hosted Docker First-Class Support

Focus:

- harden self-hosted runtime profile
- keep nginx-based hosted model coherent
- provide SQLite quick start
- support PostgreSQL as alternate hosted path
- document HTTP-first plus HTTPS guidance

Primary outcome:

- Docker becomes the first fully supported distribution model

### Phase 3: Electron Support

Focus:

- add Electron shell and local backend bootstrap
- deliver no-login desktop behavior
- define workspace/runtime-data handling
- preserve shared core logic

Primary outcome:

- desktop becomes the second fully supported distribution model

### Phase 4: AWS Skeleton

Focus:

- codify `aws_hosted` assumptions
- ensure ECS/Fargate, PostgreSQL, S3, and HTTPS can slot in cleanly
- add enough assets/docs to anchor future implementation

Primary outcome:

- later AWS implementation is enabled by architecture rather than blocked by it

---

## Implementation Tickets

These tickets are ordered and dependency-aware so they can be handed to Claude incrementally while preserving coherence.

### 1. Define Distribution Profile and Shared Config Model — ✅ DONE

Goal: introduce an explicit runtime/distribution abstraction that the rest of the implementation can build upon.

Scope:

- add a profile-aware distribution config model in backend config
- define supported values: `desktop`, `self_hosted`, `aws_hosted`
- introduce config fields for auth mode, transport mode, database mode, and storage mode
- update startup/config validation so invalid profile combinations fail clearly
- preserve backward compatibility for the current deployment as `self_hosted`

Dependencies:

- none

Exit criteria:

- application can start with explicit distribution profiles
- current deployment continues to work unchanged or with minimal env updates

### 2. Refactor Frontend and Backend for Profile-Aware Runtime Configuration — ✅ DONE

Goal: remove hardcoded assumptions that the app is always browser-served behind the current container topology.

Scope:

- make frontend API base URL/profile behavior runtime-configurable
- make backend profile-aware for auth/transport/storage assumptions
- isolate profile branching to a small number of configuration/bootstrap points
- avoid scattering `if desktop` logic throughout business code

Dependencies:

- Ticket 1

Exit criteria:

- runtime profile controls core behavior through explicit config
- business logic remains largely distribution-agnostic

### 3. Introduce Multi-Engine Persistence Support for Hosted and Desktop Modes — ✅ DONE

Goal: support SQLite and PostgreSQL deliberately rather than accidentally.

Scope:

- review ORM models and migrations for SQLite/PostgreSQL portability
- update database bootstrap/config to support both engines cleanly
- ensure Docker can run with SQLite quick start and PostgreSQL alternate configuration
- add CI coverage for both engines
- document recommended usage patterns for each engine

Dependencies:

- Ticket 1

Exit criteria:

- Docker works with SQLite and PostgreSQL
- desktop path remains valid with SQLite
- database portability risks are documented and tested

### 4. Implement Self-Hosted Docker Distribution Hardening — ✅ DONE

Goal: establish Docker as the first fully supported distribution model.

Scope:

- define the self-hosted runtime profile
- keep nginx as reverse proxy for MVP
- provide HTTP-first default setup for quick start
- document HTTPS as recommended for anything beyond localhost
- review Docker Compose structure for SQLite and PostgreSQL options
- update docs and environment templates accordingly

Dependencies:

- Tickets 1 through 3

Exit criteria:

- self-hosted Docker is a coherent documented first-class distribution
- SQLite quick start works end-to-end
- PostgreSQL variant is supported and documented

### 5. Implement Hosted Auth Policy for Self-Hosted and AWS Skeleton — ✅ DONE

Goal: make hosted auth behavior explicit and shared across hosted distributions.

Scope:

- define hosted auth mode as required in-app authentication
- ensure self-hosted profile enforces auth
- align AWS skeleton assumptions to the same auth model initially
- document that SSO/OIDC is a future enhancement, not MVP

Dependencies:

- Tickets 1 and 2

Exit criteria:

- hosted profiles share one explicit auth behavior
- desktop no-auth and hosted auth are clearly separated

### 6. Implement Transport/TLS Policy by Distribution — ✅ DONE

Goal: make transport expectations explicit by distribution rather than implied by deployment history.

Scope:

- define local loopback transport assumptions for desktop
- define HTTP-first plus optional HTTPS behavior for self-hosted
- define HTTPS-only assumption for AWS skeleton
- review WebSocket endpoint handling under each profile
- document nginx/TLS behavior for self-hosted
- document path toward automated certificate management (e.g. Let's Encrypt / Certbot)
  as a recommended enhancement for self-hosted HTTPS deployments

Dependencies:

- Tickets 1, 2, and 4

Exit criteria:

- transport posture is profile-driven and documented
- self-hosted HTTP quick start remains easy
- public-facing recommendations are explicit

### 7. Implement Electron Packaging Skeleton — ✅ DONE

Goal: create the second supported distribution target with no-login desktop behavior.

Scope:

- add Electron shell structure
- package frontend for desktop loading
- define local backend launch/bootstrap from Electron main process
- add desktop runtime profile
- ensure desktop binds backend locally and bypasses hosted login flow
- document local packaging/development workflow

Dependencies:

- Tickets 1 through 3

Exit criteria:

- Electron app launches the UI and local backend successfully
- desktop runs without login
- desktop architecture does not require reworking core services

### 8. Implement Desktop Secrets and Workspace Behavior — ✅ DONE

Goal: make desktop-specific local behavior explicit and safe enough for MVP.

Scope:

- keep secrets stored in the application database for MVP
- define desktop workspace/data directory strategy
- ensure packaged assets and runtime data are separated
- add roadmap note and extension point for future OS-native secure storage

Dependencies:

- Ticket 7

Exit criteria:

- desktop distribution has a defined storage/secrets model
- future secure-store enhancement is documented, not forgotten

### 9. Implement AWS Distribution Skeleton — ✅ DONE

Goal: ensure the architecture supports later AWS delivery without requiring major redesign.

Scope:

- document `aws_hosted` runtime profile
- define AWS assumptions: static frontend hosting, ECS/Fargate backend, PostgreSQL, S3 default file storage, HTTPS mandatory
- define infrastructure-as-code approach: **AWS CDK** stacks synthesising to CloudFormation; fully reproducible and environment-parameterised (no manual console provisioning)
- define runtime config injection model: **Secrets Manager** for sensitive values (encryption keys, DB credentials); **SSM Parameter Store** for non-sensitive operational config; both mapped into the `aws_hosted` profile at startup
- add placeholder CDK stack(s) and/or docs sufficient to anchor later implementation
- ensure hosted config model supports cloud-native secrets/config injection without requiring changes to core application logic

Dependencies:

- Tickets 1, 3, 5, and 6

Exit criteria:

- AWS is represented in the architecture and config model
- Docker/Electron choices do not block later ECS/Fargate delivery

### 10. Add Cross-Distribution Build/Test/Release Workflows — ✅ DONE

## Goal

Establish a reproducible, automation-driven build, test, and release framework across supported distribution models, ensuring:

- distribution artifacts can be built from a clean checkout
- cross-distribution regressions are detected early
- runtime profile behavior (auth, transport, database, bootstrap) is validated in CI
- release outputs are clearly defined and versioned

This work must make distribution support **operationally real**, not just buildable.

## Scope

### A. CI Platform and Structure

- use **GitHub Actions** as the CI/CD platform
- define workflows under `.github/workflows/`
- separate workflows by concern:
    - shared checks
    - Docker distribution
    - Electron packaging
    - AWS skeleton validation
    - release pipeline

---

### B. Shared Quality Workflow

Create `ci-shared.yml`:

Responsibilities:

- install backend and frontend dependencies
- run backend tests
- run frontend build and lint (if present)
- validate config model integrity
- ensure application can initialise with explicit runtime profiles

Exit conditions:

- no failing tests
- no build errors
- config validation passes

---

### C. Self-Hosted Docker Workflow

Create `ci-docker.yml`:

Responsibilities:

- build backend Docker image
- build frontend assets for hosted deployment
- validate Docker Compose configuration
- run smoke tests in:
    - SQLite mode (quick start)
    - PostgreSQL mode (hosted configuration)

Smoke tests must verify:

- containers start successfully
- backend health endpoint responds
- frontend can reach backend
- hosted auth mode is enforced
- database selection is respected (SQLite vs PostgreSQL)

Artifact handling:

- on release (tag), publish image to **GHCR**

---

### D. Database Matrix Validation

Integrated into Docker workflow:

- run migrations against:
    - SQLite
    - PostgreSQL (single supported version)
- validate application startup against both engines
- run minimal integration/smoke checks for each engine

Constraints:

- no implicit reliance on one database engine
- failures must surface in CI

---

### E. Electron Packaging Workflow

Create `ci-electron.yml`:

Responsibilities:

- build frontend assets for desktop
- package Electron app for **macOS only (initial target)**
- verify desktop bootstrap wiring

Smoke tests must confirm:

- Electron app launches
- local backend process starts
- `desktop` runtime profile is selected
- authentication is bypassed (no-login policy)
- backend binds to localhost assumptions

Artifacts:

- generate macOS build output (e.g. `.app` or installer)
- signing/notarisation is **not implemented yet**
- include placeholders for future signing integration

---

### F. AWS Skeleton Validation Workflow

Create `ci-aws-skeleton.yml`:

Responsibilities:

- install CDK dependencies
- run `cdk synth`
- validate stack definitions compile successfully

Constraints:

- **no live deployment**
- validate that `aws_hosted` profile assumptions are represented
- ensure infrastructure code is buildable and consistent

---

### G. Release Workflow

Create `release.yml`:

Trigger:

- runs on **semantic version tags only** (e.g. `v1.2.0`)

Responsibilities:

- build Docker distribution
- publish Docker image to **GitHub Container Registry (GHCR)**
- build Electron macOS artifact
- attach Electron artifact to GitHub release
- generate release metadata (version, notes)

Outputs:

Docker:

- versioned image in GHCR

Electron:

- macOS packaged artifact
- unsigned (signing deferred)

---

### H. Smoke-Test Contract by Distribution

Define and enforce minimum smoke coverage:

#### Self-hosted Docker

- container startup succeeds
- backend health endpoint responds
- frontend ↔ backend connectivity works
- auth is enforced
- SQLite mode works
- PostgreSQL mode works

#### Electron (desktop)

- app launches
- local backend starts
- no-login behavior is active
- API round-trip succeeds
- runtime profile = `desktop`

#### AWS skeleton

- CDK stacks synthesise successfully
- configuration aligns with `aws_hosted` profile expectations
- no AWS-specific assumptions leak into core application logic

---

## Non-goals

- full production-grade deployment pipelines
- multi-platform Electron packaging (Windows/Linux deferred)
- desktop code signing and notarisation (placeholders only)
- live AWS deployment
- full end-to-end functional test coverage

---

## Deliverables

- GitHub Actions workflows:
    - `ci-shared.yml`
    - `ci-docker.yml`
    - `ci-electron.yml`
    - `ci-aws-skeleton.yml`
    - `release.yml`
- reusable scripts or commands for:
    - Docker build and smoke testing
    - database matrix validation
    - Electron packaging and smoke testing
    - CDK synth validation
- documented release artifact structure
- defined smoke-test matrix per distribution

---

## Dependencies

- Ticket 3: persistence portability (SQLite/PostgreSQL)
- Ticket 4: Docker distribution hardening
- Ticket 7: Electron packaging skeleton
- Ticket 9: AWS distribution skeleton

---

## Exit Criteria

- CI can build the self-hosted Docker distribution from a clean checkout
- CI verifies application startup in both SQLite and PostgreSQL modes
- CI packages Electron app for macOS
- Electron smoke test validates local backend bootstrap and no-login behavior
- AWS CDK stacks synthesise successfully in CI
- Docker images are published to GHCR on tagged release
- Electron macOS artifact is attached to release
- regressions in:
    - runtime profile handling
    - database engine support
    - auth posture
    - transport assumptions
    - bootstrap logic  
        fail CI before merge/release

---

## Suggested Workflow Trigger Model

| Event         | Workflows                                 |
| ------------- | ----------------------------------------- |
| Pull Request  | shared + docker + electron (lightweight)  |
| Main branch   | shared + docker + electron + aws-skeleton |
| Tag (release) | full build + publish artifacts            |

---

## Electron Full Distribution Backlog

The CI workflow (`ci-electron.yml`) verifies that Electron bootstraps correctly from source and
that electron-builder can produce a `.app`. The following work is required before that `.app` is
truly user-distributable.

### 1. Path handling for packaged mode (`electron/main.js`)

`main.js` currently uses dev-relative paths:

```js
const FRONTEND_INDEX = path.join(__dirname, '..', 'frontend', 'dist', 'index.html')
const BACKEND_DIR = path.join(__dirname, '..', 'backend')
```

Inside a packaged `.app`, `__dirname` resolves to `Contents/Resources/app/`, so these `../` paths
do not find the bundled resources. Fix using `app.isPackaged`:

```js
const resourcesPath = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..')
const FRONTEND_INDEX = path.join(resourcesPath, 'frontend', 'dist', 'index.html')
const BACKEND_DIR = path.join(resourcesPath, 'backend')
```

`process.resourcesPath` is cross-platform; the same fix covers Windows and Linux.

### 2. Backend and frontend bundling (`electron/package.json`)

electron-builder's `"files"` currently only bundles `main.js` and `preload.js`. The backend
Python source and the built frontend assets must be included via `extraResources`:

```json
"extraResources": [
  {
    "from": "../backend",
    "to": "backend",
    "filter": ["**/*", "!.venv/**", "!__pycache__/**", "!*.pyc", "!test_*.db", "!tests/**"]
  },
  {
    "from": "../frontend/dist",
    "to": "frontend/dist"
  }
]
```

Files land at `Contents/Resources/backend/` and `Contents/Resources/frontend/dist/` —
matching the path fix in §1.

**Python runtime:** `extraResources` copies raw Python source. The user must have Python 3.12
installed and accessible on PATH. For a fully self-contained installer, the backend should be
compiled with PyInstaller into a single binary bundled alongside the app.

### 3. macOS code signing and notarization

Unsigned `.app` bundles are quarantined by Gatekeeper on macOS 10.15+. Users must right-click →
Open, which is unsuitable for general distribution.

**Requirements:**
- Apple Developer Program membership ($99/year)
- Developer ID Application certificate exported as `.p12`
- App-specific password for notarization (`xcrun notarytool`)

**electron-builder additions:**
```json
"mac": {
  "target": [{"target": "zip", "arch": ["x64", "arm64"]}],
  "hardenedRuntime": true,
  "gatekeeperAssess": false,
  "entitlements": "entitlements.mac.plist",
  "entitlementsInherit": "entitlements.mac.plist"
},
"afterSign": "scripts/notarize.js"
```

The entitlements plist must grant `com.apple.security.cs.allow-unsigned-executable-memory`
(for Python's JIT) and `com.apple.security.inherit` for child processes (uvicorn).

**GitHub Actions secrets needed:** `MACOS_CERTIFICATE_P12`, `MACOS_CERTIFICATE_PASSWORD`,
`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`.

### 4. Windows packaging (deferred)

**Installer target:** NSIS (default electron-builder Windows target). EV (Extended Validation)
code-signing certificate strongly recommended — bypasses SmartScreen immediately. Standard OV
certs trigger a SmartScreen warning until the app accumulates reputation.

**Venv path fix:** On Windows the virtualenv binary directory is `Scripts\`, not `bin/`. Update
`findUvicorn()` and `findAlembic()` in `main.js`:

```js
const venvBin = process.platform === 'win32' ? '.venv/Scripts' : '.venv/bin'
const ext = process.platform === 'win32' ? '.exe' : ''
const venvUvicorn = path.join(BACKEND_DIR, venvBin, `uvicorn${ext}`)
```

**GitHub Actions secrets needed:** `WINDOWS_CERTIFICATE_PFX` (base64), `WINDOWS_CERTIFICATE_PASSWORD`.

**electron-builder additions:**
```json
"win": {
  "target": "nsis",
  "publisherName": "Your Name"
}
```

### 5. Linux packaging (deferred)

**Targets:** AppImage (portable, no install required), deb (Debian/Ubuntu), rpm (Fedora/RHEL).

**Smoke test on Linux CI:** Linux runners have no display. Add before launching Electron:

```bash
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
```

**electron-builder additions:**
```json
"linux": {
  "target": ["AppImage", "deb"],
  "category": "Office"
}
```

Code signing is not required on Linux, but AppImages can be GPG-signed for integrity verification.

### 6. Auto-update (deferred)

`electron-updater` (bundled with electron-builder) provides automatic in-app updates from GitHub
Releases. Requires `publish` config in electron-builder and code signing to be in place before
it is useful.

---

### 11. Regression and Security Hardening Pass

Goal: validate that adding the distribution layer has not fragmented the product.

Scope:

- review profile branching for unnecessary spread
- confirm auth differences are deliberate and contained
- confirm transport/security defaults are documented accurately
- run regression coverage across Docker and Electron MVP paths
- validate AWS skeleton assumptions still align with the implemented abstraction

Dependencies:

- Tickets 1 through 10

Exit criteria:

- Docker and Electron are both coherent first-class distributions
- AWS remains a credible next step, not a hand-wavy aspiration

---

## Suggested Work Split

### Agent A: Backend and Architecture

- config model
- persistence strategy
- auth policy handling
- transport policy wiring
- secrets handling
- hosted deployment updates
- AWS backend deployment assets

### Agent B: Frontend and Desktop Wrapper

- frontend runtime config
- Electron shell/bootstrap
- desktop packaging
- desktop-specific UX adjustments
- distribution-aware UI behavior where needed

### Agent C: DevOps / Release Engineering

- Docker distribution hardening
- AWS infrastructure/deployment setup skeleton
- desktop packaging pipeline
- CI/CD and release automation

Recommended coordination checkpoints:

1. after runtime profile/config model is stable
2. after SQLite/PostgreSQL portability work is in place
3. after Docker first-class support is complete
4. after Electron bootstrap works end-to-end
5. after AWS skeleton assumptions are codified

---

## Files Likely Affected

### Backend

| File | New / Modified |
|---|---|
| `backend/app/config.py` | Modified |
| `backend/app/main.py` | Modified |
| `backend/app/database.py` | Modified |
| `backend/app/services/auth.py` | Modified |
| `backend/app/services/*` | Possibly modified where runtime assumptions are embedded |
| `backend/alembic/*` | Possibly modified depending on DB strategy |

### Frontend

| File | New / Modified |
|---|---|
| `frontend/src/api/client.ts` | Modified |
| `frontend/src/App.tsx` | Possibly modified |
| `frontend/src/pages/*` | Possibly modified where auth/runtime assumptions differ |
| `frontend/vite.config.ts` | Modified |

### New Desktop Layer

| File | New / Modified |
|---|---|
| `desktop/*` or `electron/*` | New |
| Electron main/preload/build config | New |

### Deployment Assets

| File | New / Modified |
|---|---|
| `docker-compose.yml` | Modified |
| `frontend/nginx.conf` | Possibly modified |
| AWS deployment files | New |
| CI/CD workflow files | Modified / New |

### Documentation

| File | New / Modified |
|---|---|
| `docs/distribution-layer-spec.md` or equivalent | New |
| deployment/readme docs | Modified |
| environment/config docs | Modified |

---

## Open Questions

The following items are intentionally left as future-phase considerations rather than blockers for this spec revision.

1. Should PostgreSQL become the default for a future hardened self-hosted production profile, while SQLite remains the quick-start path?
2. Should Electron later gain an optional local lock/unlock mechanism despite the MVP no-login policy?
3. Should OS-native secure storage become a post-MVP requirement for desktop, or remain an optional enhancement?
4. Should nginx remain the long-term reverse proxy for self-hosted deployments, or simply the acceptable MVP default?
5. Should future AWS hosted auth remain in-app only, or become the first place where SSO/OIDC is introduced?

