# Local Development

## What this covers / who should read this

How to run the backend and frontend from source, execute the test suites,
build the PyInstaller desktop binary, and enable optional observability
features. Read this before making your first code change. For deployment-time
concerns (Docker / AWS / Electron packaging) see the guides under
[`docs/deployment/`](deployment/). For the multi-agent workflow used for larger
refactors see [`PARALLEL_AGENTS.md`](../PARALLEL_AGENTS.md) at the repo root.

---

## Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp ../.env.example .env
# Set ENCRYPTION_KEY, JWT_SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD
# (ENCRYPTION_KEY and JWT_SECRET_KEY auto-generate if left blank; ADMIN_EMAIL/
#  ADMIN_PASSWORD are required on first boot for hosted profiles.)

# Apply database migrations
alembic upgrade head

# Start the development server (auto-reloads on file changes)
uvicorn app.main:app --reload
```

Backend: http://localhost:8000
API docs (Swagger): http://localhost:8000/docs

---

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173
API calls are proxied to `http://localhost:8000` via the Vite dev server config.

Set `APP_ENV=development` in the backend `.env` to allow the Vite dev origin through CORS.

---

## Database Migrations

```bash
cd backend

# Apply all pending migrations
alembic upgrade head

# Create a new migration after changing SQLAlchemy models
alembic revision --autogenerate -m "describe your change"
```

---

## PyInstaller (desktop binary)

The Electron app bundles a compiled backend binary rather than raw Python source.
This makes the packaged app self-contained — no Python installation required on the
user's machine.

### Prerequisites

```bash
cd backend
pip install -r requirements-desktop.txt   # slim deps — no asyncpg, no pytest
pip install pyinstaller
```

### Build the binary

```bash
cd backend
pyinstaller sf_bulk_loader.spec --clean --noconfirm
# Output: backend/dist/sf_bulk_loader/  (folder with executable + shared libs)
```

When running the desktop app from source, Electron looks for backend tools in the backend virtualenv:
- macOS/Linux: `backend/.venv/bin/uvicorn`, `backend/.venv/bin/alembic`
- Windows: `backend/.venv/Scripts/uvicorn.exe`, `backend/.venv/Scripts/alembic.exe`

### Test the binary

```bash
# Verify --migrate works (creates the full schema in a temp DB)
DATABASE_URL="sqlite+aiosqlite:////tmp/test.db" \
ENCRYPTION_KEY_FILE=/tmp/enc.key \
JWT_SECRET_KEY_FILE=/tmp/jwt.key \
APP_DISTRIBUTION=desktop \
./dist/sf_bulk_loader/sf_bulk_loader --migrate

# Verify the server starts
DATABASE_URL="sqlite+aiosqlite:////tmp/test.db" \
ENCRYPTION_KEY_FILE=/tmp/enc.key \
JWT_SECRET_KEY_FILE=/tmp/jwt.key \
APP_DISTRIBUTION=desktop \
./dist/sf_bulk_loader/sf_bulk_loader &
curl http://127.0.0.1:8000/api/health
```

### Notes

- `backend/dist/sf_bulk_loader/` is gitignored — it is rebuilt by CI on every release
- The binary is self-contained: no Python required on the target machine
- `asyncpg` is intentionally excluded (SQLite-only on desktop; not imported in the app tree)
- `boto3` is bundled (~50 MB) because it is imported at module level in `input_connections.py`
- Adding a new migration: add the version file to `backend/alembic/versions/` — the `alembic/`
  directory is bundled into the binary's `_MEIPASS` at build time, so no spec changes needed

---

## Observability

For the full developer reference — event taxonomy, metrics, spans, how to extend each
layer, and the Definition of Done checklist for future feature work — see
[`docs/observability.md`](observability.md).

### Structured logging

Set `LOG_FORMAT=json` in `.env` to enable structured JSON logging (one JSON object per line on stdout). This is the default for deployed environments. Local development uses plain text by default.

Set `LOG_LEVEL=DEBUG` to see detailed request and workflow logs.

