# Salesforce Bulk Loader — Technical Specification

## 1. Overview

### 1.1 Purpose

Build a containerized application that orchestrates large-scale data loads into Salesforce using the Bulk API 2.0. The application manages sequenced, multi-object loads (e.g., Account → Individual → Contact → ContactPointEmail), handles file partitioning, tracks job progress, and captures success/error logs — all through a web-based GUI.

### 1.2 Problem Statement

Organizations loading large datasets into Salesforce face several challenges:

- Loads must follow a strict object dependency order (parent objects before children).
- Salesforce imposes file size and record count limits on Bulk API jobs, requiring CSV files to be split into partitions.
- Tracking the status of dozens of concurrent or sequential jobs across multiple objects is error-prone when done manually.
- Correlating success/error results back to source records (especially for ID mapping between parent and child loads) requires careful orchestration.

### 1.3 Key Terms

- **Load Plan**: A user-defined configuration specifying which objects to load, in what order, with what CSV files, and what operation (insert/update/upsert/delete).
- **Load Step**: A single object-level unit within a Load Plan (e.g., "Insert Accounts").
- **Partition**: A chunk of a CSV file that fits within Salesforce Bulk API limits. A single Load Step may produce multiple partitions.
- **Job**: A single Salesforce Bulk API 2.0 job corresponding to one partition.
- **Load Run**: A single execution of a Load Plan. A Load Plan can be run multiple times.

---

## 2. Architecture

### 2.1 Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Backend | Python 3.12+ (FastAPI) | Async support for polling Salesforce; rich ecosystem for CSV processing |
| Database | SQLite (via SQLAlchemy + Alembic) | Zero-config, single-file DB, sufficient for single-user/team use. Easy to swap to Postgres later via SQLAlchemy abstraction |
| Frontend | React (Vite + TypeScript) | Component-based UI for status dashboards, tables, and real-time updates |
| Containerization | Docker + Docker Compose | Backend, frontend (nginx), and optional Postgres in separate containers |
| Task Processing | Background async tasks (asyncio) | Lightweight; no need for Celery unless horizontal scaling is required |

### 2.2 High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Docker Compose                         │
│                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐ │
│  │   Frontend    │   │   Backend    │   │  SQLite DB   │ │
│  │  React/Vite   │──▶│   FastAPI    │──▶│  (volume)    │ │
│  │   (nginx)     │   │              │   │              │ │
│  └──────────────┘   └──────┬───────┘   └──────────────┘ │
│                            │                             │
│                     ┌──────┴───────┐                     │
│                     │  Local CSV   │                     │
│                     │   Volume     │                     │
│                     │  /data/input │                     │
│                     └──────────────┘                     │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
                   Salesforce Bulk API 2.0
```

### 2.3 Directory Structure

```
salesforce-bulk-loader/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/                    # DB migrations
│   ├── app/
│   │   ├── main.py                 # FastAPI app entrypoint
│   │   ├── config.py               # Settings (env vars, defaults)
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   │   ├── load_plan.py
│   │   │   ├── load_step.py
│   │   │   ├── load_run.py
│   │   │   ├── job.py
│   │   │   └── connection.py
│   │   ├── schemas/                # Pydantic request/response schemas
│   │   ├── api/                    # FastAPI route modules
│   │   │   ├── connections.py
│   │   │   ├── load_plans.py
│   │   │   ├── load_runs.py
│   │   │   └── jobs.py
│   │   ├── services/               # Business logic
│   │   │   ├── salesforce_auth.py
│   │   │   ├── salesforce_bulk.py  # Bulk API 2.0 client
│   │   │   ├── csv_processor.py    # Splitting, validation
│   │   │   └── orchestrator.py     # Sequencing and execution engine
│   │   └── utils/
│   │       ├── logging.py
│   │       └── file_helpers.py
│   └── tests/
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── ConnectionManager.tsx
│   │   │   ├── LoadPlanEditor.tsx
│   │   │   ├── LoadRunView.tsx
│   │   │   └── JobDetail.tsx
│   │   ├── components/
│   │   │   ├── StepStatusCard.tsx
│   │   │   ├── ProgressBar.tsx
│   │   │   ├── LogViewer.tsx
│   │   │   └── CSVPreview.tsx
│   │   └── api/                    # API client (axios/fetch wrappers)
│   └── nginx.conf
└── data/
    ├── input/                      # Mount point for source CSVs
    ├── output/                     # Success/error logs
    └── db/                         # SQLite file (persistent volume)