### Health endpoints

Three health endpoints are available:

- `GET /api/health/live` — liveness probe (no dependency checks, always fast)
- `GET /api/health/ready` — readiness probe (checks database connectivity; returns 503 if unavailable)
- `GET /api/health/dependencies` — operator view of per-dependency health

Docker Compose uses `/api/health/ready` for its health check. The legacy `/api/health` endpoint is preserved for backward compatibility.

### Optional tracing

OpenTelemetry-compatible tracing can be enabled via `.env`:

```env
TRACING_ENABLED=true
TRACE_SAMPLE_RATIO=1.0            # 0.0 to 1.0; 1.0 = sample all
OTLP_ENDPOINT=http://localhost:4317  # optional; omit to create spans without export
```

When enabled, framework auto-instrumentation is active for FastAPI and httpx. Custom workflow spans are created for run, step, and partition/job execution boundaries.

### Optional error monitoring

Sentry-compatible error monitoring can be enabled via `.env`:

```env
ERROR_MONITORING_ENABLED=true
ERROR_MONITORING_DSN=https://<key>@<org>.ingest.sentry.io/<project>
```

Sensitive data is scrubbed before events are transmitted (authorization headers, private keys, passwords, tokens). Correlation context (run_id, step_id, request_id) is attached to captured exceptions automatically.

### Sensitive telemetry handling (SFBL-60)

All observability channels — logs, traces, metrics, and error monitoring — must comply with the telemetry hygiene rules defined in `backend/app/observability/sanitization.py`.

**Prohibited content** — must never appear in any telemetry signal:

- Salesforce access tokens and OAuth assertion JWTs
- RSA private keys (PEM) or Fernet encryption keys
- Passwords, secrets, or API keys
- `Authorization` request headers or any header matching `SCRUBBED_KEYS`
- Raw CSV row data (input or output)

**Allowed content** — safe to include in telemetry:

- Stable entity IDs: `run_id`, `step_id`, `job_record_id`, `sf_job_id`, `load_plan_id`, `input_connection_id`, `request_id`
- Salesforce object names and operation types
- HTTP status codes, method names, and URL paths (not query strings)
- Record counts and byte sizes
- Outcome codes from `app.observability.events.OutcomeCode`
- Exception types and sanitized exception messages

**Shared helpers** available from `app.observability.sanitization`:

| Helper | Purpose |
|---|---|
| `SCRUBBED_KEYS` | Frozenset of lower-cased key names that must be redacted |
| `scrub_dict(d)` | Return a copy of `d` with sensitive keys replaced by `[REDACTED]` |
| `scrub_headers(h)` | Same as `scrub_dict` but typed for HTTP header dicts |
| `safe_exc_message(exc)` | Sanitize an exception message — strips JWT and Bearer token patterns |
| `safe_record_exception(span, exc)` | Record an exception on an OTel span without leaking token data |

When adding new integration paths, error handlers, or exception types, use these helpers rather than logging raw response bodies or exception strings that may include auth material.

---

## Running Tests

### Backend

```bash
cd backend
pytest          # all tests
pytest -v       # verbose output
pytest tests/test_csv_processor.py   # single file
pytest -k test_create_plan           # by name pattern
```

Tests use a file-based SQLite database (`backend/test_api.db`, cleaned up after the
run). No Salesforce connection is required.

**Against PostgreSQL:**

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_db \
  pytest -x -q