```

---

## 3. Data Model

### 3.1 Entity Relationship

```
Connection 1──* LoadPlan 1──* LoadStep
                LoadPlan 1──* LoadRun 1──* JobRecord
                LoadStep 1──* JobRecord
```

### 3.2 Tables

#### `connection`

Stores Salesforce org credentials. Sensitive fields encrypted at rest.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | |
| name | VARCHAR(255) | Friendly name (e.g., "Production", "QA Sandbox") |
| instance_url | VARCHAR(512) | e.g., `https://myorg.my.salesforce.com` |
| login_url | VARCHAR(512) | e.g., `https://login.salesforce.com` or `https://test.salesforce.com` |
| client_id | TEXT | Connected App consumer key |
| private_key | TEXT (encrypted) | PEM-encoded RSA private key for JWT signing |
| username | VARCHAR(255) | SF username |
| access_token | TEXT (encrypted) | Current token (re-requested automatically) |
| token_expiry | TIMESTAMP | |
| is_sandbox | BOOLEAN | |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

#### `load_plan`

Defines a reusable load configuration.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | |
| connection_id | UUID (FK) | Target Salesforce org |
| name | VARCHAR(255) | e.g., "Q1 Data Migration" |
| description | TEXT | |
| abort_on_step_failure | BOOLEAN | Default true. If a step fails, halt subsequent steps |
| error_threshold_pct | FLOAT | Abort a step if error rate exceeds this (default 10%) |
| max_parallel_jobs | INT | Max concurrent Bulk API jobs per step (default 5) |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

#### `load_step`

A single object within a load plan, ordered by `sequence`.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | |
| load_plan_id | UUID (FK) | |
| sequence | INT | Execution order (1, 2, 3...) |
| object_name | VARCHAR(255) | Salesforce object API name (e.g., `Account`) |
| operation | ENUM | `insert`, `update`, `upsert`, `delete` |
| external_id_field | VARCHAR(255) | Required for upsert. Used to relate child records to parents via external IDs |
| csv_file_pattern | VARCHAR(512) | Glob pattern for source files (e.g., `accounts_*.csv`) |
| partition_size | INT | Max records per partition (default 10,000) |
| assignment_rule_id | VARCHAR(18) | Optional Salesforce assignment rule ID |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

#### `load_run`

A single execution of a load plan.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | |
| load_plan_id | UUID (FK) | |
| status | ENUM | `pending`, `running`, `completed`, `completed_with_errors`, `failed`, `aborted` |
| started_at | TIMESTAMP | |
| completed_at | TIMESTAMP | |
| total_records | INT | Across all steps |
| total_success | INT | |
| total_errors | INT | |
| initiated_by | VARCHAR(255) | Username or "scheduler" |
| error_summary | TEXT | JSON summary of errors by step |

#### `job_record`

Maps to a single Salesforce Bulk API 2.0 job.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | Internal ID |
| load_run_id | UUID (FK) | |
| load_step_id | UUID (FK) | |
| sf_job_id | VARCHAR(18) | Salesforce Bulk API job ID |
| partition_index | INT | Which partition of the step this job represents |
| status | ENUM | `pending`, `uploading`, `upload_complete`, `in_progress`, `job_complete`, `failed`, `aborted` |
| records_processed | INT | |
| records_failed | INT | |
| success_file_path | VARCHAR(512) | Local path to downloaded success CSV |
| error_file_path | VARCHAR(512) | Local path to downloaded error CSV |
| unprocessed_file_path | VARCHAR(512) | Local path to unprocessed records CSV |
| sf_api_response | TEXT | JSON of last API response |
| started_at | TIMESTAMP | |
| completed_at | TIMESTAMP | |
| error_message | TEXT | Top-level error if job failed |

---

## 4. Core Services

### 4.1 Salesforce Authentication (`salesforce_auth.py`)

MVP uses **OAuth 2.0 JWT Bearer** flow only (server-to-server, no interactive login required):

1. Build a JWT with claims: `iss` (client_id), `sub` (username), `aud` (login URL), `exp` (expiry).
2. Sign the JWT with the connected app's private key (RS256).
3. POST to `/services/oauth2/token` with `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`.
4. Receive `access_token` and `instance_url` in the response.