```

### Frontend

```bash
cd frontend
npm test            # watch mode
npm run test:run    # single run (CI)
npm run typecheck   # TypeScript type check
```

---

## End-to-end testing

The E2E suite is Playwright-based and lives under `tests/e2e/`. It is separate from
the backend (`pytest`) and frontend (Vitest) unit-test suites. See
[`docs/specs/e2e-testing-spec.md`](specs/e2e-testing-spec.md) for the full
architecture and rationale.

### Tier model

| Tier | What it covers | Salesforce requirement | When it runs |
|------|---------------|----------------------|--------------|
| **1a** | Org-free flows: file pane, plan list, RBAC nav, settings, login | None | Every PR |
| **1b** | Metadata-reading flows: mapping panel, step editor object combobox, SOQL validator, bulk-query builder | Captured describe fixtures (no live org) | Every PR |
| **2** | State-mutating flows: actual loads, query result writes, retry semantics | Real scratch org | Nightly + release-tag gate + same-repo opt-in |

### Local setup

#### Install Playwright

```bash
cd tests/e2e
npm install
npx playwright install chromium
```

#### Dev Hub (required for Tier 2 only)

Tier 2 specs spin up real Salesforce scratch orgs. You need a Dev Hub org:

```bash
sf org login web    # opens browser — log in with your Dev Hub credentials
```

The session is saved to `~/.sfdx/` and persists across terminal sessions. For
v1, a single Dev Hub is assumed (the project's own). External contributors
who need to run Tier 2 locally would require their own Dev Hub; that path is
deferred — documented here so future contributors know the constraint.

#### Tier 2 secrets (local)

The bulk loader authenticates to the scratch org via JWT. Two environment
variables are needed:

- `SFBL_E2E_BULK_LOADER_JWT_KEY` — the private key for the test-only
  Connected App / ECA deployed into each scratch org. Ask ops for the value;
  **do not commit it**. Store it in `~/.sfbl-e2e/` or export it from your
  shell profile.
- `E2E_SCRATCH_ORG` — the alias of an already-created scratch org to target.
  In CI this is generated per workflow run; locally you can set it to any alias
  in your `~/.sfdx/` that points to a valid scratch org.

### Running specs locally

All commands run from `tests/e2e/`:

```bash
npm run e2e:1a   # Tier 1a — org-free (requires app stack running)
npm run e2e:1b   # Tier 1b — fixture-backed (requires app stack in fixture mode)
npm run e2e:2    # Tier 2 — scratch org (requires E2E_SCRATCH_ORG + JWT key)
```

#### Tier 1a

Start the app in no-auth desktop mode, then run the suite:

```bash
# From repo root
APP_DISTRIBUTION=desktop docker compose up -d

# From tests/e2e
npm run e2e:1a
```

#### Tier 1b — fast feedback loop

Tier 1b requires the backend to boot in **fixture mode** (env var
`SF_DESCRIBE_FIXTURES_DIR` set at startup). The helper script mirrors the
exact CI topology — same compose files, same env vars, same fixture mounts —
so a green local run is a very strong signal that CI will also pass:

```bash
# From repo root
./tests/e2e/scripts/run-tier-1b-local.sh               # headless (matches CI)
./tests/e2e/scripts/run-tier-1b-local.sh --headed      # see the browser
./tests/e2e/scripts/run-tier-1b-local.sh --ui          # Playwright UI mode
./tests/e2e/scripts/run-tier-1b-local.sh --no-rebuild  # skip image rebuild
./tests/e2e/scripts/run-tier-1b-local.sh --keep-up     # leave stack running
```

The script backs up any existing `.env` at the repo root before writing the
fixture-mode one, and restores it on exit.

**Mode-switching gotcha.** The backend reads `SF_DESCRIBE_FIXTURES_DIR` once
at startup; it cannot switch between fixture mode (Tier 1b) and live mode
(Tier 2) at runtime. If you need to switch, restart the backend. You can
confirm the active mode from the health endpoint:

```bash
curl -s http://localhost/api/health | jq .describe_mode
# "fixture" when SF_DESCRIBE_FIXTURES_DIR is set, "live" otherwise
```

#### Tier 2 — manual ad-hoc run

You can fire Tier 2 from the GitHub Actions UI without pushing a tag or
labelling a PR. Go to **Actions → e2e-tier-2 → Run workflow** (added in
commit `d73bd46`). Two optional inputs:

- `scratch_org_alias` — override the workflow-generated unique alias (useful
  when validating against a long-lived org shape).
- `skip_destroy` — leave the scratch org running after the job completes for
  post-mortem inspection. **Remember to delete it manually** — scratch orgs
  count against your Dev Hub allocation.

### Authoring conventions

- **Page-object pattern.** Each page or modal has a corresponding page-object
  class in `tests/e2e/app/playwright/helpers/`. Specs import the page object
  and call its methods rather than calling Playwright locators directly.
- **Locator preference.** Use `getByRole()` and `getByLabel()` over
  `data-testid` attributes. Role/label selectors are closer to the ARIA tree,
  survive markup refactors, and are easier to read.
- **One spec per user-visible flow.** A spec file covers one coherent user
  journey (e.g. "create a load plan from the plans page"). Split flows that
  grow beyond ~150 lines into multiple spec files.
- **No conditional skips across tiers.** A spec belongs in exactly one tier
  and is never conditionally skipped based on a runtime env var. Tiers are
  enforced at the Playwright project level (`playwright.config.ts`).
- **Test data privacy.** Never import real-world CSVs into specs or commit
  them to the repo. All generated data uses [Faker](https://faker.readthedocs.io/)
  (`faker` Python library for CSV generators, `@faker-js/faker` for any TS
  generators). This keeps the suite GDPR-safe and removes any risk of
  production PII leaking through CI logs.

### Org Shape opt-in (Tier 2)

By default, scratch orgs are spun up as vanilla Developer Edition orgs. To
reproduce a production-shaped issue, you can nominate a source org and have
the scratch org mirror its configuration via Salesforce
[Org Shape](https://developer.salesforce.com/docs/atlas.en-us.sfdx_dev.meta/sfdx_dev/sfdx_dev_shape.htm).

**One-time operator setup (async — allow ~30 minutes):**

```bash
# Run this once per source org you want to use as a shape.
# Use the SOURCE ORG ALIAS (not its ID) with --target-org.
sf org create shape --target-org <source-org-alias>
```

After `sf org create shape` completes, the shape is associated with the
source org's ID and can be queried:

```bash
sf org list shape --json
# Filter for: sourceOrg == <your-org-id> AND status == "Active"
```

**Nominating the source org for a Tier 2 run:**

Set `E2E_ORG_SHAPE` to the source org's 15- or 18-character Salesforce org
ID (not its alias). The scratch-create helper normalises both lengths to 15
chars before writing the `sourceOrg` field into the scratch-def JSON:

```bash
export E2E_ORG_SHAPE=00D5g000000abcd   # 15-char ID
# or
export E2E_ORG_SHAPE=00D5g000000abcdEAA  # 18-char — normalised to 15 automatically
```

The helper checks that an active shape exists for the nominated org before
attempting scratch creation. If none is found it exits with a clear error
pointing back to the `sf org create shape` step above.

Leave `E2E_ORG_SHAPE` unset (or set it to `default`) to use the plain
Developer Edition scratch-def. CI never uses Org Shape by default — it is an
opt-in for local repro and manual `workflow_dispatch` runs.

### `sf/` vs `app/` boundary (D13)

The `tests/e2e/` subtree is split into two subtrees with a one-way import
direction:

- **`sf/`** — Salesforce-shaped, application-blind. Helpers, fixtures, and
  scripts that could be shared with any Salesforce-connected web app. Must
  not know anything about the bulk loader's own API or domain.
- **`app/`** — Bulk-loader-specific. Specs, page objects, and fixtures that
  call the bulk loader's API or navigate its UI. May import freely from
  `sf/`, but `sf/` must never import from `app/`.

This boundary is enforced by ESLint (`eslint-plugin-boundaries`). Any
cross-boundary import from `sf/` into `app/` is a lint error:

```bash
cd tests/e2e
npm run lint          # full lint pass
npm run lint:boundaries  # boundaries rule only
```

The guard exists so the Salesforce-shaped layer can be reused across future
projects without pulling in bulk-loader-specific concerns. Keep new shared
helpers in `sf/`; keep bulk-loader specifics in `app/`.