The service must handle token refresh transparently. All API calls should check token expiry and re-request before proceeding. Store the private key encrypted using Fernet symmetric encryption with a key derived from an environment variable (`ENCRYPTION_KEY`).

**Future consideration**: Add OAuth 2.0 Web Server Flow and Username/Password auth as alternative connection types.

### 4.2 Salesforce Bulk API 2.0 Client (`salesforce_bulk.py`)

Implement the full Bulk API 2.0 lifecycle:

```
Create Job → Upload CSV Data → Close/Upload Complete → Poll Status → Download Results
```

Key methods:

- `create_job(object_name, operation, external_id_field=None, assignment_rule_id=None) → sf_job_id`: POST to `/services/data/vXX.0/jobs/ingest`
- `upload_csv(sf_job_id, csv_content: bytes)`: PUT CSV data to the job's content URL. Set `Content-Type: text/csv`.
- `close_job(sf_job_id)`: PATCH job state to `UploadComplete`.
- `poll_job(sf_job_id) → status`: GET job status. Return normalized status. Poll interval: start at 5s, backoff to 30s max. Respect `Retry-After` headers.
- `get_success_results(sf_job_id) → csv_content`: GET successful results CSV.
- `get_failed_results(sf_job_id) → csv_content`: GET failed results CSV.
- `get_unprocessed_results(sf_job_id) → csv_content`: GET unprocessed records CSV.
- `abort_job(sf_job_id)`: PATCH job state to `Aborted`.

Use `httpx.AsyncClient` for all HTTP calls with connection pooling and retry logic (3 retries with exponential backoff for 5xx and 429 responses).

API version: Use a configurable default (e.g., `v62.0`), stored in config.

### 4.3 CSV Processor (`csv_processor.py`)

Responsibilities:

- **File discovery**: Given a glob pattern and input directory, find matching CSV files.
- **Validation**: Check that CSV headers match expected Salesforce field names (optional — can warn but not block).
- **Partitioning**: Split a CSV into chunks of N records (default 10,000, configurable per step). Preserve headers in each partition. Handle large files using streaming reads (don't load entire file into memory).
- **Encoding**: Ensure output is UTF-8. Detect and convert common encodings (latin-1, cp1252).


Note: The MVP relies on external IDs (upsert operations) to establish relationships between objects rather than runtime ID mapping. This means child CSVs should reference parent records via external ID fields (e.g., `Account.ExternalId__c` rather than `AccountId`). This is simpler, requires no inter-step coordination, and is the Salesforce-recommended approach for data migrations.

### 4.4 Orchestrator (`orchestrator.py`)

The core execution engine for a Load Run:

```
for each step (ordered by sequence):
    1. Resolve CSV files (glob pattern match)
    2. Partition CSV files
    3. For each partition:
       a. Create Bulk API job
       b. Upload CSV data
       c. Close job (trigger processing)
    4. Poll all jobs for this step (parallel polling)
    5. When all jobs complete:
       a. Download success/error/unprocessed results
       b. Save to output directory
       c. Update DB with counts
    6. Evaluate step success:
       a. If error rate > threshold → mark step failed
       b. If step failed AND abort_on_step_failure → abort run
    7. Proceed to next step
```

Concurrency control: Use `asyncio.Semaphore` to limit parallel jobs per step (from `max_parallel_jobs`). This prevents hitting Salesforce's concurrent Bulk API job limits (which vary by org edition but are typically 5-15).

The orchestrator must be resilient to transient failures. If a poll request fails, retry. If a job is stuck in `InProgress` for longer than a configurable timeout (default 30 minutes), log a warning but continue polling.

---

## 5. API Endpoints

### 5.1 Connections

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/connections` | List all connections |
| POST | `/api/connections` | Create a new connection |
| GET | `/api/connections/{id}` | Get connection details (secrets redacted) |
| PUT | `/api/connections/{id}` | Update connection |
| DELETE | `/api/connections/{id}` | Delete connection |
| POST | `/api/connections/{id}/test` | Test connectivity (attempt auth + describe a standard object) |

### 5.2 Load Plans

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/load-plans` | List all load plans |
| POST | `/api/load-plans` | Create a new load plan |
| GET | `/api/load-plans/{id}` | Get load plan with steps |
| PUT | `/api/load-plans/{id}` | Update load plan |
| DELETE | `/api/load-plans/{id}` | Delete load plan and associated steps |

### 5.3 Load Steps

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/load-plans/{plan_id}/steps` | Add a step to a plan |
| PUT | `/api/load-plans/{plan_id}/steps/{step_id}` | Update a step |
| DELETE | `/api/load-plans/{plan_id}/steps/{step_id}` | Remove a step |
| POST | `/api/load-plans/{plan_id}/steps/reorder` | Reorder steps (accepts array of step IDs) |
| POST | `/api/load-plans/{plan_id}/steps/{step_id}/preview` | Preview CSV file discovery and row counts |

### 5.4 Load Runs

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/load-plans/{plan_id}/run` | Start a new load run |
| GET | `/api/runs` | List all runs (with filtering by plan, status, date range) |
| GET | `/api/runs/{id}` | Get run details with job breakdown |
| POST | `/api/runs/{id}/abort` | Abort a running load |
| GET | `/api/runs/{id}/summary` | Aggregated success/error counts per step |

### 5.5 Jobs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/runs/{run_id}/jobs` | List jobs for a run (filterable by step, status) |
| GET | `/api/jobs/{id}` | Get job detail including SF response |
| GET | `/api/jobs/{id}/success-csv` | Download success results CSV |
| GET | `/api/jobs/{id}/error-csv` | Download error results CSV |
| GET | `/api/jobs/{id}/unprocessed-csv` | Download unprocessed records CSV |

### 5.6 Utility

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/files/input` | List CSV files in the input directory |
| GET | `/api/files/input/{filename}/preview` | Preview first N rows of a CSV |
| GET | `/api/health` | Health check (DB connectivity, Salesforce auth status) |
| WebSocket | `/ws/runs/{run_id}` | Real-time status updates for a running load |

---

## 6. Frontend

### 6.1 Pages

#### Dashboard (`/`)
- Summary cards: active runs, recent completions, error rates.
- Table of recent Load Runs with status badges, progress bars, and quick links.
- Connection health indicators.

#### Connection Manager (`/connections`)
- CRUD form for Salesforce connections.
- "Test Connection" button with inline result display.
- Fields: name, login URL (login.salesforce.com or test.salesforce.com), client ID, username, private key (PEM upload or paste), sandbox toggle.

#### Load Plan Editor (`/plans/{id}`)
- Plan metadata form (name, description, connection, settings).
- Step list with drag-and-drop reordering.
- Per-step form: object name (autocomplete from Salesforce describe if connection is active), operation selector, external ID field (for upsert), CSV pattern, partition size.
- "Preview" button per step: shows matched files and record counts.
- Visual step sequence showing execution order.

#### Load Run View (`/runs/{id}`)
- Real-time progress via WebSocket.
- Step-by-step accordion/timeline view showing: step name and object, progress bar (records processed / total), status badge, expandable job list underneath each step.
- Summary stats: total records, successes, errors, duration.
- "Abort" button (with confirmation dialog).

#### Job Detail (`/runs/{run_id}/jobs/{job_id}`)
- Full job metadata (SF job ID, partition info, timestamps).
- Inline error log viewer with search/filter.
- Download buttons for success, error, and unprocessed CSVs.
- Raw Salesforce API response viewer (collapsible JSON).

### 6.2 Real-Time Updates

Use WebSocket connection to stream run progress. Backend publishes events:

```json
{
  "event": "job_status_change",
  "run_id": "...",
  "step_id": "...",
  "job_id": "...",
  "status": "job_complete",
  "records_processed": 10000,
  "records_failed": 12
}
```

Event types: `run_started`, `step_started`, `job_status_change`, `step_completed`, `run_completed`, `run_failed`, `run_aborted`.

Frontend should update the UI incrementally from these events without polling or full-page refreshes.

---

## 7. Configuration & Environment

### 7.1 Environment Variables

```env
# Application
APP_ENV=production
LOG_LEVEL=INFO
ENCRYPTION_KEY=<32-byte-base64-encoded-key>

# Database
DATABASE_URL=sqlite:///data/db/bulk_loader.db

# Salesforce defaults
SF_API_VERSION=v62.0
SF_POLL_INTERVAL_INITIAL=5
SF_POLL_INTERVAL_MAX=30
SF_JOB_TIMEOUT_MINUTES=30

# Partitioning defaults
DEFAULT_PARTITION_SIZE=10000
MAX_PARTITION_SIZE=100000000

# Paths
INPUT_DIR=/data/input
OUTPUT_DIR=/data/output
```

### 7.2 Docker Compose

```yaml
version: "3.8"

services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    volumes:
      - ./data/input:/data/input:ro
      - ./data/output:/data/output
      - ./data/db:/data/db
    env_file: .env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  frontend:
    build: ./frontend
    ports:
      - "3000:80"
    depends_on:
      - backend
```

---

## 8. Object Relationships via External IDs

The MVP uses Salesforce's external ID relationship resolution rather than runtime ID mapping. This means:

- Parent objects should define an external ID field (e.g., `ExternalId__c`).
- Child CSVs reference parents using relationship notation in the column header: `Account.ExternalId__c` rather than `AccountId`.
- Salesforce resolves these references server-side during the upsert.
- Steps still execute in sequence (parents before children) to ensure parent records exist when children are processed.

This approach eliminates the need for a client-side ID mapper. A future version could add runtime ID mapping for insert-only workflows where external IDs are not available.

**Example CSV headers for a Contact upsert referencing Account by external ID:**

```csv
FirstName,LastName,Email,Account.ExternalId__c
Jane,Doe,jane@example.com,ACCT-001
```

---

## 9. Error Handling & Resilience

### 9.1 Retry Strategy

| Scenario | Strategy |
|----------|----------|
| Salesforce API 5xx | 3 retries, exponential backoff (1s, 2s, 4s) |
| Salesforce API 429 (rate limit) | Respect `Retry-After` header, retry after wait |
| Network timeout | 3 retries with 30s timeout per request |
| Job stuck in `InProgress` | Continue polling up to `SF_JOB_TIMEOUT_MINUTES`, then log warning |
| Authentication failure (401) | Attempt token refresh once, then fail |

### 9.2 Error Threshold

Each Load Step has an `error_threshold_pct` (inherited from the plan if not overridden). After all jobs in a step complete, calculate:

```
error_rate = total_errors / total_records
if error_rate > error_threshold_pct / 100:
    mark step as FAILED
```

### 9.3 Abort Behavior

When a run is aborted (user-initiated or threshold-triggered):

1. Set run status to `aborted`.
2. For any in-progress Salesforce jobs, call `abort_job()`.
3. Mark all pending jobs as `aborted` in the DB (don't submit them).
4. Download any available results from completed/aborted jobs.
5. Write a summary log.

### 9.4 Logging

Use structured logging (JSON format) with `structlog`. Key fields on every log entry: `run_id`, `step_id`, `job_id`, `sf_job_id`, `object_name`, `operation`. Log levels:

- **INFO**: Job created, job completed, step completed, run completed.
- **WARNING**: Records failed (below threshold), poll retry, unmappable records.
- **ERROR**: Step failed, run failed, API errors, auth failures.

---

## 10. Salesforce Bulk API 2.0 — Key Constraints

These constraints should be documented in the application and enforced where possible:

- **Max 150 MB per CSV upload** (compressed). Partition accordingly.
- **Max 150,000,000 records per 24-hour rolling period** (varies by org).
- **Max concurrent Bulk API jobs**: varies by edition (typically 5-15 for ingest). The app's `max_parallel_jobs` setting should stay within this.
- **Job timeout**: Salesforce may abort jobs that take too long (typically 10 minutes of inactivity or 100+ minutes total). The app should detect `Failed` status and check the error message.
- **Column order**: The CSV header in the data upload must match the fields the job expects. Salesforce reads headers from the first row.
- **Line endings**: Salesforce expects `\n` (LF). The CSV processor should normalize line endings.
- **Null values**: Use `#N/A` in CSV cells to null out a field value.

---

## 11. Security Considerations

- **Credential storage**: All Salesforce credentials (client secrets, private keys, tokens) must be encrypted at rest using Fernet encryption. The encryption key is provided via environment variable and never committed to version control.
- **API tokens**: Access tokens are short-lived and refreshed automatically. Refresh tokens are encrypted.
- **Input validation**: All API inputs validated via Pydantic schemas. CSV filenames are sanitized to prevent path traversal.
- **Docker**: Run backend as non-root user. Input volume mounted read-only. No host network mode.
- **CORS**: Frontend origin explicitly whitelisted. No wildcard origins in production.
- **No secrets in logs**: Ensure structured logging redacts tokens and credentials.

---

## 12. Testing Strategy

### 12.1 Unit Tests

- CSV processor: partitioning edge cases (empty files, single record, exact boundary, unicode).
- Orchestrator: sequencing logic, abort behavior, threshold calculations.
- Auth service: JWT construction, token expiry handling.

### 12.2 Integration Tests

- Bulk API client against a Salesforce Developer Edition or sandbox using `pytest` fixtures.
- Database operations via SQLAlchemy against an in-memory SQLite instance.
- API endpoint tests using FastAPI's `TestClient`.

### 12.3 End-to-End Tests

- Full load plan execution against a Salesforce sandbox.
- Test with known good data (expect 100% success) and known bad data (expect specific errors).
- Test abort mid-run.
- Test multi-step sequential load using external ID relationships.

---

## 13. Future Considerations (Out of Scope for V1)

These are not required for the initial build but should be kept in mind to avoid architectural decisions that preclude them:

- **Runtime ID mapping**: For insert-only workflows where external IDs are unavailable, add a client-side ID mapper that reads parent success CSVs and injects Salesforce IDs into child CSVs before upload.
- **Additional auth flows**: Add OAuth 2.0 Web Server Flow (interactive login) and Username/Password + Security Token (legacy) as alternative connection types.
- **Scheduling**: Cron-based or interval-based automatic execution of load plans.
- **Postgres migration**: SQLAlchemy makes this a config change. Consider if multi-user access or larger datasets are needed.
- **Parallel steps**: Allow steps at the same sequence number to run in parallel (when no dependencies exist between them).
- **Pre/post hooks**: Run custom scripts or API calls before/after steps (e.g., disable triggers, run Apex batch jobs).
- **Field mapping UI**: Visual mapper between CSV columns and Salesforce fields.
- **Salesforce Describe integration**: Auto-populate object/field picklists from the target org's metadata.
- **Notifications**: Email or Slack alerts on run completion/failure.
- **Audit log**: Immutable log of who ran what, when, with what data.
- **Multi-org support**: Run the same plan against multiple connections (e.g., deploy data to production after testing in sandbox).
- **Data masking**: Optionally mask sensitive fields in logs and error reports.
- **Resume from failure**: Allow re-running a failed run from the failed step, rather than restarting from scratch.

---

## 14. Development Approach with Claude Code

### 14.1 Suggested Build Order

1. **Database models and migrations** — Get the schema right first. Use Alembic for migrations from day one.
2. **Salesforce auth service** — Implement JWT Bearer flow. This unblocks everything else.
3. **Bulk API client** — Implement the full job lifecycle. Test against a sandbox.
4. **CSV processor** — File discovery, validation, partitioning.
5. **Orchestrator** — Wire services together. Start with single-step execution, then add multi-step sequencing.
6. **API layer** — FastAPI endpoints wrapping the services.
7. **Frontend** — Start with the run monitoring view (most valuable for tracking), then build outward.
8. **Docker** — Containerize once the app runs locally.
9. **Error handling hardening** — Retry logic, timeouts, threshold enforcement.

### 14.2 Key Technical Decisions to Make Early

- **Async vs sync**: Use `async` throughout the backend. FastAPI natively supports it, and the polling-heavy workload benefits from non-blocking I/O.
- **SQLite concurrency**: SQLite handles concurrent reads well but serializes writes. Since this is typically a single-user tool, this is fine. Use WAL mode (`PRAGMA journal_mode=WAL`) for better concurrent read/write performance.
- **File paths**: Use relative paths in the DB (relative to `INPUT_DIR` / `OUTPUT_DIR`) so the app is portable across environments.
- **CSV library**: Use Python's built-in `csv` module for reliability. For large files, stream with `csv.reader` over `open()` — don't use `pandas` for the core path (too heavyweight for streaming).

### 14.3 Claude Code Prompting Tips

When working with Claude Code on this project:

- Reference this spec by section number (e.g., "Implement the Bulk API client per Section 4.2").
- Provide the Salesforce Bulk API 2.0 documentation URL for reference: `https://developer.salesforce.com/docs/atlas.en-us.api_asynch.meta/api_asynch/`
- When building the frontend, share wireframe descriptions or screenshots of the desired layout.
- For the orchestrator, start with a simplified version (single partition, no ID mapping) and iterate.
- Ask Claude Code to write tests alongside implementation, not as a separate pass.
